"""Hand-written DuckDB-SQL baseline.

A single ``FULL OUTER JOIN`` against both Parquets, registered via
``read_parquet``, with the three buckets computed in one pass:

* key only on left  → missing on right
* key only on right → missing on left
* key on both       → row is "changed" if any non-key column pair
                      differs (``IS DISTINCT FROM`` so NULLs are
                      treated as equal, matching fastrecon).

This is the SQL version of what ``pandas-merge`` and ``polars`` do in
their respective DataFrame APIs. Useful as a baseline because it shows
how much overhead a recon library adds over a hand-tuned, fully
vectorized SQL query running in the same engine fastrecon already
depends on.
"""

from __future__ import annotations

from typing import Dict, Optional

from ._base import run_adapter


def _quote_ident(name: str) -> str:
    # DuckDB identifiers: wrap in double quotes and escape inner quotes.
    return '"' + name.replace('"', '""') + '"'


def _compare(args) -> Dict[str, Optional[int]]:
    import duckdb
    import pyarrow.parquet as pq  # only used to peek at the schema

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    schema_l = pq.read_schema(args.left)
    schema_r = pq.read_schema(args.right)
    cols_l = list(schema_l.names)
    cols_r = set(schema_r.names)
    non_key_cols = [c for c in cols_l if c not in keys and c in cols_r]

    on_clause = " AND ".join(
        f"l.{_quote_ident(k)} = r.{_quote_ident(k)}" for k in keys
    )
    # Project explicit l_/r_ aliases for the keys (the * from a FULL OUTER
    # JOIN coalesces duplicate names, so we'd lose the side info otherwise),
    # plus l_/r_ aliases for every non-key column we need to compare.
    select_cols = []
    for k in keys:
        select_cols.append(f"l.{_quote_ident(k)} AS {_quote_ident('l_' + k)}")
        select_cols.append(f"r.{_quote_ident(k)} AS {_quote_ident('r_' + k)}")
    for c in non_key_cols:
        select_cols.append(f"l.{_quote_ident(c)} AS {_quote_ident('l_' + c)}")
        select_cols.append(f"r.{_quote_ident(c)} AS {_quote_ident('r_' + c)}")

    right_null = " AND ".join(f"{_quote_ident('r_' + k)} IS NULL" for k in keys)
    left_null = " AND ".join(f"{_quote_ident('l_' + k)} IS NULL" for k in keys)
    both_present = " AND ".join(
        f"{_quote_ident('l_' + k)} IS NOT NULL AND {_quote_ident('r_' + k)} IS NOT NULL"
        for k in keys
    )

    if non_key_cols:
        # IS DISTINCT FROM: NULL == NULL is FALSE (i.e. not different),
        # which matches fastrecon's "both NULL = equal" semantics.
        any_diff = " OR ".join(
            f"{_quote_ident('l_' + c)} IS DISTINCT FROM {_quote_ident('r_' + c)}"
            for c in non_key_cols
        )
        changed_expr = f"SUM(CASE WHEN ({both_present}) AND ({any_diff}) THEN 1 ELSE 0 END)"
    else:
        changed_expr = "0"

    sql = f"""
        WITH joined AS (
            SELECT {", ".join(select_cols)}
            FROM read_parquet('{args.left}') l
            FULL OUTER JOIN read_parquet('{args.right}') r
                ON {on_clause}
        )
        SELECT
            SUM(CASE WHEN {right_null} THEN 1 ELSE 0 END) AS missing_in_right,
            SUM(CASE WHEN {left_null}  THEN 1 ELSE 0 END) AS missing_in_left,
            {changed_expr} AS changed_rows
        FROM joined
    """

    con = duckdb.connect(":memory:")
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()

    missing_in_right, missing_in_left, changed = row
    return {
        "reported_missing_in_left": int(missing_in_left or 0),
        "reported_missing_in_right": int(missing_in_right or 0),
        "reported_changed_rows": int(changed or 0),
    }


if __name__ == "__main__":
    run_adapter(_compare)
