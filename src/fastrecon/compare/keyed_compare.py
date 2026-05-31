"""Key-based row comparison.

Strategy:
    1. Detect duplicate keys on each side.
    2. LEFT/RIGHT anti-join to find rows missing on either side.
    3. INNER join + per-column normalized comparison to find changed rows.
    4. Sample up to ``config.sample_limit`` mismatches for the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import ReconConfig
from ..engines import DuckDBEngine
from ..exceptions import CompareError
from ..utils.normalization import normalize_expr, quote_ident
from .hash_compare import row_hash_expr


@dataclass
class KeyedCompareResult:
    missing_in_left: int = 0
    missing_in_right: int = 0
    changed_rows: int = 0
    duplicate_keys_left: int = 0
    duplicate_keys_right: int = 0
    sample_missing_in_left: List[Dict[str, Any]] = field(default_factory=list)
    sample_missing_in_right: List[Dict[str, Any]] = field(default_factory=list)
    sample_changed: List[Dict[str, Any]] = field(default_factory=list)
    columns_compared: List[str] = field(default_factory=list)


def keyed_compare(
    engine: DuckDBEngine,
    left_view: str,
    right_view: str,
    keys: List[str],
    common_columns: List[str],
    left_dtypes: Dict[str, str],
    right_dtypes: Dict[str, str],
    config: ReconConfig,
    logical_types: Optional[Dict[str, str]] = None,
) -> KeyedCompareResult:
    if not keys:
        raise CompareError("keyed_compare requires at least one key column")

    # Validate keys exist on both sides
    for k in keys:
        if k not in left_dtypes:
            raise CompareError(f"Key column {k!r} not in left source")
        mapped = config.column_mapping.get(k, k)
        if mapped not in right_dtypes:
            raise CompareError(f"Key column {mapped!r} not in right source")

    # Columns to compare = common cols minus keys minus excluded; honor 'columns' allowlist
    excluded = set(config.exclude_columns) | set(keys)
    candidates = [c for c in common_columns if c not in excluded]
    if config.columns is not None:
        allow = set(config.columns)
        candidates = [c for c in candidates if c in allow]

    res = KeyedCompareResult(columns_compared=candidates)

    # 1) Duplicate keys
    res.duplicate_keys_left = _dup_count(engine, left_view, keys)
    right_keys = [config.column_mapping.get(k, k) for k in keys]
    res.duplicate_keys_right = _dup_count(engine, right_view, right_keys)

    # 2) Missing rows (anti joins)
    res.missing_in_right, res.sample_missing_in_right = _anti_join(
        engine, left_view, right_view, keys, right_keys, config.sample_limit
    )
    res.missing_in_left, res.sample_missing_in_left = _anti_join(
        engine, right_view, left_view, right_keys, keys, config.sample_limit
    )

    # 3) Changed rows — either per-column compare (default) or per-row
    #    hash compare (when ReconConfig.row_hash=True). The hash path is
    #    much cheaper on wide tables: one BIGINT compare instead of N
    #    column predicates, at the cost of losing per-column left/right
    #    values in the changed sample (only the keys are kept).
    if candidates:
        if config.row_hash:
            res.changed_rows, res.sample_changed = _changed_rows_hash(
                engine, left_view, right_view, keys, right_keys,
                candidates, left_dtypes, right_dtypes, config,
            )
        else:
            res.changed_rows, res.sample_changed = _changed_rows(
                engine, left_view, right_view, keys, right_keys,
                candidates, left_dtypes, right_dtypes, config,
                logical_types=logical_types,
            )

    return res


def _dup_count(engine: DuckDBEngine, view: str, keys: List[str]) -> int:
    keycols = ", ".join(quote_ident(k) for k in keys)
    sql = (
        f"SELECT COUNT(*) FROM ("
        f"SELECT {keycols} FROM \"{view}\" "
        f"GROUP BY {keycols} HAVING COUNT(*) > 1"
        f")"
    )
    return int(engine.fetchall(sql)[0][0])


def _anti_join(
    engine: DuckDBEngine,
    a_view: str,
    b_view: str,
    a_keys: List[str],
    b_keys: List[str],
    sample_limit: int,
) -> Tuple[int, List[Dict[str, Any]]]:
    on = " AND ".join(f"a.{quote_ident(ak)} = b.{quote_ident(bk)}" for ak, bk in zip(a_keys, b_keys))
    where_null = " AND ".join(f"b.{quote_ident(bk)} IS NULL" for bk in b_keys)
    a_keys_qual = ", ".join(f"a.{quote_ident(k)}" for k in a_keys)
    select_keys = ", ".join(f"a.{quote_ident(k)} AS {quote_ident(k)}" for k in a_keys)

    # Count DISTINCT missing keys — duplicates on the source side must not
    # inflate the missing count.
    count_sql = (
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {a_keys_qual} FROM \"{a_view}\" a "
        f"LEFT JOIN \"{b_view}\" b ON {on} WHERE {where_null})"
    )
    count = int(engine.fetchall(count_sql)[0][0])

    samples: List[Dict[str, Any]] = []
    if count and sample_limit > 0:
        sample_sql = (
            f"SELECT DISTINCT {select_keys} FROM \"{a_view}\" a LEFT JOIN \"{b_view}\" b "
            f"ON {on} WHERE {where_null} LIMIT {int(sample_limit)}"
        )
        rows = engine.fetchall(sample_sql)
        samples = [dict(zip(a_keys, r)) for r in rows]
    return count, samples


def _changed_rows(
    engine: DuckDBEngine,
    left_view: str,
    right_view: str,
    left_keys: List[str],
    right_keys: List[str],
    columns: List[str],
    left_dtypes: Dict[str, str],
    right_dtypes: Dict[str, str],
    config: ReconConfig,
    logical_types: Optional[Dict[str, str]] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    on = " AND ".join(f"l.{quote_ident(lk)} = r.{quote_ident(rk)}" for lk, rk in zip(left_keys, right_keys))

    # Per-column inequality predicates with tolerance + normalization.
    #
    # Two robustness rules layered on top of the normalization expressions:
    #
    #   (a) When the two sides report DIFFERENT dtypes for the same logical
    #       column (e.g. INTEGER on DB-A vs VARCHAR-with-empty-strings on
    #       DB-B), `IS DISTINCT FROM` would force DuckDB to find a common
    #       type and try to coerce the VARCHAR side into the numeric one,
    #       blowing up on values like ''.  We sidestep that by coercing
    #       both sides to VARCHAR via TRY_CAST (returns NULL on failure
    #       instead of raising) and treating empty string as NULL so a
    #       semantically-empty VARCHAR matches a NULL on the other side.
    #
    #   (b) The tolerance branch used to use bare CAST(... AS DOUBLE), which
    #       hits the same wall: CAST('' AS DOUBLE) raises. Switching to
    #       TRY_CAST makes unparseable values become NULL; the explicit
    #       (IS NULL) <> (IS NULL) check then catches the asymmetry.
    diff_predicates = []
    for c in columns:
        rc = config.column_mapping.get(c, c)
        l_dt = (left_dtypes.get(c, "varchar") or "varchar").lower()
        r_dt = (right_dtypes.get(rc, "varchar") or "varchar").lower()

        if l_dt != r_dt:
            # Mixed-dtype path. If logical type inference agrees on a
            # numeric/temporal/bool bucket for both sides, cast both to
            # that SQL type — so VARCHAR "100" vs INT 100 compares
            # numerically (no false mismatch on whitespace, leading
            # zeros, or trailing ".0"). Otherwise fall back to text
            # comparison via TRY_CAST so unparseable values don't
            # raise.
            from ..utils.type_inference import LOGICAL_TO_SQL
            logical = (logical_types or {}).get(c)
            if logical and logical not in ("text", "null"):
                # Stringify both sides, trim whitespace, treat empty as
                # NULL, then TRY_CAST to the agreed logical SQL type.
                # TRY_CAST returns NULL on failure (no exception), and
                # IS DISTINCT FROM treats NULL == NULL, so a blank text
                # cell correctly equals a real NULL on the other side.
                sql_type = LOGICAL_TO_SQL[logical]
                l_expr = f'TRY_CAST(NULLIF(TRIM(CAST(l."{c}" AS VARCHAR)), \'\') AS {sql_type})'
                r_expr = f'TRY_CAST(NULLIF(TRIM(CAST(r."{rc}" AS VARCHAR)), \'\') AS {sql_type})'
            else:
                l_expr = f'NULLIF(TRY_CAST(l."{c}" AS VARCHAR), \'\')'
                r_expr = f'NULLIF(TRY_CAST(r."{rc}" AS VARCHAR), \'\')'
                if config.trim_strings:
                    l_expr = f"TRIM({l_expr})"
                    r_expr = f"TRIM({r_expr})"
                if not config.case_sensitive:
                    l_expr = f"LOWER({l_expr})"
                    r_expr = f"LOWER({r_expr})"
        else:
            l_expr = normalize_expr(c, l_dt, config).replace('"' + c + '"', f'l."{c}"')
            r_expr = normalize_expr(rc, r_dt, config).replace('"' + rc + '"', f'r."{rc}"')

        tol = config.tolerances.get(c)
        if tol is not None:
            diff = (
                f"(({l_expr}) IS NULL) <> (({r_expr}) IS NULL) "
                f"OR ABS(TRY_CAST({l_expr} AS DOUBLE) - TRY_CAST({r_expr} AS DOUBLE)) > {float(tol)}"
            )
        else:
            diff = f"({l_expr}) IS DISTINCT FROM ({r_expr})"
        diff_predicates.append(f"({diff})")
    any_diff = " OR ".join(diff_predicates) if diff_predicates else "FALSE"

    count_sql = (
        f"SELECT COUNT(*) FROM \"{left_view}\" l INNER JOIN \"{right_view}\" r "
        f"ON {on} WHERE {any_diff}"
    )
    count = int(engine.fetchall(count_sql)[0][0])

    samples: List[Dict[str, Any]] = []
    if count and config.sample_limit > 0:
        # Build select list: keys + (left/right) per changed column
        sel_parts = [f"l.{quote_ident(k)} AS {quote_ident(k)}" for k in left_keys]
        for c in columns:
            rc = config.column_mapping.get(c, c)
            sel_parts.append(f'l."{c}" AS "{c}__left"')
            sel_parts.append(f'r."{rc}" AS "{c}__right"')
        sel = ", ".join(sel_parts)
        sample_sql = (
            f"SELECT {sel} FROM \"{left_view}\" l INNER JOIN \"{right_view}\" r "
            f"ON {on} WHERE {any_diff} LIMIT {int(config.sample_limit)}"
        )
        cur = engine.execute(sample_sql)
        col_names = [d[0] for d in cur.description]
        for r in cur.fetchall():
            samples.append(dict(zip(col_names, r)))
    return count, samples


def _changed_rows_hash(
    engine: DuckDBEngine,
    left_view: str,
    right_view: str,
    left_keys: List[str],
    right_keys: List[str],
    columns: List[str],
    left_dtypes: Dict[str, str],
    right_dtypes: Dict[str, str],
    config: ReconConfig,
) -> Tuple[int, List[Dict[str, Any]]]:
    """INNER join + single per-row hash inequality.

    Both sides hash the same logical column list (right may have its
    own names via ``column_mapping``) using the same normalization, so
    if the hashes differ the rows differ.
    """
    on = " AND ".join(
        f"l.{quote_ident(lk)} = r.{quote_ident(rk)}"
        for lk, rk in zip(left_keys, right_keys)
    )
    right_cols = [config.column_mapping.get(c, c) for c in columns]
    l_hash = row_hash_expr(columns, left_dtypes, config)
    r_hash = row_hash_expr(right_cols, right_dtypes, config)
    # Qualify column refs to the join sides. row_hash_expr emits bare
    # quoted identifiers, so prefix-substitute them here.
    for c in columns:
        l_hash = l_hash.replace(f'"{c}"', f'l."{c}"')
    for c in right_cols:
        r_hash = r_hash.replace(f'"{c}"', f'r."{c}"')

    diff = f"({l_hash}) IS DISTINCT FROM ({r_hash})"
    count_sql = (
        f'SELECT COUNT(*) FROM "{left_view}" l INNER JOIN "{right_view}" r '
        f"ON {on} WHERE {diff}"
    )
    count = int(engine.fetchall(count_sql)[0][0])

    samples: List[Dict[str, Any]] = []
    if count and config.sample_limit > 0:
        sel = ", ".join(
            f"l.{quote_ident(k)} AS {quote_ident(k)}" for k in left_keys
        )
        sample_sql = (
            f'SELECT {sel} FROM "{left_view}" l INNER JOIN "{right_view}" r '
            f"ON {on} WHERE {diff} LIMIT {int(config.sample_limit)}"
        )
        cur = engine.execute(sample_sql)
        col_names = [d[0] for d in cur.description]
        for r in cur.fetchall():
            samples.append(dict(zip(col_names, r)))
    return count, samples
