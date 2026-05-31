"""Hand-written pandas+merge baseline.

The "I'll just do it myself" comparison most data engineers reach for first
before pulling in any reconciliation library: read both Parquets into
``DataFrame``s, ``pd.merge`` on the key columns with ``indicator=True``,
then bucket the result.

* ``_merge == 'left_only'``  → row missing on the right (key only on left)
* ``_merge == 'right_only'`` → row missing on the left  (key only on right)
* ``_merge == 'both'``       → compare non-key columns elementwise; any
                               row with at least one differing column
                               counts as a changed row.

Like ``datacompy``, this is pandas-bound and reads both inputs fully into
memory — so it sets a useful "naive baseline" floor for memory and time
that fastrecon's partitioned execution should beat at scale.
"""

from __future__ import annotations

from typing import Dict, Optional

from ._base import run_adapter


def _compare(args) -> Dict[str, Optional[int]]:
    import pandas as pd

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    df_l = pd.read_parquet(args.left)
    df_r = pd.read_parquet(args.right)

    merged = df_l.merge(df_r, on=keys, how="outer", indicator=True,
                        suffixes=("__l", "__r"))

    missing_in_right = int((merged["_merge"] == "left_only").sum())
    missing_in_left = int((merged["_merge"] == "right_only").sum())

    # Non-key columns that exist on both sides will appear with __l/__r
    # suffixes after the merge. A row is "changed" if ANY of those pairs
    # disagree (NaN==NaN treated as equal, matching fastrecon's semantics).
    both = merged[merged["_merge"] == "both"]
    non_key_cols = [c for c in df_l.columns if c not in keys and c in df_r.columns]

    if not non_key_cols or len(both) == 0:
        changed = 0
    else:
        diff_mask = None
        for c in non_key_cols:
            l = both[f"{c}__l"]
            r = both[f"{c}__r"]
            # Treat NaN==NaN as equal so we don't over-count on nullable cols.
            col_diff = (l != r) & ~(l.isna() & r.isna())
            diff_mask = col_diff if diff_mask is None else (diff_mask | col_diff)
        changed = int(diff_mask.sum())

    return {
        "reported_missing_in_left": missing_in_left,
        "reported_missing_in_right": missing_in_right,
        "reported_changed_rows": changed,
    }


if __name__ == "__main__":
    run_adapter(_compare)
