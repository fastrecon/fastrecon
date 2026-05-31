"""Per-column profiling compare (null counts, distinct counts, min/max)."""

from __future__ import annotations

from typing import Any, Dict, List

from ..engines import DuckDBEngine
from ..utils.normalization import quote_ident


def profile_columns(engine: DuckDBEngine, view: str, columns: List[str]) -> Dict[str, Dict[str, Any]]:
    if not columns:
        return {}
    parts = []
    for c in columns:
        ic = quote_ident(c)
        parts.append(f"COUNT(*) FILTER (WHERE {ic} IS NULL) AS {quote_ident(c + '__nulls')}")
        parts.append(f"COUNT(DISTINCT {ic}) AS {quote_ident(c + '__distinct')}")
        parts.append(f"MIN(CAST({ic} AS VARCHAR)) AS {quote_ident(c + '__min')}")
        parts.append(f"MAX(CAST({ic} AS VARCHAR)) AS {quote_ident(c + '__max')}")
    sql = f"SELECT {', '.join(parts)} FROM \"{view}\""
    cur = engine.execute(sql)
    row = cur.fetchone()
    names = [d[0] for d in cur.description]
    flat = dict(zip(names, row))
    out: Dict[str, Dict[str, Any]] = {}
    for c in columns:
        out[c] = {
            "nulls": flat.get(c + "__nulls"),
            "distinct": flat.get(c + "__distinct"),
            "min": flat.get(c + "__min"),
            "max": flat.get(c + "__max"),
        }
    return out


def compare_profiles(
    engine: DuckDBEngine, left_view: str, right_view: str, columns: List[str]
) -> Dict[str, Dict[str, Any]]:
    left = profile_columns(engine, left_view, columns)
    right = profile_columns(engine, right_view, columns)
    merged: Dict[str, Dict[str, Any]] = {}
    for c in columns:
        merged[c] = {"left": left.get(c, {}), "right": right.get(c, {})}
    return merged
