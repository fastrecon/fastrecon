"""Benchmark harness: orchestrate (tool × scenario × scale) runs.

Each tool runs in **its own subprocess** — ideally inside its own virtualenv
(see ``benchmarks/setup_envs.sh``) — so transitive dependency conflicts
between fastrecon, datacompy, and data-diff don't pollute results. The
harness:

* shells out to ``<tool_python> -m benchmarks.adapters.<tool>_adapter ...``
* parses the JSON line the adapter prints to stdout
* enforces a wall-clock timeout; ``TIMEOUT`` becomes a DNF
* compares the tool's reported counts against the seeded ``GroundTruth``
  to populate ``BenchResult.correct``
* repeats each cell ``repeat`` times (default 1) and reports
  median / p95 / min / max so single-shot CI noise doesn't mask small
  regressions or inflate small wins.

The harness itself is dependency-light (stdlib only) so it can be imported
from CI workflows without pulling fastrecon's heavy deps.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .datasets import GroundTruth, generate
from .result import BenchResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "benchmarks" / "data"
DEFAULT_ENVS_DIR = REPO_ROOT / ".benchmarks_envs"

# Map tool name → adapter module + venv directory name (under DEFAULT_ENVS_DIR)
ADAPTERS = {
    "fastrecon": ("benchmarks.adapters.fastrecon_adapter", "fastrecon"),
    "datacompy": ("benchmarks.adapters.datacompy_adapter", "datacompy"),
    "data-diff": ("benchmarks.adapters.datadiff_adapter", "datadiff"),
    "pandas-merge": ("benchmarks.adapters.pandas_merge_adapter", "pandas_merge"),
    "pyspark": ("benchmarks.adapters.pyspark_adapter", "pyspark"),
    "polars": ("benchmarks.adapters.polars_adapter", "polars"),
    "duckdb-sql": ("benchmarks.adapters.duckdb_sql_adapter", "duckdb_sql"),
}


def _python_for(tool: str, envs_dir: Path = DEFAULT_ENVS_DIR) -> str:
    """Return the python executable that should run ``tool``'s adapter.

    Prefer a per-tool venv created by ``setup_envs.sh``; fall back to the
    current interpreter (useful for local smoke tests where you've already
    installed the tool in your active env).
    """
    _, venv_name = ADAPTERS[tool]
    venv_py = envs_dir / venv_name / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def _percentile(samples: List[float], pct: float) -> float:
    """Nearest-rank percentile (no scipy/numpy dependency)."""
    if not samples:
        raise ValueError("empty sample list")
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    # Nearest-rank: index = ceil(pct/100 * N) - 1
    k = max(0, min(len(s) - 1, int(-(-pct * len(s) // 100)) - 1))
    return s[k]


def _invoke_adapter(
    tool: str, scenario: str, rows: int, left: Path, right: Path,
    keys: str, timeout_sec: Optional[float], envs_dir: Path,
) -> dict:
    """One subprocess invocation. Returns a dict with keys 'payload' (or
    None on parse failure) plus a synthetic 'dnf' on timeout/parse-fail."""
    module, _ = ADAPTERS[tool]
    py = _python_for(tool, envs_dir)
    cmd = [
        py, "-m", module,
        "--left", str(left), "--right", str(right),
        "--keys", keys, "--rows", str(rows),
        "--scenario", scenario, "--tool", tool,
    ]
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep
           + str(REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_sec, env=env, cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"payload": None, "dnf": "TIMEOUT"}

    payload = _parse_last_json_line(proc.stdout)
    if payload is None:
        return {
            "payload": None,
            "dnf": (f"ERROR: adapter produced no JSON (rc={proc.returncode}); "
                    "stderr tail: " + (proc.stderr or "")[-500:]),
        }
    return {"payload": payload, "dnf": payload.get("dnf")}


def run_one(
    tool: str,
    scenario: str,
    rows: int,
    *,
    keys: str = "id",
    timeout_sec: Optional[float] = None,
    data_dir: Path = DEFAULT_DATA_DIR,
    envs_dir: Path = DEFAULT_ENVS_DIR,
    repeat: int = 1,
) -> BenchResult:
    """Run a single benchmark cell ``repeat`` times and return a populated
    BenchResult with median / p95 / min / max across the samples.

    On the first DNF (timeout, parse error, or adapter-reported dnf) the
    cell short-circuits: re-running a 60s timeout four more times is just
    burning CI minutes for the same answer.
    """
    if tool not in ADAPTERS:
        raise ValueError(f"unknown tool {tool!r}; valid: {list(ADAPTERS)}")
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")

    try:
        left, right, gt = generate(scenario, rows, data_dir)
    except MemoryError:
        return BenchResult(tool=tool, scenario=scenario, rows=rows,
                           dnf="OOM_DURING_FIXTURE_GEN", repeat=repeat)
    except Exception as e:
        return BenchResult(
            tool=tool, scenario=scenario, rows=rows, repeat=repeat,
            dnf=f"FIXTURE_GEN_ERROR: {e.__class__.__name__}: {e}",
        )

    samples: List[float] = []
    rss_samples: List[int] = []
    last_payload: Optional[dict] = None
    for _ in range(repeat):
        out = _invoke_adapter(
            tool, scenario, rows, left, right, keys, timeout_sec, envs_dir,
        )
        if out["dnf"] is not None:
            return BenchResult(
                tool=tool, scenario=scenario, rows=rows, repeat=repeat,
                dnf=out["dnf"], samples_sec=samples,
            )
        payload = out["payload"]
        last_payload = payload
        elapsed = payload.get("elapsed_sec")
        if elapsed is not None:
            samples.append(float(elapsed))
        rss = payload.get("peak_rss_bytes")
        if rss is not None:
            rss_samples.append(int(rss))

    if not samples or last_payload is None:
        return BenchResult(
            tool=tool, scenario=scenario, rows=rows, repeat=repeat,
            dnf="ERROR: no timing samples collected", samples_sec=samples,
        )

    median_sec = statistics.median(samples)
    p95_sec = _percentile(samples, 95)
    min_sec = min(samples)
    max_sec = max(samples)
    rps = (rows / median_sec) if median_sec > 0 else None

    res = BenchResult(
        tool=tool, scenario=scenario, rows=rows, repeat=repeat,
        elapsed_sec=median_sec,
        peak_rss_bytes=max(rss_samples) if rss_samples else None,
        rows_per_sec=rps,
        reported_missing_in_left=last_payload.get("reported_missing_in_left"),
        reported_missing_in_right=last_payload.get("reported_missing_in_right"),
        reported_changed_rows=last_payload.get("reported_changed_rows"),
        dnf=None,
        samples_sec=samples,
        median_sec=median_sec,
        p95_sec=p95_sec,
        min_sec=min_sec,
        max_sec=max_sec,
    )
    res.correct = _check_correct(res, gt)
    return res


def _parse_last_json_line(text: str) -> Optional[dict]:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def _check_correct(res: BenchResult, gt: GroundTruth) -> bool:
    """STRICT correctness check: the tool's reported counts must match the
    seeded ground truth in all three buckets.

    If a tool can only report a single "differing rows" total (no
    decomposition into changed vs side-only), its adapter must surface
    that as ``dnf="UNSUPPORTED: cannot decompose"`` rather than guess —
    a strict bucket-by-bucket comparison is the whole point of having
    a ground truth.
    """
    expected = (gt.missing_in_left, gt.missing_in_right, gt.changed_rows)
    actual = (
        res.reported_missing_in_left or 0,
        res.reported_missing_in_right or 0,
        res.reported_changed_rows or 0,
    )
    return actual == expected


def run_matrix(
    tools=("fastrecon", "datacompy", "data-diff", "pandas-merge", "pyspark",
           "polars", "duckdb-sql"),
    scenarios=("all_match", "small_mismatch", "large_mismatch", "precision_diff"),
    scales=(1_000_000,),
    *,
    keys: str = "id",
    timeout_sec: Optional[float] = None,
    data_dir: Path = DEFAULT_DATA_DIR,
    envs_dir: Path = DEFAULT_ENVS_DIR,
    repeat: int = 1,
):
    """Yield ``BenchResult`` for every (tool, scenario, scale) cell."""
    for scale in scales:
        for scenario in scenarios:
            for tool in tools:
                yield run_one(
                    tool, scenario, scale,
                    keys=keys, timeout_sec=timeout_sec,
                    data_dir=data_dir, envs_dir=envs_dir,
                    repeat=repeat,
                )
