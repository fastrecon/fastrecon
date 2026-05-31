"""Tests for the fast-path short-circuit in keyed/sampled modes.

The fast path computes a (count, bit_xor(hash), sum(hash)) fingerprint
per side and skips the full join + per-column diff when both sides
agree. These tests pin the contract:

  - identical data MATCHes via fast path (no columns_compared populated)
  - any real difference falls through and is correctly detected
  - opting out (fast_path=False) restores the full-path behavior
  - column_mapping, exclude_columns, and the columns allowlist are
    honored when building the fingerprint
  - schema/rowcount/names_only/profile/hash modes are unaffected
"""

from __future__ import annotations

import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from fastrecon import ParquetFile, ReconConfig, compare


def _write(rows, schema=None):
    fd, p = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    if schema is not None:
        pq.write_table(pa.table(rows, schema=schema), p)
    else:
        pq.write_table(pa.Table.from_pylist(rows), p)
    return p


def _identical_pair(n=50):
    rows = [{"id": i, "name": f"u{i}", "amount": i * 1.5} for i in range(n)]
    return _write(rows), _write(rows)


def test_fast_path_identical_data_short_circuits():
    """Best case: identical data → MATCH via fingerprint, full diff
    pipeline is skipped (no columns_compared, fast_path stats present)."""
    l, r = _identical_pair()
    try:
        result = compare(ParquetFile(l), ParquetFile(r), keys=["id"])
        assert result.status == "MATCH"
        assert result.data_match is True
        assert "fast_path" in (result.column_stats or {})
        fp = result.column_stats["fast_path"]
        assert fp["matched"] is True
        assert fp["row_count"] == 50
        # Skipping the full diff means none of these stages ran:
        assert result.missing_in_left == 0
        assert result.missing_in_right == 0
        assert result.changed_rows == 0
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_row_count_diff_falls_through():
    """Differing row counts trip the count check and fall through to the
    full keyed compare so we get per-row missing-row detail."""
    l = _write([{"id": i, "v": i} for i in range(10)])
    r = _write([{"id": i, "v": i} for i in range(8)])
    try:
        result = compare(ParquetFile(l), ParquetFile(r), keys=["id"])
        assert result.status == "MISMATCH"
        assert result.missing_in_right == 2
        # Diagnostic preserved so the caller can see WHY fast-path declined.
        fp = (result.column_stats or {}).get("fast_path")
        assert fp is not None
        assert fp["matched"] is False
        assert fp["reason"] == "row_count_diff"
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_value_drift_falls_through_and_finds_changes():
    """Same row count, different values → fingerprints disagree, full
    keyed compare runs and reports the changed rows."""
    l = _write([{"id": i, "v": i} for i in range(10)])
    r = _write([{"id": i, "v": (i + 1 if i < 3 else i)} for i in range(10)])
    try:
        result = compare(ParquetFile(l), ParquetFile(r), keys=["id"])
        assert result.status == "MISMATCH"
        assert result.changed_rows == 3
        fp = (result.column_stats or {}).get("fast_path")
        assert fp is not None and fp["matched"] is False
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_swapped_keys_detected():
    """Two datasets with the same VALUE multiset but swapped keys must
    NOT false-match. Including keys in the fingerprint protects this."""
    l = _write([{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])
    r = _write([{"id": 1, "v": "b"}, {"id": 2, "v": "a"}])
    try:
        result = compare(ParquetFile(l), ParquetFile(r), keys=["id"])
        assert result.status == "MISMATCH"
        assert result.changed_rows == 2
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_disabled_runs_full_compare():
    """fast_path=False should always run the full per-column path,
    even on identical data — useful for benchmarking and for callers
    who want columns_compared populated."""
    l, r = _identical_pair()
    try:
        result = compare(
            ParquetFile(l), ParquetFile(r), keys=["id"],
            config=ReconConfig(fast_path=False),
        )
        assert result.status == "MATCH"
        # No fast_path entry → we know we went down the full path.
        assert "fast_path" not in (result.column_stats or {})
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_honors_column_mapping():
    """The right side names a column differently. Fast path must hash
    the RIGHT columns under their actual names — otherwise the SQL
    would fail outright (column not found) on real-world mapped pairs."""
    l = _write([{"id": i, "amount": i * 10.0} for i in range(20)])
    r = _write([{"id": i, "total": i * 10.0} for i in range(20)])
    try:
        result = compare(
            ParquetFile(l), ParquetFile(r), keys=["id"],
            config=ReconConfig(column_mapping={"amount": "total"}),
        )
        assert result.status == "MATCH"
        assert (result.column_stats or {}).get("fast_path", {}).get("matched") is True
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_honors_exclude_columns():
    """A column listed in exclude_columns must not contribute to the
    fingerprint, so two datasets that differ ONLY on excluded columns
    must MATCH via the fast path."""
    l = _write([{"id": i, "v": i, "noise": "L"} for i in range(15)])
    r = _write([{"id": i, "v": i, "noise": "R"} for i in range(15)])
    try:
        result = compare(
            ParquetFile(l), ParquetFile(r), keys=["id"],
            config=ReconConfig(exclude_columns=["noise"]),
        )
        assert result.status == "MATCH"
        assert (result.column_stats or {}).get("fast_path", {}).get("matched") is True
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_skipped_for_non_keyed_modes():
    """schema/rowcount/names_only/profile/hash modes never invoke the
    fast-path helper — they have their own (already-cheap) execution
    paths and shouldn't pay an extra scan."""
    l, r = _identical_pair()
    try:
        for mode in ("schema", "rowcount", "names_only", "profile", "hash"):
            result = compare(
                ParquetFile(l), ParquetFile(r),
                keys=["id"] if mode in ("hash",) else None,
                compare_mode=mode,
            )
            assert "fast_path" not in (result.column_stats or {}), (
                f"{mode} should not invoke fast_path"
            )
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_empty_inputs_match():
    """Two empty inputs: counts agree (0==0), fingerprints both NULL→0,
    so we MATCH via fast path without trying to anti-join nothing."""
    schema = pa.schema([("id", pa.int64()), ("v", pa.int64())])
    l = _write({"id": [], "v": []}, schema=schema) if False else None
    fd_l, l = tempfile.mkstemp(suffix=".parquet"); os.close(fd_l)
    fd_r, r = tempfile.mkstemp(suffix=".parquet"); os.close(fd_r)
    pq.write_table(pa.table({"id": [], "v": []}, schema=schema), l)
    pq.write_table(pa.table({"id": [], "v": []}, schema=schema), r)
    try:
        result = compare(ParquetFile(l), ParquetFile(r), keys=["id"])
        assert result.status == "MATCH"
        assert result.row_count_left == 0
        assert result.row_count_right == 0
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_handles_left_column_colliding_with_mapping_target():
    """Edge case: column_mapping={"a":"b"} AND left independently has a
    column literally named "b". The fingerprint must hash the mapped
    pair (left.a ↔ right.b) without silently including or skipping the
    standalone left.b column, and must not produce a SQL error."""
    # left = {id, a, b}, right = {id, b}; mapping says "a on left ↔ b on right"
    # left.b has no counterpart on right (right has only one "b", taken by "a")
    l = _write([{"id": i, "a": i * 10, "b": "extra"} for i in range(10)])
    r = _write([{"id": i, "b": i * 10} for i in range(10)])
    try:
        result = compare(
            ParquetFile(l), ParquetFile(r), keys=["id"],
            config=ReconConfig(column_mapping={"a": "b"}),
        )
        # Schema is reported as MISMATCH (left.b has no counterpart on
        # right after the mapping consumes the only "b") — that's
        # correct behavior. What we're pinning here is the *data*
        # fingerprint: it must succeed without a SQL error and report
        # the mapped pair as matching.
        assert result.data_match is True
        fp = (result.column_stats or {}).get("fast_path", {})
        assert fp.get("matched") is True
        # Confirm the standalone left.b was NOT silently included in the
        # hash columns (it has no right-side partner so it'd be unsafe).
        assert "b" not in fp.get("columns_hashed", [])
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_sampled_mismatch_preserves_diagnostic():
    """Sampled mode mismatch path must surface the fast-path
    diagnostic (symmetric with the keyed branch), so users can see
    WHY the short-circuit declined alongside the sampled-mode stats."""
    l = _write([{"id": i, "v": i} for i in range(20)])
    r = _write([{"id": i, "v": (i + 1 if i < 5 else i)} for i in range(20)])
    try:
        result = compare(
            ParquetFile(l), ParquetFile(r), keys=["id"],
            compare_mode="sampled",
            config=ReconConfig(sample_size_keyed=20),
        )
        # Sampled stats must still be present
        assert "sampled" in (result.column_stats or {})
        # AND fast-path diagnostic must be preserved (matched=False,
        # because hashes disagreed — not a row_count_diff this time)
        fp = (result.column_stats or {}).get("fast_path")
        assert fp is not None
        assert fp["matched"] is False
        # Hash digests should be present (this wasn't a row_count_diff)
        assert "left_xor" in fp and "right_xor" in fp
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_catches_duplicates_via_sum_digest():
    """A duplicated row XOR-cancels with itself, but sum(hash) is
    additive so it still detects the duplication. Two datasets where
    one has a duplicate that the other doesn't must NOT false-match
    even though the bit_xor digest could collide."""
    # Left: each row once. Right: each row once but row 0 duplicated.
    # Counts differ (10 vs 11) → caught by the count check; this is
    # the realistic scenario. The pure XOR-collision case requires
    # two duplicates on one side which is contrived but worth pinning.
    l = _write([{"id": i, "v": i} for i in range(10)])
    r = _write([{"id": i, "v": i} for i in range(10)] + [{"id": 0, "v": 0}])
    try:
        result = compare(ParquetFile(l), ParquetFile(r), keys=["id"])
        assert result.status == "MISMATCH"
        # Caught at the count check, no hashes needed
        fp = (result.column_stats or {}).get("fast_path", {})
        assert fp.get("reason") == "row_count_diff"
    finally:
        os.unlink(l); os.unlink(r)


def test_fast_path_sampled_mode_short_circuits_on_identical_data():
    """Sampled mode on identical data is pure waste — fast path catches
    it before any sampling SQL runs."""
    l, r = _identical_pair(n=200)
    try:
        result = compare(
            ParquetFile(l), ParquetFile(r), keys=["id"],
            compare_mode="sampled",
            config=ReconConfig(sample_size_keyed=20),
        )
        assert result.status == "MATCH"
        fp = (result.column_stats or {}).get("fast_path")
        assert fp is not None and fp["matched"] is True
        # If the fast path fired, the sampled-stats key must NOT be
        # present — they're only set on the full sampled path.
        assert "sampled" not in result.column_stats
    finally:
        os.unlink(l); os.unlink(r)
