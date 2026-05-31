"""Plain-text reconciliation report.

Goal: a single, human-readable artifact you can dump into a build log,
email body, or Slack code block. No ANSI colors, no Unicode tables —
just fixed-width sections so it renders identically on every terminal.

``detail`` controls how much is included:

* ``"summary"`` — headline metrics only (drop-in replacement for
  ``result.summary()`` but with a header banner).
* ``"diff"`` (default) — summary + schema diff + the first
  ``ReconConfig.sample_limit`` mismatch rows from each bucket.
* ``"full"`` — everything including the partition table when present.
"""

from __future__ import annotations

from typing import Any, Dict, List

_BAR = "=" * 72
_SUB = "-" * 72


def render_text(result: "ReconResult", detail: str = "diff") -> str:  # noqa: F821
    if detail not in ("summary", "diff", "full"):
        raise ValueError(f"Unknown detail level: {detail!r}. Use 'summary', 'diff', or 'full'.")

    parts: List[str] = []
    parts.append(_BAR)
    parts.append(f"  fastrecon report  —  {result.status}")
    parts.append(_BAR)
    parts.append(_summary_block(result))

    if detail == "summary":
        return "\n".join(parts) + "\n"

    parts.append("")
    parts.append(_schema_block(result))

    samples = result.sample_mismatches or {}
    for bucket in ("missing_in_left", "missing_in_right", "changed"):
        rows = samples.get(bucket) or []
        if not rows:
            continue
        parts.append("")
        parts.append(f"{bucket.replace('_', ' ').title()}  ({len(rows)} sample row{'s' if len(rows) != 1 else ''})")
        parts.append(_SUB)
        parts.append(_table(rows))

    if detail == "full":
        partitions = (result.column_stats or {}).get("partitions") or []
        if partitions:
            parts.append("")
            parts.append("Partitions")
            parts.append(_SUB)
            parts.append(_table(partitions))

    return "\n".join(parts) + "\n"


def _summary_block(result) -> str:
    pairs = [
        ("status", result.status),
        ("compare_mode", result.compare_mode),
        ("keys", ", ".join(result.keys) or "—"),
        ("row_count_left", f"{result.row_count_left:,}"),
        ("row_count_right", f"{result.row_count_right:,}"),
        ("schema_match", result.schema_match),
        ("data_match", result.data_match),
        ("missing_in_left", f"{result.missing_in_left:,}"),
        ("missing_in_right", f"{result.missing_in_right:,}"),
        ("changed_rows", f"{result.changed_rows:,}"),
        ("duplicate_keys_left", f"{result.duplicate_keys_left:,}"),
        ("duplicate_keys_right", f"{result.duplicate_keys_right:,}"),
        ("elapsed_sec", f"{result.execution_metrics.elapsed_sec:.3f}"),
        ("engine", result.execution_metrics.engine),
    ]
    if result.error:
        pairs.append(("error", result.error))
    width = max(len(k) for k, _ in pairs)
    return "\n".join(f"  {k.ljust(width)} : {v}" for k, v in pairs)


def _schema_block(result) -> str:
    sd = result.schema_diff
    if not sd:
        return "Schema diff: (not computed)"
    if not (sd.missing_in_left or sd.missing_in_right or sd.logical_type_mismatches):
        return "Schema diff: schemas match."
    out = ["Schema diff", _SUB]
    if sd.missing_in_left:
        out.append(f"  missing_in_left  : {', '.join(sd.missing_in_left)}")
    if sd.missing_in_right:
        out.append(f"  missing_in_right : {', '.join(sd.missing_in_right)}")
    if sd.logical_type_mismatches:
        out.append("  logical_type_mismatches:")
        for col, sides in sd.logical_type_mismatches.items():
            out.append(f"    {col}: left={sides['left']} right={sides['right']}")
    return "\n".join(out)


def _table(rows: List[Dict[str, Any]]) -> str:
    """Compact fixed-width table. Truncates wide cells at 40 chars."""
    if not rows:
        return "  (none)"
    cols = list(rows[0].keys())
    # Union extra columns from later rows so heterogeneous samples render.
    for r in rows[1:]:
        for c in r.keys():
            if c not in cols:
                cols.append(c)
    widths = {c: len(c) for c in cols}
    string_rows: List[List[str]] = []
    for r in rows:
        srow = []
        for c in cols:
            v = r.get(c, "")
            s = "" if v is None else str(v)
            if len(s) > 40:
                s = s[:37] + "..."
            widths[c] = max(widths[c], len(s))
            srow.append(s)
        string_rows.append(srow)
    header = "  " + " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "  " + "-+-".join("-" * widths[c] for c in cols)
    body = "\n".join("  " + " | ".join(srow[i].ljust(widths[c]) for i, c in enumerate(cols)) for srow in string_rows)
    return "\n".join([header, sep, body])
