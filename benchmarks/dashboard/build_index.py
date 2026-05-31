"""Rebuild ``history/index.json`` from the JSON files in ``history/``.

Each history file is named ``<timestamp>-<tier>-<sha7>.json`` and contains the
raw list of ``BenchResult`` dicts emitted by ``benchmarks.run_matrix``. The
dashboard fetches ``index.json`` to discover which runs to plot.

Usage::

    python -m benchmarks.dashboard.build_index \\
        --history-dir benchmarks/dashboard/history \\
        --repo-url https://github.com/owner/repo
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# <timestamp>-<tier>-<sha7>.json, e.g. 20260423T060000Z-nightly-abc1234.json
_NAME_RE = re.compile(
    r"^(?P<ts>\d{8}T\d{6}Z)-(?P<tier>[a-zA-Z0-9_]+)-(?P<sha>[0-9a-f]{7,40})\.json$"
)


def _iso_from_compact(ts: str) -> str:
    # 20260423T060000Z -> 2026-04-23T06:00:00Z
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"


def build(history_dir: Path, repo_url: str | None) -> dict:
    runs = []
    for path in sorted(history_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        m = _NAME_RE.match(path.name)
        if not m:
            continue
        runs.append({
            "file": path.name,
            "timestamp": _iso_from_compact(m.group("ts")),
            "tier": m.group("tier"),
            "commit": m.group("sha"),
        })
    runs.sort(key=lambda r: r["timestamp"])
    return {"repo_url": repo_url or "", "runs": runs}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--history-dir", required=True, type=Path)
    p.add_argument("--repo-url", default=None)
    p.add_argument("--out", type=Path, default=None,
                   help="Defaults to <history-dir>/index.json")
    args = p.parse_args(argv)

    args.history_dir.mkdir(parents=True, exist_ok=True)
    out = args.out or (args.history_dir / "index.json")
    payload = build(args.history_dir, args.repo_url)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out} ({len(payload['runs'])} run(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
