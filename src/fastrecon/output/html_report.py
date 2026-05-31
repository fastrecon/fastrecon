"""Single-file self-contained HTML report for ``ReconResult``.

No template engine, no external assets — just a string template. Open the
output file in any browser; safe to email or attach to a CI build.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
from typing import Any, Dict, List


def render_html(
    result: "ReconResult",  # noqa: F821
    title: str = "fastrecon report",
    detail: str = "full",
) -> str:
    """Render a self-contained HTML report.

    ``detail`` controls how much is included:
        * ``"summary"`` — header + metrics table only.
        * ``"diff"``    — metrics + schema diff (skip mismatch sample tables).
        * ``"full"``    — everything (default).
    """
    if detail not in ("summary", "diff", "full"):
        raise ValueError(f"Unknown detail level: {detail!r}. Use 'summary', 'diff', or 'full'.")
    status = result.status
    color = {"MATCH": "#16a34a", "MISMATCH": "#dc2626", "ERROR": "#9333ea"}.get(status, "#475569")

    metrics = [
        ("Status", status),
        ("Compare mode", result.compare_mode),
        ("Keys", ", ".join(result.keys) or "—"),
        ("Row count (left)", f"{result.row_count_left:,}"),
        ("Row count (right)", f"{result.row_count_right:,}"),
        ("Schema match", result.schema_match),
        ("Data match", result.data_match),
        ("Missing in left", f"{result.missing_in_left:,}"),
        ("Missing in right", f"{result.missing_in_right:,}"),
        ("Changed rows", f"{result.changed_rows:,}"),
        ("Duplicate keys (left)", f"{result.duplicate_keys_left:,}"),
        ("Duplicate keys (right)", f"{result.duplicate_keys_right:,}"),
        ("Elapsed (sec)", f"{result.execution_metrics.elapsed_sec:.3f}"),
        ("Engine", result.execution_metrics.engine),
    ]

    # Detail gating: summary drops everything below the metrics table;
    # diff keeps the schema diff but drops mismatch samples + the
    # partition table; full keeps everything.
    schema_html = _schema_section(result) if detail in ("diff", "full") else ""
    partition_html = _partition_section(result) if detail == "full" else ""
    samples_html = _samples_section(result) if detail == "full" else ""

    metric_rows = "\n".join(
        f"<tr><th>{_html.escape(str(k))}</th><td>{_html.escape(str(v))}</td></tr>"
        for k, v in metrics
    )
    err = (
        f'<div class="err">Error: {_html.escape(result.error)}</div>'
        if result.error else ""
    )
    generated = _dt.datetime.now().isoformat(timespec="seconds")

    return _TEMPLATE.format(
        title=_html.escape(title),
        status=_html.escape(status),
        color=color,
        generated=_html.escape(generated),
        metric_rows=metric_rows,
        err=err,
        schema_section=schema_html,
        partition_section=partition_html,
        samples_section=samples_html,
    )


def _schema_section(result) -> str:
    sd = result.schema_diff
    if not sd:
        return ""
    bits = []
    if sd.missing_in_left:
        bits.append(f"<p><b>Missing in left:</b> {_html.escape(', '.join(sd.missing_in_left))}</p>")
    if sd.missing_in_right:
        bits.append(f"<p><b>Missing in right:</b> {_html.escape(', '.join(sd.missing_in_right))}</p>")
    if sd.type_mismatches:
        rows = "".join(
            f"<tr><td>{_html.escape(c)}</td><td>{_html.escape(l)}</td><td>{_html.escape(r)}</td></tr>"
            for c, (l, r) in sd.type_mismatches.items()
        )
        bits.append(
            "<table><thead><tr><th>Column</th><th>Left type</th><th>Right type</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    if not bits:
        bits.append("<p>Schemas match.</p>")
    return "<h2>Schema diff</h2>" + "".join(bits)


def _partition_section(result) -> str:
    parts = result.column_stats.get("partitions") if result.column_stats else None
    if not parts:
        return ""
    meta = result.column_stats.get("partitioned_by", {})
    heatmap = _partition_heatmap(parts)
    rows = "".join(
        f'<tr class="{"ok" if p["match"] else "bad"}">'
        f'<td>{_html.escape(str(p["partition"]))}</td>'
        f'<td>{p["row_count_left"]:,}</td>'
        f'<td>{p["row_count_right"]:,}</td>'
        f'<td>{p["missing_in_left"]:,}</td>'
        f'<td>{p["missing_in_right"]:,}</td>'
        f'<td>{p["changed_rows"]:,}</td>'
        f'<td>{"OK" if p["match"] else "FAIL"}</td>'
        "</tr>"
        for p in parts
    )
    head = (
        f"<h2>Partitioned by <code>{_html.escape(meta.get('column', ''))}</code> "
        f"(strategy: {_html.escape(meta.get('strategy', ''))}, "
        f"{meta.get('n_partitions', 0)} partitions)</h2>"
    )
    return head + heatmap + (
        "<table><thead><tr><th>Partition</th><th>Left</th><th>Right</th>"
        "<th>Missing L</th><th>Missing R</th><th>Changed</th><th>Status</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _partition_heatmap(parts: List[Dict[str, Any]]) -> str:
    """A pure-CSS heatmap: one cell per partition, color-graded by mismatch rate.

    No JS, no CDN — renders identically when the file is opened from disk
    with no network. Each cell shows partition label and total mismatches;
    intensity is proportional to the share of mismatching rows.
    """
    if not parts:
        return ""
    totals = []
    for p in parts:
        denom = max(int(p.get("row_count_left", 0)) + int(p.get("row_count_right", 0)), 1)
        bad = int(p.get("missing_in_left", 0)) + int(p.get("missing_in_right", 0)) + int(p.get("changed_rows", 0))
        totals.append((p, bad, bad / denom))
    max_rate = max((t[2] for t in totals), default=0.0) or 1.0

    cells: List[str] = []
    for p, bad, rate in totals:
        # 0 -> green, max -> red. Linear interpolation in HSL.
        if p["match"]:
            bg = "#f0fdf4"; fg = "#166534"
        else:
            # 0..120 hue (red..yellow..green) but inverted: rate=1 -> hue 0 (red)
            hue = int(120 * (1 - (rate / max_rate)))
            bg = f"hsl({hue}, 75%, 55%)"
            fg = "#0f172a"
        label = _html.escape(str(p["partition"]))
        cells.append(
            f'<div class="hm-cell" style="background:{bg};color:{fg}" '
            f'title="{label}: {bad} mismatches ({rate:.1%})">'
            f'<div class="hm-label">{label}</div>'
            f'<div class="hm-val">{bad:,}</div>'
            f"</div>"
        )
    return f'<div class="hm-grid">{"".join(cells)}</div>'


def _samples_section(result) -> str:
    if not result.sample_mismatches:
        return ""
    out = ["<h2>Mismatch samples</h2>"]
    for label in ("missing_in_left", "missing_in_right", "changed"):
        rows: List[Dict[str, Any]] = result.sample_mismatches.get(label) or []
        if not rows:
            continue
        cols = list(rows[0].keys())
        thead = "".join(f"<th>{_html.escape(c)}</th>" for c in cols)
        body = "".join(
            "<tr>" + "".join(
                f"<td>{_html.escape(str(r.get(c, '')))}</td>" for c in cols
            ) + "</tr>"
            for r in rows
        )
        out.append(
            f"<h3>{label.replace('_', ' ').title()} ({len(rows)} sample"
            f"{'s' if len(rows) != 1 else ''})</h3>"
            f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"
        )
    return "".join(out)


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>{title}</title>
<style>
  body{{font-family:system-ui,-apple-system,sans-serif;margin:2rem;color:#0f172a;background:#f8fafc}}
  h1{{margin:0 0 .25rem 0}}
  .badge{{display:inline-block;padding:.25rem .75rem;border-radius:.5rem;color:#fff;background:{color};font-weight:600;letter-spacing:.05em}}
  .meta{{color:#64748b;font-size:.85rem;margin-bottom:1.5rem}}
  table{{border-collapse:collapse;background:#fff;margin:.5rem 0 1.5rem 0;box-shadow:0 1px 2px rgba(0,0,0,.04);width:100%}}
  th,td{{padding:.5rem .75rem;border-bottom:1px solid #e2e8f0;text-align:left;font-size:.9rem}}
  thead th{{background:#f1f5f9;color:#334155;font-weight:600}}
  tr.ok td{{background:#f0fdf4}}
  tr.bad td{{background:#fef2f2}}
  h2{{margin-top:2rem;border-bottom:2px solid #e2e8f0;padding-bottom:.25rem}}
  h3{{margin-top:1rem;color:#475569;font-size:1rem}}
  code{{background:#e2e8f0;padding:.1rem .35rem;border-radius:.25rem;font-size:.85em}}
  .err{{background:#fef2f2;color:#991b1b;padding:.75rem 1rem;border-left:4px solid #dc2626;margin:1rem 0}}
  .hm-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:.5rem;margin:.75rem 0 1rem 0}}
  .hm-cell{{padding:.6rem .5rem;border-radius:.4rem;text-align:center;font-size:.8rem;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
  .hm-label{{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .hm-val{{font-variant-numeric:tabular-nums;opacity:.85;margin-top:.15rem}}
</style>
</head><body>
<h1>{title}</h1>
<div><span class="badge">{status}</span></div>
<div class="meta">Generated {generated} · fastrecon</div>
{err}
<h2>Summary</h2>
<table><tbody>{metric_rows}</tbody></table>
{schema_section}
{partition_section}
{samples_section}
</body></html>
"""
