"""Tests for the new ``hash`` compare mode and the keyed ``row_hash`` opt-in."""

from __future__ import annotations

from pathlib import Path

from fastrecon import CsvFile, ReconConfig, compare


def _write(p: Path, lines: list[str]) -> None:
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_pair(tmp_path: Path, b_lines: list[str]) -> tuple[CsvFile, CsvFile]:
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write(a, [
        "id,name,amount,qty",
        "1,alice,10.00,1",
        "2,bob,20.00,2",
        "3,carol,30.00,3",
    ])
    _write(b, b_lines)
    return CsvFile(str(a)), CsvFile(str(b))


def test_hash_mode_match(tmp_path: Path):
    left, right = _make_pair(tmp_path, [
        "id,name,amount,qty",
        "1,alice,10.00,1",
        "2,bob,20.00,2",
        "3,carol,30.00,3",
    ])
    res = compare(left, right, keys=["id"], compare_mode="hash")
    assert res.status == "MATCH", res.summary()
    assert res.data_match is True
    h = res.column_stats["hash"]
    assert h["algo"] == "xxhash64"
    assert h["left_checksum"] == h["right_checksum"]
    assert "id" not in h["columns_hashed"]  # keys excluded from fingerprint
    assert set(h["columns_hashed"]) == {"name", "amount", "qty"}


def test_hash_mode_order_independent(tmp_path: Path):
    """Re-shuffled rows must produce the same fingerprint."""
    left, right = _make_pair(tmp_path, [
        "id,name,amount,qty",
        "3,carol,30.00,3",
        "1,alice,10.00,1",
        "2,bob,20.00,2",
    ])
    res = compare(left, right, keys=["id"], compare_mode="hash")
    assert res.status == "MATCH", res.summary()


def test_hash_mode_mismatch(tmp_path: Path):
    left, right = _make_pair(tmp_path, [
        "id,name,amount,qty",
        "1,alice,10.00,1",
        "2,bob,99.00,2",      # changed amount
        "3,carol,30.00,3",
    ])
    res = compare(left, right, keys=["id"], compare_mode="hash")
    assert res.status == "MISMATCH"
    h = res.column_stats["hash"]
    assert h["left_checksum"] != h["right_checksum"]
    # Hash mode reports no per-row sample by design.
    assert res.sample_mismatches == {}


def test_hash_mode_excludes_columns(tmp_path: Path):
    """Differences only in excluded columns must still hash as MATCH."""
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write(a, [
        "id,name,amount,load_ts",
        "1,alice,10.00,2026-01-01",
        "2,bob,20.00,2026-01-01",
    ])
    _write(b, [
        "id,name,amount,load_ts",
        "1,alice,10.00,2026-04-23",   # only load_ts differs
        "2,bob,20.00,2026-04-23",
    ])
    res = compare(
        CsvFile(str(a)), CsvFile(str(b)),
        keys=["id"], compare_mode="hash",
        exclude_columns=["load_ts"],
    )
    assert res.status == "MATCH", res.summary()


def test_keyed_row_hash_matches_per_column_count(tmp_path: Path):
    """row_hash=True must report the same changed-row count as the
    default per-column path on the same data."""
    left, right = _make_pair(tmp_path, [
        "id,name,amount,qty",
        "1,alice,10.00,1",
        "2,bob,25.00,2",      # changed amount
        "3,carol,30.00,9",    # changed qty
    ])
    cfg = ReconConfig(row_hash=True)
    res = compare(left, right, keys=["id"], config=cfg)
    assert res.status == "MISMATCH"
    assert res.changed_rows == 2
    # Sample carries keys but no per-column left/right values.
    keys_in_sample = {row["id"] for row in res.sample_mismatches["changed"]}
    assert keys_in_sample == {2, 3}
    for row in res.sample_mismatches["changed"]:
        assert "amount__left" not in row
        assert "amount__right" not in row
