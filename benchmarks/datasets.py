"""Deterministic synthetic-dataset generator for the benchmark suite.

Generation runs **entirely inside DuckDB** so we can stream 100M-row
fixtures without ever materializing them in Python lists. Every column
is derived from the row's ``id`` via ``hash(id * P)`` with a different
prime per column, which makes the output:

* fully deterministic across processes (no Python ``random``, no
  hash-randomization issues), and
* cheap to write — DuckDB streams to a ZSTD-compressed Parquet file
  in a single ``COPY ... TO ...`` statement, no intermediate Python
  buffer.

The mismatch-bearing scenarios use **modular predicates on id** to pick
which rows change or drop. Because the predicates are arithmetic, we
can compute ``GroundTruth`` exactly via a tiny COUNT(*) query — no
sample-overlap fudge factor.

Schema (chosen to look like a typical warehouse fact table):

    id          int64        — primary key
    customer_id int64        — high-cardinality dim
    region      string       — partition column (4 values)
    amount      float64      — numeric, can drift
    qty         int32        — small numeric
    name        string       — random-looking string
    created_at  timestamp_ms — timestamp, can drift by a few ms
    is_active   bool
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Tuple

import duckdb

SCENARIOS = ("all_match", "small_mismatch", "large_mismatch", "precision_diff")


@dataclass(frozen=True)
class GroundTruth:
    """Authoritative answer the harness checks the tool's output against."""
    rows_left: int
    rows_right: int
    missing_in_left: int   # ids only in right
    missing_in_right: int  # ids only in left
    changed_rows: int      # same id, at least one column differs

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


# Base columns derived deterministically from id. All hashes use distinct
# primes so columns aren't correlated. ``hash`` in DuckDB returns UBIGINT.
_BASE_SELECT = """
    id::BIGINT AS id,
    (hash(id::BIGINT * 11) % {cust_mod})::BIGINT                        AS customer_id,
    CASE (hash(id::BIGINT * 7) % 4)
        WHEN 0 THEN 'EU' WHEN 1 THEN 'US' WHEN 2 THEN 'APAC'
        ELSE 'LATAM' END                                                AS region,
    round((hash(id::BIGINT * 13) % 100000) / 100.0, 2)                  AS amount,
    ((hash(id::BIGINT * 17) % 100) + 1)::INTEGER                        AS qty,
    'name_' || lpad((hash(id::BIGINT * 19) % 10000000)::VARCHAR, 7, '0')
                                                                         AS name,
    -- 1700000000000 ms = 2023-11-14 22:13:20 UTC; offset is a deterministic
    -- pseudo-random number of ms in [0, 1e10). make_timestamp() takes
    -- microseconds, so multiply ms by 1000.
    make_timestamp(
        (1700000000000::BIGINT + (hash(id::BIGINT * 23) % 10000000000)::BIGINT) * 1000
    )                                                                    AS created_at,
    ((hash(id::BIGINT * 29) % 100) < 92)                                AS is_active
"""


def _base_sql(rows: int) -> str:
    return f"SELECT {_BASE_SELECT.format(cust_mod=max(rows // 10, 1))} FROM range(0, {rows}) t(id)"


def generate(scenario: str, rows: int, out_dir: Path) -> Tuple[Path, Path, GroundTruth]:
    """Materialize the (left, right, ground_truth) triple, caching to disk.

    If the parquet pair and the sidecar ``ground_truth.json`` already exist
    for this (scenario, rows) combo, just return them.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; valid: {SCENARIOS}")
    out_dir.mkdir(parents=True, exist_ok=True)
    a = out_dir / f"{scenario}_{rows}_left.parquet"
    b = out_dir / f"{scenario}_{rows}_right.parquet"
    gt_path = out_dir / f"{scenario}_{rows}_ground_truth.json"

    if a.exists() and b.exists() and gt_path.exists():
        gt = GroundTruth(**json.loads(gt_path.read_text()))
        return a, b, gt

    base = _base_sql(rows)
    con = duckdb.connect(":memory:")

    def _count(predicate: str) -> int:
        return int(con.execute(
            f"SELECT COUNT(*) FROM range(0, {rows}) t(id) WHERE {predicate}"
        ).fetchone()[0])

    if scenario == "all_match":
        left_sql, right_sql = base, base
        gt = GroundTruth(rows, rows, 0, 0, 0)

    elif scenario == "small_mismatch":
        # Use disjoint modular predicates so changed/dropped sets never overlap:
        #   * changed   : id % 1000 == 0    (~0.1%)
        #   * drop_right: id % 5000 == 1
        #   * drop_left : id % 5000 == 2
        # These three patterns can never coincide (different residues mod 5000),
        # so ground-truth math is exact at every scale.
        n_changed     = _count("id % 1000 = 0 AND id % 5000 NOT IN (1, 2)")
        n_drop_right  = _count("id % 5000 = 1")
        n_drop_left   = _count("id % 5000 = 2")
        left_sql  = f"SELECT * FROM ({base}) WHERE id % 5000 <> 2"
        right_sql = f"""
            SELECT id, customer_id, region,
                CASE WHEN id % 1000 = 0 THEN amount + 0.5 ELSE amount END AS amount,
                qty, name, created_at, is_active
            FROM ({base}) WHERE id % 5000 <> 1
        """
        gt = GroundTruth(
            rows_left=rows - n_drop_left,
            rows_right=rows - n_drop_right,
            missing_in_left=n_drop_left,
            missing_in_right=n_drop_right,
            changed_rows=n_changed,
        )

    elif scenario == "large_mismatch":
        # ~5% rows changed across two columns.
        n_changed = _count("id % 20 = 0")
        left_sql = base
        right_sql = f"""
            SELECT id, customer_id, region,
                CASE WHEN id % 20 = 0 THEN amount + 1.0 ELSE amount END AS amount,
                CASE WHEN id % 20 = 0 THEN qty + 1   ELSE qty    END AS qty,
                name, created_at, is_active
            FROM ({base})
        """
        gt = GroundTruth(rows, rows, 0, 0, n_changed)

    else:  # precision_diff
        # Same logical data; right side has tiny precision drift in float +
        # a few-ms timestamp offset. Tools without tolerance support will see
        # *every* row as changed — by design.
        left_sql = base
        right_sql = f"""
            SELECT id, customer_id, region,
                amount + 1e-6 AS amount,
                qty, name,
                created_at + INTERVAL 3 MILLISECOND AS created_at,
                is_active
            FROM ({base})
        """
        gt = GroundTruth(rows, rows, 0, 0, rows)

    # Stream straight to parquet. No Python materialization — works at 100M.
    con.execute(
        f"COPY ({left_sql})  TO '{a}' (FORMAT 'parquet', COMPRESSION 'zstd')"
    )
    con.execute(
        f"COPY ({right_sql}) TO '{b}' (FORMAT 'parquet', COMPRESSION 'zstd')"
    )
    con.close()

    gt_path.write_text(gt.to_json())
    return a, b, gt


if __name__ == "__main__":  # pragma: no cover - manual generation
    import argparse, sys
    p = argparse.ArgumentParser(description="Pre-generate benchmark fixtures.")
    p.add_argument("--scenario", choices=SCENARIOS, required=True)
    p.add_argument("--rows", type=int, required=True)
    p.add_argument("--out-dir", default="benchmarks/data")
    args = p.parse_args()
    a, b, gt = generate(args.scenario, args.rows, Path(args.out_dir))
    print(f"left:  {a}\nright: {b}\nground_truth: {gt.to_json()}", file=sys.stderr)
