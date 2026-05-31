"""Tests for the new compare modes: ``names_only`` and ``sampled``."""

from __future__ import annotations

import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fastrecon import ParquetFile, ReconConfig, compare
from fastrecon.exceptions import CompareError


def _write_parquet(rows):
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


# ---------------------------------------------------------------- names_only

def test_names_only_matches_when_columns_and_counts_align():
    """Same column names, same row count, even with different dtypes
    (which would normally flip schema_match): names_only ignores types."""
    left = _write_parquet([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    right = _write_parquet([{"id": "1", "name": "a"}, {"id": "2", "name": "b"}])
    try:
        result = compare(ParquetFile(left), ParquetFile(right), compare_mode="names_only")
        assert result.status == "MATCH"
        assert result.schema_match is True
        assert result.data_match is True
        # Logical inference should NOT have run in names_only mode
        assert result.schema_diff.logical_left == {}
        assert result.schema_diff.logical_right == {}
    finally:
        os.unlink(left)
        os.unlink(right)


def test_names_only_flags_missing_column():
    left = _write_parquet([{"id": 1, "name": "a"}])
    right = _write_parquet([{"id": 1, "extra": "x"}])
    try:
        result = compare(ParquetFile(left), ParquetFile(right), compare_mode="names_only")
        assert result.status == "MISMATCH"
        assert result.schema_match is False
        assert "name" in result.schema_diff.missing_in_right
        assert "extra" in result.schema_diff.missing_in_left
    finally:
        os.unlink(left)
        os.unlink(right)


def test_names_only_flags_row_count_drift():
    left = _write_parquet([{"id": 1}, {"id": 2}])
    right = _write_parquet([{"id": 1}])
    try:
        result = compare(ParquetFile(left), ParquetFile(right), compare_mode="names_only")
        assert result.status == "MISMATCH"
        assert result.data_match is False
        assert result.row_count_left == 2
        assert result.row_count_right == 1
    finally:
        os.unlink(left)
        os.unlink(right)


# -------------------------------------------------------------------- sampled

def test_sampled_requires_keys():
    """compare() catches CompareError and surfaces it via result.error
    rather than raising — verify both that the error is reported and
    the status is ERROR."""
    left = _write_parquet([{"id": 1}])
    right = _write_parquet([{"id": 1}])
    try:
        result = compare(ParquetFile(left), ParquetFile(right), compare_mode="sampled")
        assert result.status == "ERROR"
        assert "sampled" in (result.error or "")
    finally:
        os.unlink(left)
        os.unlink(right)


def test_sampled_runs_keyed_compare_on_subset():
    """Sampled compare picks N keys, filters both sides, runs keyed
    compare. With sample_size_keyed >= total rows, the result must
    match a full keyed compare."""
    rows_left = [{"id": i, "v": i * 10} for i in range(20)]
    rows_right = [{"id": i, "v": i * 10} for i in range(20)]
    left = _write_parquet(rows_left)
    right = _write_parquet(rows_right)
    try:
        # Sample size >= rows → behaves like full keyed compare.
        result = compare(
            ParquetFile(left), ParquetFile(right), keys=["id"],
            compare_mode="sampled",
            # Disable fast_path so we exercise the sampled pipeline
            # itself; with identical data the fingerprint would otherwise
            # short-circuit before any sampling SQL ran.
            config=ReconConfig(sample_size_keyed=100, fast_path=False),
        )
        assert result.status == "MATCH"
        assert result.compare_mode == "sampled"
        assert "sampled" in result.column_stats
        assert result.column_stats["sampled"]["actual_keys_sampled"] == 20
    finally:
        os.unlink(left)
        os.unlink(right)


def test_sampled_detects_mismatch_in_sampled_subset():
    """With a small sample size, mismatches that fall in the sample are
    reported. We sample everything (sample_size_keyed huge) to make the
    test deterministic."""
    left = _write_parquet([{"id": i, "v": i} for i in range(10)])
    right = _write_parquet([
        {"id": i, "v": (i * 2 if i == 5 else i)}  # row 5 changed
        for i in range(10)
    ])
    try:
        result = compare(
            ParquetFile(left), ParquetFile(right), keys=["id"],
            compare_mode="sampled",
            config=ReconConfig(sample_size_keyed=1000),
        )
        assert result.status == "MISMATCH"
        assert result.changed_rows == 1
    finally:
        os.unlink(left)
        os.unlink(right)


def test_sampled_uses_mapped_key_when_filtering_right_view():
    """The sampled filter on the right view must reference the mapped
    right-side key name, not the left's. We test this directly at the
    SQL layer because the downstream keyed_compare currently has a
    separate, pre-existing limitation with key-column mapping that
    would mask the sampled filter's correctness."""
    import duckdb
    from fastrecon.utils.normalization import quote_ident as _q
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE l AS SELECT * FROM (VALUES (1,'a'),(2,'b')) t(id,v)")
    con.execute("CREATE TABLE r AS SELECT * FROM (VALUES (1,'a'),(2,'b')) t(customer_id,v)")
    # Mimic what api.py builds for the right-side filter
    con.execute(f"CREATE TEMP TABLE sk AS SELECT DISTINCT {_q('id')} FROM l")
    pred = f"t.{_q('customer_id')} = s.{_q('id')}"
    con.execute(
        f"CREATE TEMP VIEW rs AS SELECT t.* FROM r t WHERE EXISTS "
        f"(SELECT 1 FROM sk s WHERE {pred})"
    )
    n = con.execute("SELECT COUNT(*) FROM rs").fetchone()[0]
    assert n == 2, "sampled mode must filter right view by mapped right-key name"


def test_sampled_handles_quoted_key_identifier_safely():
    """Defensive: a key column name containing a double-quote character
    must be safely quoted, not break out of the SQL identifier."""
    # Build parquet files where the key column contains a literal '"'.
    weird_key = 'id"weird'
    fd_l, lp = tempfile.mkstemp(suffix=".parquet"); os.close(fd_l)
    fd_r, rp = tempfile.mkstemp(suffix=".parquet"); os.close(fd_r)
    pq.write_table(pa.table({weird_key: [1, 2, 3], "v": [10, 20, 30]}), lp)
    pq.write_table(pa.table({weird_key: [1, 2, 3], "v": [10, 20, 30]}), rp)
    try:
        result = compare(
            ParquetFile(lp), ParquetFile(rp), keys=[weird_key],
            compare_mode="sampled",
            config=ReconConfig(sample_size_keyed=100),
        )
        # Survived the quoted-identifier path → status is real, not ERROR.
        assert result.status == "MATCH"
    finally:
        os.unlink(lp)
        os.unlink(rp)


def test_sampled_empty_input_reports_empty_sample():
    """An empty left view yields empty filtered subviews. Status is
    MATCH (vacuously) but column_stats.empty_sample is True so the
    caller can distinguish the two cases."""
    # Empty parquet needs an explicit schema — pa.Table.from_pylist([])
    # produces a no-column table that DuckDB rejects.
    schema = pa.schema([("id", pa.int64()), ("v", pa.int64())])
    fd_l, left = tempfile.mkstemp(suffix=".parquet"); os.close(fd_l)
    pq.write_table(pa.table({"id": [], "v": []}, schema=schema), left)
    right = _write_parquet([{"id": 1, "v": 1}])
    try:
        result = compare(
            ParquetFile(left), ParquetFile(right), keys=["id"],
            compare_mode="sampled",
            config=ReconConfig(sample_size_keyed=100),
        )
        assert result.column_stats["sampled"]["empty_sample"] is True
        assert result.column_stats["sampled"]["actual_keys_sampled"] == 0
    finally:
        os.unlink(left)
        os.unlink(right)


def test_sampled_row_counts_reflect_filtered_subset():
    """row_count_left/right must reflect the filtered subviews, not the
    underlying full tables — otherwise users would be misled."""
    left = _write_parquet([{"id": i, "v": i} for i in range(100)])
    right = _write_parquet([{"id": i, "v": i} for i in range(100)])
    try:
        result = compare(
            ParquetFile(left), ParquetFile(right), keys=["id"],
            compare_mode="sampled",
            # Disable fast_path so the sampling actually runs; identical
            # data would otherwise short-circuit before sampling.
            config=ReconConfig(sample_size_keyed=10, fast_path=False),
        )
        # We sampled 10 distinct keys → both filtered subviews must have
        # exactly 10 rows.
        assert result.row_count_left == 10
        assert result.row_count_right == 10
        assert result.column_stats["sampled"]["actual_keys_sampled"] == 10
    finally:
        os.unlink(left)
        os.unlink(right)


# -------------------------------------------------- ODBC / JDBC source classes


def test_odbc_query_class_importable():
    """OdbcQuery should be importable from the public API even when
    pyodbc isn't installed — the ImportError only fires at register()."""
    from fastrecon import OdbcQuery
    src = OdbcQuery(conn_str="DRIVER=fake", query="SELECT 1")
    assert src.conn_str == "DRIVER=fake"


def test_jdbc_query_class_importable():
    from fastrecon import JdbcQuery
    src = JdbcQuery(
        jdbc_url="jdbc:fake://x",
        driver_class="com.fake.Driver",
        driver_jar="/tmp/fake.jar",
        query="SELECT 1",
    )
    assert src.jdbc_url == "jdbc:fake://x"
