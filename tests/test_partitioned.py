"""Partition-wise reconciliation tests."""

from __future__ import annotations

from pathlib import Path

from fastrecon import CsvFile, PartitionSpec, compare


def _write(p: Path, lines):
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _two_csvs(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    rows_a = ["id,region,amount"]
    rows_b = ["id,region,amount"]
    for i in range(1, 21):
        region = "EU" if i <= 10 else "US"
        rows_a.append(f"{i},{region},{i*10}.00")
        rows_b.append(f"{i},{region},{i*10}.00")
    return a, b, rows_a, rows_b


def test_value_partition_match(tmp_path: Path):
    a, b, ra, rb = _two_csvs(tmp_path)
    _write(a, ra); _write(b, rb)

    res = compare(
        CsvFile(str(a)), CsvFile(str(b)),
        keys=["id"],
        partition=PartitionSpec(column="region", strategy="value"),
    )
    assert res.status == "MATCH", res.summary()
    assert res.column_stats["partitioned_by"]["n_partitions"] == 2
    assert all(p["match"] for p in res.column_stats["partitions"])


def test_value_partition_detects_per_partition_diff(tmp_path: Path):
    a, b, ra, rb = _two_csvs(tmp_path)
    # Mutate one row in US, drop one in EU on the right side
    rb = [r for r in rb if not r.startswith("3,")]
    rb = [r if not r.startswith("15,") else "15,US,9999.00" for r in rb]
    _write(a, ra); _write(b, rb)

    res = compare(
        CsvFile(str(a)), CsvFile(str(b)),
        keys=["id"],
        partition=PartitionSpec(column="region", strategy="value"),
    )
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1
    assert res.missing_in_right == 1

    # Per-partition breakdown should pinpoint which partition failed
    parts = {p["partition"]: p for p in res.column_stats["partitions"]}
    assert parts["EU"]["missing_in_right"] == 1
    assert parts["US"]["changed_rows"] == 1
    assert parts["EU"]["match"] is False
    assert parts["US"]["match"] is False


def test_hash_partition_match(tmp_path: Path):
    a, b, ra, rb = _two_csvs(tmp_path)
    _write(a, ra); _write(b, rb)

    res = compare(
        CsvFile(str(a)), CsvFile(str(b)),
        keys=["id"],
        partition=PartitionSpec(column="id", strategy="hash", buckets=4),
    )
    assert res.status == "MATCH", res.summary()
    assert res.column_stats["partitioned_by"]["n_partitions"] == 4
    # Total counts across buckets must equal full counts
    assert sum(p["row_count_left"] for p in res.column_stats["partitions"]) == 20


def test_range_partition(tmp_path: Path):
    a, b, ra, rb = _two_csvs(tmp_path)
    rb = [r if not r.startswith("7,") else "7,EU,77.00" for r in rb]
    _write(a, ra); _write(b, rb)

    res = compare(
        CsvFile(str(a)), CsvFile(str(b)),
        keys=["id"],
        partition=PartitionSpec(
            column="id", strategy="range",
            boundaries=[(1, 11), (11, 21)],
        ),
    )
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1
    parts = {p["partition"]: p for p in res.column_stats["partitions"]}
    assert parts["[1, 11)"]["changed_rows"] == 1
    assert parts["[11, 21)"]["changed_rows"] == 0
