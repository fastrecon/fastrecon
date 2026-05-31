"""Run ``datacompy`` against the same Parquet pair.

datacompy is pandas-bound and does not support partition-wise execution,
so it represents the "naive" baseline. Reads both Parquets fully into
memory before joining.
"""

from __future__ import annotations

from typing import Dict, Optional

from ._base import run_adapter


def _compare(args) -> Dict[str, Optional[int]]:
    import pandas as pd
    import datacompy

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    df_l = pd.read_parquet(args.left)
    df_r = pd.read_parquet(args.right)
    cmp = datacompy.Compare(df_l, df_r, join_columns=keys, df1_name="left", df2_name="right")

    # datacompy's API: rows only on one side, plus mismatched-row count on join.
    only_left = len(cmp.df1_unq_rows)        # missing on right
    only_right = len(cmp.df2_unq_rows)       # missing on left
    # Rows present on both sides whose non-key columns disagree.
    changed = len(cmp.intersect_rows) - int(cmp.count_matching_rows())

    return {
        "reported_missing_in_left": only_right,
        "reported_missing_in_right": only_left,
        "reported_changed_rows": changed,
    }


if __name__ == "__main__":
    run_adapter(_compare)
