"""Polars baseline.

Modern columnar DataFrame library written in Rust. Like ``pandas-merge``
this is the "I'll just do it myself" baseline — except Polars' query
engine is multi-threaded and Arrow-native, so it sets a much tougher
floor for fastrecon to beat than pandas does.

Strategy mirrors ``pandas_merge_adapter``:

* full outer join on the key columns (``how="full"``)
* a key present only on one side → missing on the other side
* a key present on both sides → compare the non-key columns elementwise;
  any row with at least one differing column counts as a changed row.
  ``null == null`` is treated as equal so we don't over-count on
  nullable columns (matching fastrecon's semantics).
"""

from __future__ import annotations

from typing import Dict, Optional

from ._base import run_adapter


def _compare(args) -> Dict[str, Optional[int]]:
    import polars as pl

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    df_l = pl.read_parquet(args.left)
    df_r = pl.read_parquet(args.right)

    # ``how="full"`` keeps every row from both sides; the right-side keys
    # land in ``<key>_right`` columns. coalesce=False so we can detect
    # which side a key came from by checking nulls in the key columns.
    joined = df_l.join(df_r, on=keys, how="full", suffix="_right", coalesce=False)

    left_key = pl.col(keys[0])
    right_key = pl.col(f"{keys[0]}_right")

    missing_in_right = int(joined.filter(right_key.is_null()).height)
    missing_in_left = int(joined.filter(left_key.is_null()).height)

    both = joined.filter(left_key.is_not_null() & right_key.is_not_null())
    non_key_cols = [c for c in df_l.columns if c not in keys and c in df_r.columns]

    if not non_key_cols or both.height == 0:
        changed = 0
    else:
        diff_expr = None
        for c in non_key_cols:
            l = pl.col(c)
            r = pl.col(f"{c}_right")
            # null==null treated equal, matching fastrecon.
            col_diff = (l != r) & ~(l.is_null() & r.is_null())
            # When exactly one side is null, ``!=`` is null in Polars; coerce
            # to True so single-side nulls count as a difference.
            col_diff = col_diff.fill_null(l.is_null() ^ r.is_null())
            diff_expr = col_diff if diff_expr is None else (diff_expr | col_diff)
        changed = int(both.select(diff_expr.alias("d")).get_column("d").sum())

    return {
        "reported_missing_in_left": missing_in_left,
        "reported_missing_in_right": missing_in_right,
        "reported_changed_rows": changed,
    }


if __name__ == "__main__":
    run_adapter(_compare)
