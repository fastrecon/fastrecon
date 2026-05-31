"""PySpark baseline — the heavyweight distributed option.

Even on a single machine, PySpark is the comparison most data teams
reach for once their tables stop fitting in pandas. We run it in
``local[*]`` mode against the same Parquet pair so the numbers are
directly comparable to the in-process tools.

Counting model
--------------
We do a single ``FULL OUTER JOIN`` on the key columns. With both sides
aliased we can classify every joined row from a single pass:

* ``r.<key> IS NULL``     → key only on left  → missing on right
* ``l.<key> IS NULL``     → key only on right → missing on left
* both present, and any non-key column disagrees (NULL-safe via ``<=>``
  inverted) → changed row

Then we just take three ``count()``s. PySpark needs a JVM (``java``) on
``PATH``; if it isn't available the adapter raises and the harness
records a DNF — exactly how it handles other missing prerequisites.
"""

from __future__ import annotations

from typing import Dict, Optional

from ._base import run_adapter


def _compare(args) -> Dict[str, Optional[int]]:
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]

    # local[*] keeps everything in one JVM — fairer comparison vs the
    # other in-process baselines than spinning up a real cluster, and
    # avoids any network / scheduler noise in the timing.
    spark = (
        SparkSession.builder
        .appName("fastrecon-bench-pyspark")
        .master("local[*]")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "4g")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    try:
        df_l = spark.read.parquet(args.left).alias("l")
        df_r = spark.read.parquet(args.right).alias("r")

        join_cond = None
        for k in keys:
            cond = F.col(f"l.{k}").eqNullSafe(F.col(f"r.{k}"))
            join_cond = cond if join_cond is None else (join_cond & cond)

        joined = df_l.join(df_r, on=join_cond, how="fullouter")

        first_key = keys[0]
        left_null = F.col(f"l.{first_key}").isNull()
        right_null = F.col(f"r.{first_key}").isNull()

        # Build "any non-key column disagrees" predicate over columns that
        # exist on both sides. eqNullSafe treats NULL==NULL as equal.
        # Reuse the already-loaded frames' schemas — no extra parquet read.
        l_cols = set(df_l.columns)
        r_cols = set(df_r.columns)
        non_key_cols = [c for c in l_cols & r_cols if c not in keys]

        if non_key_cols:
            diff_pred = None
            for c in non_key_cols:
                col_diff = ~F.col(f"l.{c}").eqNullSafe(F.col(f"r.{c}"))
                diff_pred = col_diff if diff_pred is None else (diff_pred | col_diff)
            changed_pred = (~left_null) & (~right_null) & diff_pred
        else:
            changed_pred = F.lit(False)

        # One pass over the joined frame computes all three counts.
        agg = joined.agg(
            F.sum(F.when(left_null, 1).otherwise(0)).alias("missing_in_left"),
            F.sum(F.when(right_null, 1).otherwise(0)).alias("missing_in_right"),
            F.sum(F.when(changed_pred, 1).otherwise(0)).alias("changed"),
        ).collect()[0]

        return {
            "reported_missing_in_left": int(agg["missing_in_left"] or 0),
            "reported_missing_in_right": int(agg["missing_in_right"] or 0),
            "reported_changed_rows": int(agg["changed"] or 0),
        }
    finally:
        spark.stop()


if __name__ == "__main__":
    run_adapter(_compare)
