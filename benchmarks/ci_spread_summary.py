"""Print a per-cell spread summary from a run_matrix JSON output.

Used by .github/workflows/benchmarks.yml so reviewers can eyeball
sample-to-sample noise (min .. p95) directly in the job log without
downloading the artifact, and so cells whose p95 is more than 1.5x
their median get flagged as "noisy run, treat results with caution"
(without failing the build).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NOISE_THRESHOLD = 1.5  # p95 / median above this -> flag as noisy


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <results.json>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file() or path.stat().st_size == 0:
        print(f"No results JSON at {path}; skipping spread summary.")
        return 0

    data = json.loads(path.read_text())
    print(f"\n=== Per-cell sample spread (from {path}) ===")
    header = (
        f"{'tool':>10}  {'scenario':<18}  {'rows':>11}  {'n':>2}  "
        f"{'min':>7}  {'med':>7}  {'p95':>7}  {'max':>7}  notes"
    )
    print(header)
    print("-" * len(header))

    noisy: list[tuple[str, str, int, float]] = []
    for r in data:
        tool = r.get("tool", "?")
        scenario = r.get("scenario", "?")
        rows = r.get("rows", 0) or 0
        if r.get("dnf"):
            print(
                f"{tool:>10}  {scenario:<18}  {rows:>11,}  --  "
                f"{'':>7}  {'':>7}  {'':>7}  {'':>7}  DNF ({r['dnf']})"
            )
            continue
        samples = r.get("samples_sec") or []
        med = r.get("median_sec")
        p95 = r.get("p95_sec")
        mn = r.get("min_sec")
        mx = r.get("max_sec")
        if not samples or med is None or p95 is None:
            continue
        note = ""
        if med > 0 and (p95 / med) > NOISE_THRESHOLD:
            ratio = p95 / med
            note = f"NOISY (p95/median={ratio:.2f}x — treat with caution)"
            noisy.append((tool, scenario, rows, ratio))
        print(
            f"{tool:>10}  {scenario:<18}  {rows:>11,}  {len(samples):>2}  "
            f"{mn:>7.2f}  {med:>7.2f}  {p95:>7.2f}  {mx:>7.2f}  {note}"
        )

    if noisy:
        print(
            f"\n::warning::{len(noisy)} noisy cell(s) detected "
            f"(p95/median > {NOISE_THRESHOLD}x); benchmark numbers "
            "may be unreliable for those cells."
        )
        for tool, scenario, rows, ratio in noisy:
            print(
                f"::warning::noisy cell: {tool} / {scenario} @ {rows:,} rows "
                f"(p95/median = {ratio:.2f}x)"
            )
    else:
        print(
            f"\nAll cells within p95/median <= {NOISE_THRESHOLD}x — "
            "spread looks clean."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
