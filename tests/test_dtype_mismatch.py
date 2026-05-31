"""Regression: same column name, different dtypes across the two sides
must not crash the changed-rows predicate.

Real-world report: a SQL Server column was INT on one DB and VARCHAR
(with empty-string placeholders) on the other. The compare blew up with
'Could not convert string "" to INT32' because IS DISTINCT FROM tried
to find a common type and picked the numeric one.
"""

from __future__ import annotations

import duckdb

from fastrecon import ReconConfig, compare
from fastrecon.engines import DuckDBEngine
from fastrecon.sources.base import Source


class _ViewSource(Source):
    """Test-only source that just hands back a pre-existing view."""

    def __init__(self, sql: str):
        self._sql = sql

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        con.execute(f'CREATE OR REPLACE VIEW "{view_name}" AS {self._sql}')
        return f'SELECT * FROM "{view_name}"'


def test_int_vs_varchar_with_empty_string_does_not_crash():
    # Left: numeric column. Right: same logical column as VARCHAR with
    # one empty-string row. Same key on both sides.
    left_sql = (
        "SELECT * FROM (VALUES "
        "(1, CAST(10 AS INTEGER)), "
        "(2, CAST(20 AS INTEGER)), "
        "(3, CAST(30 AS INTEGER))"
        ") t(id, amount)"
    )
    right_sql = (
        "SELECT * FROM (VALUES "
        "(1, CAST('10' AS VARCHAR)), "
        "(2, CAST('' AS VARCHAR)), "       # the killer row
        "(3, CAST('30' AS VARCHAR))"
        ") t(id, amount)"
    )

    res = compare(
        _ViewSource(left_sql), _ViewSource(right_sql),
        keys=["id"],
    )
    # Must not be ERROR; the ConversionException must be gone.
    assert res.status != "ERROR", res.error
    # id=2 differs (10 vs empty), id=1 and id=3 match.
    assert res.changed_rows == 1, res.summary()
    assert res.row_count_left == 3
    assert res.missing_in_left == 0 and res.missing_in_right == 0


def test_dtype_mismatch_with_tolerance_does_not_crash():
    # Same setup but a tolerance is configured. The tolerance branch
    # used CAST(... AS DOUBLE) which also dies on ''.
    left_sql = (
        "SELECT * FROM (VALUES "
        "(1, CAST(10.0 AS DOUBLE)), "
        "(2, CAST(20.0 AS DOUBLE))"
        ") t(id, amount)"
    )
    right_sql = (
        "SELECT * FROM (VALUES "
        "(1, CAST('10.001' AS VARCHAR)), "  # within tol
        "(2, CAST(''       AS VARCHAR))"    # unparseable
        ") t(id, amount)"
    )

    res = compare(
        _ViewSource(left_sql), _ViewSource(right_sql),
        keys=["id"],
        config=ReconConfig(tolerances={"amount": 0.01}),
    )
    assert res.status != "ERROR", res.error
    # id=1: 10.0 vs 10.001 within 0.01 → match
    # id=2: 20.0 vs '' (unparseable → NULL) → mismatch
    assert res.changed_rows == 1, res.summary()


def test_same_dtype_path_unchanged():
    # Sanity: when types agree, behavior is what it always was.
    left_sql = "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) t(id, x)"
    right_sql = "SELECT * FROM (VALUES (1, 'a'), (2, 'B')) t(id, x)"
    res = compare(_ViewSource(left_sql), _ViewSource(right_sql), keys=["id"])
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1
