"""Tests for data-driven logical type inference + schema-aware
cross-dtype comparison."""

from __future__ import annotations

import os
import tempfile

import duckdb
import pytest

from fastrecon import ReconConfig, compare
from fastrecon.sources import CsvFile, ParquetFile
from fastrecon.utils.type_inference import (
    infer_logical_types,
    physical_to_logical,
)


# --------------------------------------------------------------- unit: inference
def test_physical_to_logical_buckets():
    assert physical_to_logical("BIGINT") == "integer"
    assert physical_to_logical("INTEGER") == "integer"
    assert physical_to_logical("DOUBLE") == "decimal"
    assert physical_to_logical("DECIMAL(10,2)") == "decimal"
    assert physical_to_logical("VARCHAR") == "text"
    assert physical_to_logical("DATE") == "date"
    assert physical_to_logical("TIMESTAMP") == "timestamp"
    assert physical_to_logical("BOOLEAN") == "bool"


def test_infer_logical_types_textual_columns():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE t AS SELECT * FROM (VALUES
            ('1',  '1.5',  '2024-01-15', '2024-01-15 10:30:00', 'true',  'foo'),
            ('2',  '2.7',  '2024-02-20', '2024-02-20 11:00:00', 'false', 'bar'),
            ('3',  '3.14', '2024-03-25', '2024-03-25 12:00:00', 'true',  'baz')
        ) AS v(int_col, dec_col, date_col, ts_col, bool_col, text_col)
    """)
    physical = {r[0]: r[1] for r in con.execute("DESCRIBE SELECT * FROM t").fetchall()}
    logical = infer_logical_types(con, "t", physical, sample_size=100)
    assert logical["int_col"] == "integer"
    assert logical["dec_col"] == "decimal"
    assert logical["date_col"] == "date"
    assert logical["ts_col"] == "timestamp"
    assert logical["bool_col"] == "bool"
    assert logical["text_col"] == "text"


def test_infer_handles_nulls_and_empties():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE t AS SELECT * FROM (VALUES
            ('1',  CAST(NULL AS VARCHAR)),
            (NULL, CAST(NULL AS VARCHAR)),
            ('',   CAST(NULL AS VARCHAR)),
            ('2',  CAST(NULL AS VARCHAR))
        ) AS v(numeric_with_blanks, all_null_text)
    """)
    physical = {r[0]: r[1] for r in con.execute("DESCRIBE SELECT * FROM t").fetchall()}
    logical = infer_logical_types(con, "t", physical, sample_size=100)
    # Empty strings + NULLs shouldn't block integer detection.
    assert logical["numeric_with_blanks"] == "integer"
    # An all-NULL *textual* column has no evidence to assign a type, so
    # we mark it "null" and treat it as compatible with anything.
    assert logical["all_null_text"] == "null"


# ---------------------------------------------------- integration: cross-dtype
def _write_csv(rows: list[dict]) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    cols = list(rows[0].keys())
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(",".join("" if r[c] is None else str(r[c]) for c in cols) + "\n")
    return path


def _write_parquet(rows: list[dict], schema=None) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    cols = list(rows[0].keys())
    if schema is not None:
        tbl = pa.table({c: [r[c] for r in rows] for c in cols}, schema=schema)
    else:
        arrays = {c: pa.array([r[c] for r in rows]) for c in cols}
        tbl = pa.table(arrays)
    pq.write_table(tbl, path)
    return path


def _varchar_parquet(rows):
    import pyarrow as pa
    schema = pa.schema([(c, pa.string()) for c in rows[0].keys()])
    return _write_parquet(rows, schema=schema)


def test_text_ints_match_real_ints():
    """The headline use case: one side has integers stored as VARCHAR
    ("100"), the other has real INTEGERs (100). Without logical
    inference the mixed-dtype text fallback works for clean values, but
    breaks on whitespace/blanks. With it, both sides cast to BIGINT
    and the values compare numerically."""
    text_path = _varchar_parquet([
        {"id": "1", "amount": "100"},
        {"id": "2", "amount": "200"},
        {"id": "3", "amount": "300"},
    ])
    int_path = _write_parquet([
        {"id": 1, "amount": 100},
        {"id": 2, "amount": 200},
        {"id": 3, "amount": 300},
    ])
    try:
        result = compare(
            ParquetFile(text_path), ParquetFile(int_path), keys=["id"],
        )
        assert result.status == "MATCH", result.summary()
        assert result.changed_rows == 0
        sd = result.schema_diff
        assert sd is not None
        assert sd.logical_left.get("amount") == "integer"
        assert sd.logical_right.get("amount") == "integer"
        assert not sd.logical_type_mismatches


    finally:
        os.unlink(text_path)
        os.unlink(int_path)


def test_text_ints_with_whitespace_and_blanks_match_real_ints():
    """Whitespace-padded and empty-string text values should still match
    their numeric counterparts (empty → NULL on the numeric side)."""
    text_path = _varchar_parquet([
        {"id": "1", "amount": " 100 "},
        {"id": "2", "amount": ""},
        {"id": "3", "amount": "300"},
    ])
    int_path = _write_parquet([
        {"id": 1, "amount": 100},
        {"id": 2, "amount": None},
        {"id": 3, "amount": 300},
    ])
    try:
        result = compare(
            ParquetFile(text_path), ParquetFile(int_path), keys=["id"],
        )
        assert result.status == "MATCH", result.summary()
    finally:
        os.unlink(text_path)
        os.unlink(int_path)


def test_genuine_logical_type_mismatch_is_reported():
    """Free-text on the left vs numeric on the right is real drift —
    must show up in logical_type_mismatches AND flip schema_match=False."""
    left_csv = _write_csv([
        {"id": "1", "name": "alice"},
        {"id": "2", "name": "bob"},
    ])
    right_pq = _write_parquet([
        {"id": 1, "name": 100},
        {"id": 2, "name": 200},
    ])
    try:
        result = compare(
            CsvFile(left_csv), ParquetFile(right_pq), keys=["id"],
        )
        sd = result.schema_diff
        assert sd is not None
        assert "name" in sd.logical_type_mismatches
        assert sd.logical_type_mismatches["name"]["left"] == "text"
        assert sd.logical_type_mismatches["name"]["right"] == "integer"
        assert result.schema_match is False
        assert result.status == "MISMATCH"
    finally:
        os.unlink(left_csv)
        os.unlink(right_pq)


def test_missing_columns_show_in_summary():
    left = _write_csv([{"id": "1", "a": "x", "b": "y"}])
    right = _write_csv([{"id": "1", "a": "x", "c": "z"}])
    try:
        result = compare(CsvFile(left), CsvFile(right), keys=["id"])
        sd = result.schema_diff
        assert sd is not None
        assert sd.missing_in_right == ["b"]
        assert sd.missing_in_left == ["c"]
        out = result.summary()
        assert "schema_diff:" in out
        assert "missing_in_left" in out and "c" in out
        assert "missing_in_right" in out and "b" in out
    finally:
        os.unlink(left)
        os.unlink(right)


def test_infer_disabled_falls_back_to_physical_drift_check():
    """Regression guard: with infer_logical_types=False, the schema
    match flag must still reflect physical type drift — otherwise users
    asking for strict physical checking get silent matches."""
    text_path = _varchar_parquet([{"id": "1", "name": "alice"}])
    int_path = _write_parquet([{"id": 1, "name": "alice"}])
    try:
        result = compare(
            ParquetFile(text_path), ParquetFile(int_path), keys=["id"],
            config=ReconConfig(infer_logical_types=False),
        )
        # id is VARCHAR on left, BIGINT on right — that's physical drift
        # and with inference off it should flip schema_match to False.
        assert result.schema_match is False
        assert "id" in result.schema_diff.type_mismatches
    finally:
        os.unlink(text_path)
        os.unlink(int_path)


def test_iso_t_timestamp_classifies_as_timestamp_not_date():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE t AS SELECT * FROM (VALUES
            ('2024-01-01T10:00:00'),
            ('2024-02-02T11:30:00')
        ) AS v(iso_ts)
    """)
    physical = {r[0]: r[1] for r in con.execute("DESCRIBE SELECT * FROM t").fetchall()}
    logical = infer_logical_types(con, "t", physical, sample_size=100)
    assert logical["iso_ts"] == "timestamp"


def test_infer_logical_types_can_be_disabled():
    """With inference off, the schema_diff carries no logical info and
    schema_match falls back to physical-dtype strictness — see
    test_infer_disabled_falls_back_to_physical_drift_check for the
    full semantics. Here we just verify the off switch works and the
    underlying row data still matches when types align."""
    a_path = _write_parquet([{"id": 1, "v": 1}, {"id": 2, "v": 2}])
    b_path = _write_parquet([{"id": 1, "v": 1}, {"id": 2, "v": 2}])
    try:
        result = compare(
            ParquetFile(a_path), ParquetFile(b_path), keys=["id"],
            config=ReconConfig(infer_logical_types=False),
        )
        assert result.schema_diff is not None
        assert result.schema_diff.logical_left == {}
        assert result.schema_diff.logical_right == {}
        assert result.status == "MATCH"
    finally:
        os.unlink(a_path)
        os.unlink(b_path)
