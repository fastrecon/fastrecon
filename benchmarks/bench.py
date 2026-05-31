"""Benchmark fastrecon vs datacompy on synthetic CSV/Parquet data.

Run:
    PYTHONPATH=src python benchmarks/bench.py --rows 100000 --diff-pct 0.5

Notes
-----
* ``data-diff`` requires a live DB and is environment-bound; we skip it here
  and reference its docs instead. The relevant comparison is fastrecon vs
  ``datacompy`` (the de-facto pandas-bound recon library).
* If ``datacompy`` is not installed we still print fastrecon timings so the
  script is useful for tracking our own perf regressions.
"""

from __future__ import annotations

import argparse
import gc
import random
import time
import tracemalloc
from pathlib import Path
from typing import Callable, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from fastrecon import CsvFile, ParquetFile, compare


def gen(rows: int, diff_pct: float, out_dir: Path) -> Tuple[Path, Path]:
    random.seed(42)
    out_dir.mkdir(parents=True, exist_ok=True)
    a = out_dir / f"left_{rows}.parquet"
    b = out_dir / f"right_{rows}.parquet"
    ids = list(range(rows))
    amts = [round(random.random() * 1000, 2) for _ in ids]
    regions = [random.choice(["EU", "US", "APAC", "LATAM"]) for _ in ids]
    pq.write_table(pa.table({"id": ids, "amount": amts, "region": regions}), a)

    n_diff = max(1, int(rows * diff_pct / 100))
    diff_idx = set(random.sample(range(rows), n_diff))
    amts2 = [amts[i] + 1.0 if i in diff_idx else amts[i] for i in range(rows)]
    pq.write_table(pa.table({"id": ids, "amount": amts2, "region": regions}), b)
    return a, b


def _measure(label: str, fn: Callable):
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{label:<28} elapsed={elapsed:7.3f}s  peak_rss={peak/1e6:7.1f} MB")
    return label, elapsed, peak, result


def bench_fastrecon(a: Path, b: Path):
    return _measure(
        "fastrecon (parquet, keyed)",
        lambda: compare(ParquetFile(str(a)), ParquetFile(str(b)), keys=["id"]),
    )


def bench_fastrecon_partitioned(a: Path, b: Path):
    from fastrecon import PartitionSpec
    return _measure(
        "fastrecon (parquet, hash×8)",
        lambda: compare(
            ParquetFile(str(a)), ParquetFile(str(b)),
            keys=["id"], partition=PartitionSpec(column="id", strategy="hash", buckets=8),
        ),
    )


def bench_datacompy(a: Path, b: Path):
    try:
        from datacompy.core import Compare  # datacompy 0.19+
        import pandas as pd  # type: ignore
    except Exception as e:
        print(f"datacompy not available ({e}) — skipping")
        return None

    def _run():
        df1 = pd.read_parquet(a)
        df2 = pd.read_parquet(b)
        c = Compare(df1, df2, join_columns="id")
        # Force evaluation
        return (c.matches(), c.count_matching_rows())

    return _measure("datacompy (pandas, keyed)", _run)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=100_000)
    p.add_argument("--diff-pct", type=float, default=0.5)
    p.add_argument("--data-dir", default="benchmarks/data")
    args = p.parse_args()

    print(f"\n=== Generating {args.rows:,} rows, ~{args.diff_pct}% mutated ===")
    a, b = gen(args.rows, args.diff_pct, Path(args.data_dir))
    print(f"left:  {a}  ({a.stat().st_size/1e6:.1f} MB)")
    print(f"right: {b}  ({b.stat().st_size/1e6:.1f} MB)\n")

    runs = [bench_fastrecon(a, b), bench_fastrecon_partitioned(a, b), bench_datacompy(a, b)]

    print("\n=== Summary ===")
    print(f"{'engine':<28} {'elapsed':>10} {'peak_mem':>12}")
    print("-" * 54)
    for r in runs:
        if r is None: continue
        label, elapsed, peak, _ = r
        print(f"{label:<28} {elapsed:>9.3f}s {peak/1e6:>10.1f} MB")


if __name__ == "__main__":
    main()
