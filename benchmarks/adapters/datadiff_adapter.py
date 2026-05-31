"""Run ``data-diff`` against the same Parquet pair.

data-diff is now in maintenance mode and is primarily designed for in-DB
comparison; for parity we materialize both Parquets into a temporary
DuckDB file and diff via data-diff's table-table mode. If the local
install can't talk to DuckDB (it occasionally regresses), the run is
reported as DNF.

Counting model
--------------
``diff_tables`` yields ``(sign, row_tuple)``; row_tuple's leading fields
are the key columns. We group rows by their key tuple — a key that
appears on both sides (one ``-`` and one ``+``) is a **changed** row;
a key that appears only as ``-`` is **missing on the right**; only
``+`` is **missing on the left**. This avoids the buggy `min(...)`
heuristic in the previous version, which mis-classified counts when both
sides had unique rows.
"""

from __future__ import annotations

import os
import tempfile
from typing import Dict, Optional

from ._base import run_adapter


def _compare(args) -> Dict[str, Optional[int]]:
    import duckdb  # always available — fastrecon's hard dep
    from data_diff import connect_to_table, diff_tables  # type: ignore

    keys = tuple(k.strip() for k in args.keys.split(",") if k.strip())

    # Materialize both sides in a single on-disk DuckDB so data-diff can
    # connect via a SQLAlchemy URL. No httpfs / network needed. We clean
    # the file up in `finally` so large/full matrix runs don't accumulate
    # GBs of temp DuckDB files on the runner.
    fd, ddb_path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    try:
        con = duckdb.connect(ddb_path)
        con.execute(f"CREATE TABLE t_left  AS SELECT * FROM read_parquet('{args.left}')")
        con.execute(f"CREATE TABLE t_right AS SELECT * FROM read_parquet('{args.right}')")
        con.close()

        url = f"duckdb:///{ddb_path}"
        left_tbl = connect_to_table(url, "t_left", keys)
        right_tbl = connect_to_table(url, "t_right", keys)

        # Group diff events by the key prefix to classify changed vs missing.
        seen_minus: dict = {}  # key -> count of '-' events
        seen_plus: dict = {}   # key -> count of '+' events
        nkeys = len(keys)
        for sign, row in diff_tables(left_tbl, right_tbl):
            key = tuple(row[:nkeys])
            if sign == "-":
                seen_minus[key] = seen_minus.get(key, 0) + 1
            else:
                seen_plus[key] = seen_plus.get(key, 0) + 1

        all_keys = set(seen_minus) | set(seen_plus)
        changed = sum(1 for k in all_keys if k in seen_minus and k in seen_plus)
        missing_in_right = sum(1 for k in seen_minus if k not in seen_plus)
        missing_in_left = sum(1 for k in seen_plus if k not in seen_minus)
    finally:
        try:
            os.unlink(ddb_path)
        except OSError:
            pass

    return {
        "reported_missing_in_left": missing_in_left,
        "reported_missing_in_right": missing_in_right,
        "reported_changed_rows": changed,
    }


if __name__ == "__main__":
    run_adapter(_compare)
