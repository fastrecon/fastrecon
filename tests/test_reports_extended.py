"""Tests for the extended report exporters: to_csv, to_text, to_json(path),
and the new ``detail`` arg on to_html."""

from __future__ import annotations

import json
import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from fastrecon import ParquetFile, ReconConfig, compare


def _write_parquet(rows):
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _mismatch_pair():
    """Two small parquet files with one missing-left, one missing-right,
    one changed row — exercises every sample bucket."""
    left = _write_parquet([
        {"id": 1, "amt": 10},
        {"id": 2, "amt": 20},
        {"id": 3, "amt": 30},
    ])
    right = _write_parquet([
        {"id": 2, "amt": 20},
        {"id": 3, "amt": 99},  # changed
        {"id": 4, "amt": 40},  # extra on right
    ])
    return left, right


def test_to_csv_diff_layout_contains_all_buckets():
    left, right = _mismatch_pair()
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        csv_out = result.to_csv(layout="diff")
        # Header + at least one row per bucket
        assert csv_out.startswith("__bucket")
        assert "missing_in_left" in csv_out
        assert "missing_in_right" in csv_out
        assert "changed" in csv_out
    finally:
        os.unlink(left)
        os.unlink(right)


def test_to_csv_summary_layout_is_key_value():
    left, right = _mismatch_pair()
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        csv_out = result.to_csv(layout="summary")
        lines = csv_out.strip().splitlines()
        assert lines[0] == "metric,value"
        body = "\n".join(lines)
        assert "status,MISMATCH" in body
        assert "compare_mode,keyed" in body
    finally:
        os.unlink(left)
        os.unlink(right)


def test_to_csv_writes_file_when_path_given():
    left, right = _mismatch_pair()
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        out = result.to_csv(path=csv_path)
        # Use newline="" on read too — Python's universal-newline mode
        # rewrites line endings on read by default, which would
        # spuriously differ from the on-disk bytes written by csv.writer.
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            on_disk = f.read()
        assert on_disk == out
    finally:
        os.unlink(left)
        os.unlink(right)
        os.unlink(csv_path)


def test_to_text_renders_banner_and_buckets():
    left, right = _mismatch_pair()
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        txt = result.to_text(detail="diff")
        assert "fastrecon report" in txt
        assert "MISMATCH" in txt
        # Bucket headings present
        assert "Missing In Left" in txt
        assert "Missing In Right" in txt
        assert "Changed" in txt
    finally:
        os.unlink(left)
        os.unlink(right)


def test_to_text_summary_drops_buckets():
    left, right = _mismatch_pair()
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        txt = result.to_text(detail="summary")
        assert "MISMATCH" in txt
        assert "Missing In Left" not in txt
        assert "Changed" not in txt
    finally:
        os.unlink(left)
        os.unlink(right)


def test_to_json_writes_file_and_returns_string():
    left, right = _mismatch_pair()
    fd, json_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        out = result.to_json(path=json_path, indent=True)
        with open(json_path, "r", encoding="utf-8") as f:
            on_disk = f.read()
        assert on_disk == out
        parsed = json.loads(out)
        assert parsed["status"] == "MISMATCH"
        assert parsed["changed_rows"] == 1
    finally:
        os.unlink(left)
        os.unlink(right)
        os.unlink(json_path)


def test_to_html_detail_summary_drops_samples():
    left, right = _mismatch_pair()
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        full = result.to_html(detail="full")
        summary = result.to_html(detail="summary")
        # Full has the samples section, summary does not
        assert "Mismatch samples" in full
        assert "Mismatch samples" not in summary
        assert "Schema diff" not in summary  # schema also dropped at summary
    finally:
        os.unlink(left)
        os.unlink(right)


def test_to_html_detail_diff_keeps_schema_drops_samples():
    left, right = _mismatch_pair()
    try:
        result = compare(ParquetFile(left), ParquetFile(right), keys=["id"])
        diff = result.to_html(detail="diff")
        assert "Mismatch samples" not in diff
    finally:
        os.unlink(left)
        os.unlink(right)
