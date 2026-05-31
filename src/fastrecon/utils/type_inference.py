"""Logical type inference.

Real-world reconciliation often pairs a well-typed source (Parquet,
SQL) against a stringly-typed one (CSV without header sniffing, JSON,
Excel exports, raw VARCHAR DB columns where someone stored numbers as
text). The physical dtypes look incompatible — ``BIGINT`` vs
``VARCHAR`` — but the *logical* type is the same: both are integers.

This module derives a logical type per column by:

  1. Looking at the physical dtype DuckDB reports for the registered
     view; numeric/date/bool physical types map directly.
  2. For physically-textual columns (VARCHAR / TEXT), sampling up to
     ``sample_size`` non-null values and probing in priority order:
     boolean → integer → decimal → date → timestamp. The narrowest
     type that accepts every sampled value wins. If none match the
     column stays ``text``.

The result feeds two downstream features:

  - SchemaDiff.logical_type_mismatches: a structured "real" type-drift
    report independent of physical-dtype noise (so MSSQL ``INT`` vs
    Postgres ``BIGINT`` no longer shows up, but JSON ``"42"`` vs
    Parquet ``INT`` does only when the inference disagrees).
  - keyed_compare: when both sides agree on a logical type but
    disagree on the physical one, cast both to a common SQL type
    (``BIGINT``/``DOUBLE``/``DATE``/``TIMESTAMP``) before diffing,
    instead of falling back to text comparison. This is how a
    VARCHAR ``"100"`` vs INT ``100`` correctly reports MATCH instead
    of MISMATCH on whitespace, leading zeros, or trailing decimals.
"""

from __future__ import annotations

from typing import Dict


# Logical type names — kept short and stable since they show up in the
# JSON report. ``text`` is the catch-all when no narrower type fits.
LOGICAL_TYPES = ("null", "bool", "integer", "decimal", "date", "timestamp", "text")


# Logical type → DuckDB SQL type to cast to during comparison.
LOGICAL_TO_SQL = {
    "bool":      "BOOLEAN",
    "integer":   "BIGINT",
    "decimal":   "DOUBLE",
    "date":      "DATE",
    "timestamp": "TIMESTAMP",
    "text":      "VARCHAR",
    "null":      "VARCHAR",
}


def physical_to_logical(physical: str) -> str:
    """Map a DuckDB physical dtype to its logical bucket. Used as the
    starting point before any data-driven inference."""
    p = physical.lower().split("(")[0].strip()
    if p == "boolean":
        return "bool"
    if p in {"tinyint", "smallint", "integer", "int", "bigint", "hugeint",
             "utinyint", "usmallint", "uinteger", "ubigint"}:
        return "integer"
    if p in {"double", "real", "float"} or p.startswith("decimal") or p.startswith("numeric"):
        return "decimal"
    if p == "date":
        return "date"
    if p in {"timestamp", "datetime", "timestamp_s", "timestamp_ms",
             "timestamp_us", "timestamp_ns"} or p.startswith("timestamp"):
        return "timestamp"
    if p in {"varchar", "text", "string", "char", "blob"}:
        return "text"
    return "text"


def _is_textual(physical: str) -> bool:
    p = physical.lower().split("(")[0].strip()
    return p in {"varchar", "text", "string", "char"}


# Probe order matters — narrowest first. ``BOOLEAN`` is checked against
# a small whitelist so we don't accept ``"1"``/``"0"`` as booleans (they
# should win the integer test instead).
_PROBES = [
    ("bool", (
        "COUNT(*) FILTER (WHERE {c} IS NOT NULL "
        "AND LOWER(TRIM({c})) NOT IN ('true','false','t','f','yes','no')) = 0"
    )),
    ("integer", (
        # DuckDB's TRY_CAST('1.5' AS BIGINT) truncates to 1 instead of
        # returning NULL — so we additionally reject anything containing
        # a decimal point or exponent character. That keeps "1.5" out of
        # the integer bucket and lets the decimal probe win.
        "COUNT(*) FILTER (WHERE {c} IS NOT NULL AND TRIM({c}) <> '' "
        "AND (TRY_CAST(TRIM({c}) AS BIGINT) IS NULL "
        "     OR TRIM({c}) LIKE '%.%' OR TRIM({c}) LIKE '%e%' OR TRIM({c}) LIKE '%E%')) = 0"
    )),
    ("decimal", (
        "COUNT(*) FILTER (WHERE {c} IS NOT NULL AND TRIM({c}) <> '' "
        "AND TRY_CAST(TRIM({c}) AS DOUBLE) IS NULL) = 0"
    )),
    ("date", (
        # Pure dates only — exclude anything containing time indicators
        # (':', ISO 'T' separator, or whitespace) so timestamp strings
        # fall through to the timestamp probe instead of being silently
        # truncated to their date portion.
        "COUNT(*) FILTER (WHERE {c} IS NOT NULL AND TRIM({c}) <> '' "
        "AND (TRY_CAST(TRIM({c}) AS DATE) IS NULL "
        "     OR TRIM({c}) LIKE '%:%' OR TRIM({c}) LIKE '% %' "
        "     OR REGEXP_MATCHES(TRIM({c}), '[0-9]T[0-9]'))) = 0"
    )),
    ("timestamp", (
        "COUNT(*) FILTER (WHERE {c} IS NOT NULL AND TRIM({c}) <> '' "
        "AND TRY_CAST(TRIM({c}) AS TIMESTAMP) IS NULL) = 0"
    )),
]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def infer_logical_types(
    con,
    view: str,
    physical: Dict[str, str],
    sample_size: int = 10_000,
) -> Dict[str, str]:
    """Return ``{column: logical_type}`` for every column in ``physical``.

    Non-textual columns get a direct physical→logical mapping (no data
    scan). Textual columns are probed against a sampled subquery so the
    pass is bounded regardless of view size.
    """
    out: Dict[str, str] = {}
    textual = []
    for col, phys in physical.items():
        if _is_textual(phys):
            textual.append(col)
            out[col] = "text"  # default until we probe
        else:
            out[col] = physical_to_logical(phys)

    if not textual:
        return out

    # One sampled subquery, many probes — much cheaper than re-sampling
    # per column. ``USING SAMPLE`` is DuckDB's bounded reservoir sampler.
    sample_sql = (
        f'(SELECT * FROM "{view}" USING SAMPLE {int(sample_size)} ROWS)'
    )

    # Build one big SELECT that runs every (column, probe) pair so we
    # round-trip to DuckDB exactly once.
    select_parts = []
    keys = []
    for col in textual:
        qc = _quote(col)
        select_parts.append(f"COUNT(*) FILTER (WHERE {qc} IS NOT NULL) AS {_quote(col + '__nonnull')}")
        keys.append((col, "__nonnull"))
        for name, tmpl in _PROBES:
            select_parts.append(
                f"({tmpl.format(c=qc)}) AS {_quote(col + '__' + name)}"
            )
            keys.append((col, name))
    sql = f"SELECT {', '.join(select_parts)} FROM {sample_sql}"
    row = con.execute(sql).fetchone()
    if row is None:
        return out

    # Reconstruct {col: {probe: result}} from the flat row.
    by_col: Dict[str, Dict[str, object]] = {}
    for (col, probe), val in zip(keys, row):
        by_col.setdefault(col, {})[probe] = val

    for col, results in by_col.items():
        nonnull = int(results.get("__nonnull") or 0)
        if nonnull == 0:
            out[col] = "null"  # all-NULL column — defer typing
            continue
        # Pick the narrowest probe whose count-of-violations is 0.
        chosen = "text"
        for name, _ in _PROBES:
            if results.get(name):
                chosen = name
                break
        out[col] = chosen
    return out
