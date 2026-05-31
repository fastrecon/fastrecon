"""CSV report exporter.

Two layouts depending on what's most useful:

* ``layout="summary"`` — a 2-column key/value CSV of the headline metrics
  (status, row counts, mismatches, schema-match, etc.). Same shape as
  ``result.summary()`` but machine-readable.
* ``layout="diff"`` (default) — one CSV per mismatch bucket
  (``missing_in_left``, ``missing_in_right``, ``changed``) merged into a
  single sheet with a leading ``__bucket`` column. Empty buckets are
  skipped. Use this to feed BI tools or load into Excel.

Both layouts are deterministic and quote-safe via the stdlib ``csv``
module — no extra dependency.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List


def render_csv(result: "ReconResult", layout: str = "diff") -> str:  # noqa: F821
    if layout == "summary":
        return _render_summary(result)
    if layout == "diff":
        return _render_diff(result)
    raise ValueError(f"Unknown CSV layout: {layout!r}. Use 'summary' or 'diff'.")


def _render_summary(result) -> str:
    rows = [
        ("status", result.status),
        ("compare_mode", result.compare_mode),
        ("keys", "|".join(result.keys)),
        ("row_count_left", result.row_count_left),
        ("row_count_right", result.row_count_right),
        ("schema_match", result.schema_match),
        ("data_match", result.data_match),
        ("missing_in_left", result.missing_in_left),
        ("missing_in_right", result.missing_in_right),
        ("changed_rows", result.changed_rows),
        ("duplicate_keys_left", result.duplicate_keys_left),
        ("duplicate_keys_right", result.duplicate_keys_right),
        ("elapsed_sec", f"{result.execution_metrics.elapsed_sec:.6f}"),
        ("engine", result.execution_metrics.engine),
    ]
    if result.error:
        rows.append(("error", result.error))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", "value"])
    w.writerows(rows)
    return buf.getvalue()


def _render_diff(result) -> str:
    """All mismatch sample rows in one sheet, tagged by bucket.

    Column union across buckets keeps the CSV rectangular even when
    ``changed`` rows have ``__left/__right`` suffix columns that the
    missing-row buckets don't.
    """
    samples: Dict[str, List[Dict[str, Any]]] = result.sample_mismatches or {}
    all_rows: List[Dict[str, Any]] = []
    col_order: List[str] = ["__bucket"]
    seen = {"__bucket"}
    for bucket in ("missing_in_left", "missing_in_right", "changed"):
        for r in samples.get(bucket) or []:
            tagged = {"__bucket": bucket, **r}
            all_rows.append(tagged)
            for c in tagged.keys():
                if c not in seen:
                    seen.add(c)
                    col_order.append(c)

    buf = io.StringIO()
    w = csv.writer(buf)
    if not all_rows:
        # Empty diff still emits a one-line header so downstream tooling
        # doesn't choke on a zero-byte file.
        w.writerow(["__bucket"])
        return buf.getvalue()
    w.writerow(col_order)
    for r in all_rows:
        w.writerow([_stringify(r.get(c, "")) for c in col_order])
    return buf.getvalue()


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        # Stable repr for nested values; CSV cells stay scalar.
        return str(v)
    return str(v)
