"""CLI entry-point: run the benchmark matrix and emit a results table.

    python -m benchmarks.run_matrix --tier pr            # 1M tier (PR gate)
    python -m benchmarks.run_matrix --tier nightly       # 1M + 10M
    python -m benchmarks.run_matrix --tier full          # 1M + 10M + 100M

Each cell is run multiple times (``--repeat``, defaults: 3 for ``pr`` /
5 for ``nightly`` and ``full``) and reported as ``median (p95)`` so
single-shot CI noise doesn't mask small regressions or inflate small
wins. All raw samples are kept in the JSON output.

The output is both a machine-readable JSON file (``--json out.json``) and
a human-readable Markdown table (``--markdown out.md``). DNF cells are
shown as ``DNF (reason)`` so OOMs etc. don't mask real regressions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List

from .datasets import SCENARIOS
from .harness import run_matrix
from .result import BenchResult

TIERS = {
    "smoke":    (10_000,),
    "pr":       (1_000_000,),
    "nightly":  (1_000_000, 10_000_000),
    "full":     (1_000_000, 10_000_000, 100_000_000),
}

# Default sample count per tier. Smoke is a sanity check, so 1 is fine;
# pr is the headline gate (3 samples ≈ stable median in ~3× wall time);
# nightly/full have the budget for 5.
TIER_DEFAULT_REPEATS = {
    "smoke":   1,
    "pr":      3,
    "nightly": 5,
    "full":    5,
}


def _fmt_cell(r: BenchResult) -> str:
    if r.is_dnf:
        return f"DNF ({r.dnf})"
    rss_mb = (r.peak_rss_bytes or 0) / 1e6
    rps = r.rows_per_sec or 0
    ok = "✓" if r.correct else "✗"
    median = r.median_sec if r.median_sec is not None else r.elapsed_sec
    p95 = r.p95_sec if r.p95_sec is not None else median
    # median (p95) · RSS · r/s · ✓ — spread is always visible, even when
    # the cell only has one sample (median == p95).
    return f"{median:.2f}s (p95 {p95:.2f}s) · {rss_mb:.0f} MB · {rps:,.0f} r/s · {ok}"


def _markdown_table(results: List[BenchResult], scales: Iterable[int]) -> str:
    tools = sorted({r.tool for r in results})
    scenarios = sorted({r.scenario for r in results})
    lines = []
    for scale in scales:
        lines.append(f"\n### {scale:,} rows\n")
        lines.append("| Scenario | " + " | ".join(tools) + " |")
        lines.append("|" + "---|" * (1 + len(tools)))
        for scenario in scenarios:
            row = [scenario]
            for tool in tools:
                hit = next(
                    (r for r in results if r.tool == tool
                     and r.scenario == scenario and r.rows == scale),
                    None,
                )
                row.append(_fmt_cell(hit) if hit else "—")
            lines.append("| " + " | ".join(row) + " |")
    lines.append(
        "\n_Cell format: median (p95) · peak RSS · throughput · correctness "
        "(✓ matches ground-truth counts, ✗ disagrees). Median is taken over "
        "the cell's repeated samples; raw timings are in the JSON output._\n"
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", choices=list(TIERS), default="pr",
                   help="Which scale tier to run (default: pr = 1M rows)")
    p.add_argument("--tools", nargs="+",
                   default=["fastrecon", "datacompy", "data-diff",
                            "pandas-merge", "pyspark",
                            "polars", "duckdb-sql"])
    p.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    p.add_argument("--timeout", type=float, default=None,
                   help="Per-cell wall-clock timeout in seconds (DNF on overrun). "
                        "Applies to each individual sample, not the sum.")
    p.add_argument("--repeat", type=int, default=None,
                   help="How many times to run each cell. Defaults per tier: "
                        "smoke=1, pr=3, nightly=5, full=5. "
                        "Median + p95 + min/max are reported across samples.")
    p.add_argument("--json", dest="json_out", help="Write raw results to PATH (JSON).")
    p.add_argument("--markdown", dest="md_out", help="Write Markdown table to PATH.")
    args = p.parse_args(argv)

    scales = TIERS[args.tier]
    repeat = args.repeat if args.repeat is not None else TIER_DEFAULT_REPEATS[args.tier]
    if repeat < 1:
        p.error(f"--repeat must be >= 1, got {repeat}")

    print(f"# benchmark methodology: tier={args.tier}, repeat={repeat} "
          f"(reporting median + p95 across samples)", file=sys.stderr)

    results: List[BenchResult] = []
    for r in run_matrix(
        tools=tuple(args.tools), scenarios=tuple(args.scenarios),
        scales=scales, timeout_sec=args.timeout, repeat=repeat,
    ):
        print(f"[{r.tool:>10}] {r.scenario:<18} rows={r.rows:>11,}  ",
              _fmt_cell(r), file=sys.stderr)
        results.append(r)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps([r.to_dict() for r in results], indent=2, default=str)
        )
    md = _markdown_table(results, scales)
    if args.md_out:
        Path(args.md_out).write_text(md)
    print(md)
    # Exit 1 if any non-DNF run was incorrect (catches real regressions)
    return 1 if any(r.correct is False for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
