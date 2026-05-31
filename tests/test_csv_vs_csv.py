"""CSV vs CSV reconciliation — covers schema, key, change, missing, dup."""

from __future__ import annotations

from pathlib import Path

from fastrecon import CsvFile, ReconConfig, compare


def _write(p: Path, lines: list[str]) -> None:
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_perfect_match(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    rows = ["id,name,amount", "1,alice,10.00", "2,bob,20.00", "3,carol,30.00"]
    _write(a, rows)
    _write(b, rows)

    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"])
    assert res.status == "MATCH", res.summary()
    assert res.schema_match is True
    assert res.data_match is True
    assert res.row_count_left == 3
    assert res.row_count_right == 3
    assert res.changed_rows == 0
    assert res.missing_in_left == 0
    assert res.missing_in_right == 0


def test_missing_changed_and_duplicates(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write(a, [
        "id,name,amount",
        "1,alice,10.00",
        "2,bob,20.00",
        "3,carol,30.00",
        "4,dave,40.00",
    ])
    _write(b, [
        "id,name,amount",
        "1,alice,10.00",
        "2,bob,25.00",     # changed
        # 3 missing
        "4,dave,40.00",
        "5,erin,50.00",    # extra
        "5,erin,50.00",    # duplicate key on right
    ])

    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"])
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1
    assert res.missing_in_left == 1   # id=5 not in left
    assert res.missing_in_right == 1  # id=3 not in right
    assert res.duplicate_keys_right == 1
    assert res.duplicate_keys_left == 0
    # Sample mismatches captured
    assert any(s.get("id") == 2 for s in res.sample_mismatches["changed"])


def test_tolerance_treats_close_decimals_as_match(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write(a, ["id,amount", "1,10.000", "2,20.000"])
    _write(b, ["id,amount", "1,10.005", "2,20.001"])

    res = compare(
        CsvFile(str(a)), CsvFile(str(b)),
        keys=["id"], tolerances={"amount": 0.01},
    )
    assert res.status == "MATCH", res.summary()
    assert res.changed_rows == 0


def test_schema_mode_detects_extra_column(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write(a, ["id,name", "1,alice"])
    _write(b, ["id,name,extra", "1,alice,x"])

    res = compare(CsvFile(str(a)), CsvFile(str(b)), compare_mode="schema")
    assert res.schema_match is False
    assert res.status == "MISMATCH"
    assert "extra" in res.schema_diff.missing_in_left


def test_string_normalization_via_config(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write(a, ["id,name", "1,Alice", "2,bob "])
    _write(b, ["id,name", "1,alice", "2,BOB"])

    cfg = ReconConfig(trim_strings=True, case_sensitive=False)
    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"], config=cfg)
    assert res.status == "MATCH", res.summary()


def test_to_json_serializable(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _write(a, ["id,name", "1,alice"])
    _write(b, ["id,name", "1,alice"])

    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"])
    payload = res.to_json()
    assert '"status":"MATCH"' in payload
