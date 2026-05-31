"""SQL expression builders for normalizing column values per ReconConfig."""

from __future__ import annotations

from typing import List

from ..config import ReconConfig


def quote_ident(name: str) -> str:
    """DuckDB identifier quoting."""
    return '"' + name.replace('"', '""') + '"'


def normalize_expr(col: str, dtype: str, config: ReconConfig) -> str:
    """Return a DuckDB SQL expression that normalizes ``col`` for compare.

    ``dtype`` is the lowercase DuckDB type string (e.g. ``"varchar"``,
    ``"integer"``, ``"decimal(18,2)"``, ``"timestamp"``).
    """
    ident = quote_ident(col)
    expr = ident
    lower = dtype.lower()

    if "varchar" in lower or "text" in lower or lower in ("string",):
        if config.trim_strings:
            expr = f"TRIM({expr})"
        if not config.case_sensitive:
            expr = f"LOWER({expr})"
        if config.null_equals_empty:
            expr = f"NULLIF({expr}, '')"

    if (
        "decimal" in lower or "numeric" in lower or "double" in lower or "real" in lower or "float" in lower
    ) and config.decimal_scale is not None:
        expr = f"ROUND(CAST({expr} AS DOUBLE), {int(config.decimal_scale)})"

    if "timestamp" in lower and config.timestamp_tz:
        # DuckDB: timestamp_tz_func; use AT TIME ZONE
        expr = f"({expr} AT TIME ZONE '{config.timestamp_tz}')"

    return expr


def hash_row_expr(cols: List[str], dtypes: dict, config: ReconConfig) -> str:
    """Build a DuckDB expression that computes a stable row hash over ``cols``."""
    parts = []
    for c in cols:
        e = normalize_expr(c, dtypes.get(c, "varchar"), config)
        # COALESCE to a sentinel so NULLs differ from empty strings
        parts.append(f"COALESCE(CAST({e} AS VARCHAR), '\\x00NULL')")
    concat = " || '||' || ".join(parts) if parts else "''"
    return f"md5({concat})"
