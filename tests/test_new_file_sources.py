"""Tests for the new file source types: JSON, fixed-width, TSV alias."""

from __future__ import annotations

import json
from pathlib import Path

from fastrecon import CsvFile, FixedWidthFile, JsonFile, compare


def _write(p: Path, content: str) -> None:
    p.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------- JSON

def test_json_ndjson_match(tmp_path: Path):
    a = tmp_path / "a.ndjson"
    b = tmp_path / "b.ndjson"
    rows = [
        {"id": 1, "name": "alice", "amount": 10.0},
        {"id": 2, "name": "bob", "amount": 20.0},
    ]
    payload = "\n".join(json.dumps(r) for r in rows) + "\n"
    _write(a, payload)
    _write(b, payload)
    res = compare(JsonFile(str(a)), JsonFile(str(b)), keys=["id"])
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 2


def test_json_array_mismatch(tmp_path: Path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write(a, json.dumps([
        {"id": 1, "amount": 10.0},
        {"id": 2, "amount": 20.0},
    ]))
    _write(b, json.dumps([
        {"id": 1, "amount": 10.0},
        {"id": 2, "amount": 99.0},   # changed
    ]))
    res = compare(JsonFile(str(a)), JsonFile(str(b)), keys=["id"])
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1


# --------------------------------------------------------------- fixed-width

def test_fixed_width_match(tmp_path: Path):
    # Layout: id (1-5, 5 chars), name (6-15, 10 chars), amount (16-25, 10 chars)
    rows_a = [
        "00001alice     0000010.00",
        "00002bob       0000020.00",
        "00003carol     0000030.00",
    ]
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    _write(a, "\n".join(rows_a) + "\n")
    _write(b, "\n".join(rows_a) + "\n")

    spec = [("id", 1, 5), ("name", 6, 10), ("amount", 16, 10)]
    res = compare(
        FixedWidthFile(str(a), columns=spec),
        FixedWidthFile(str(b), columns=spec),
        keys=["id"],
    )
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 3


def test_fixed_width_detects_change(tmp_path: Path):
    rows_a = [
        "00001alice     0000010.00",
        "00002bob       0000020.00",
    ]
    rows_b = [
        "00001alice     0000010.00",
        "00002bob       0000099.00",  # amount changed
    ]
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    _write(a, "\n".join(rows_a) + "\n")
    _write(b, "\n".join(rows_b) + "\n")

    spec = [("id", 1, 5), ("name", 6, 10), ("amount", 16, 10)]
    res = compare(
        FixedWidthFile(str(a), columns=spec),
        FixedWidthFile(str(b), columns=spec),
        keys=["id"],
    )
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1


def test_fixed_width_skip_header(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    payload = (
        "HEADER LINE TO IGNORE\n"
        "00001alice     0000010.00\n"
        "00002bob       0000020.00\n"
    )
    _write(a, payload)
    _write(b, payload)
    spec = [("id", 1, 5), ("name", 6, 10), ("amount", 16, 10)]
    res = compare(
        FixedWidthFile(str(a), columns=spec, skip_rows=1),
        FixedWidthFile(str(b), columns=spec, skip_rows=1),
        keys=["id"],
    )
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 2


# ----------------------------------------------------------------------- TSV

def test_tsv_via_csv_options(tmp_path: Path):
    a = tmp_path / "a.tsv"
    b = tmp_path / "b.tsv"
    payload = "id\tname\tamount\n1\talice\t10.00\n2\tbob\t20.00\n"
    _write(a, payload)
    _write(b, payload)
    res = compare(
        CsvFile(str(a), options={"delim": "\t"}),
        CsvFile(str(b), options={"delim": "\t"}),
        keys=["id"],
    )
    assert res.status == "MATCH", res.summary()


# ----------------------------------------------------------- parquet glob (folder)

def test_parquet_folder_glob(tmp_path: Path):
    """ParquetFile already accepts globs; this nails down the
    'folder of files as dataset' behavior the user listed."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()
    for i, folder in enumerate([folder_a, folder_b]):
        # Two parquet files per side, same logical content.
        for shard in (0, 1):
            tbl = pa.table({
                "id": [shard * 10 + 1, shard * 10 + 2],
                "amount": [1.0, 2.0],
            })
            pq.write_table(tbl, folder / f"part{shard}.parquet")

    from fastrecon import ParquetFile
    res = compare(
        ParquetFile(str(folder_a / "*.parquet")),
        ParquetFile(str(folder_b / "*.parquet")),
        keys=["id"],
    )
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 4
