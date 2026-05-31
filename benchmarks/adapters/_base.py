"""Shared CLI entry-point for adapters.

Each adapter is invoked as a subprocess in its own virtualenv:

    python -m benchmarks.adapters.fastrecon_adapter \\
        --left   fixtures/x_left.parquet \\
        --right  fixtures/x_right.parquet \\
        --keys   id \\
        --rows   1000000 \\
        --scenario small_mismatch

It runs the tool's compare and emits **one JSON line** to stdout describing
the measurement (timing, peak RSS, mismatch counts). All other output goes
to stderr. Errors are reported as a JSON line with a ``dnf`` field set so
the harness can mark the run as DNF without aborting the suite.
"""

from __future__ import annotations

import argparse
import gc
import json
import resource
import sys
import time
import traceback
from typing import Callable, Dict, Optional


def _peak_rss_bytes() -> int:
    """resource.ru_maxrss is in KiB on Linux, bytes on macOS — normalize to bytes."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(rss)
    return int(rss) * 1024


def parse_argv() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--left", required=True)
    p.add_argument("--right", required=True)
    p.add_argument("--keys", default="id")
    p.add_argument("--rows", type=int, required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--tool", required=True)
    return p.parse_args()


def run_adapter(compare_fn: Callable[[argparse.Namespace], Dict[str, Optional[int]]]) -> None:
    """Time ``compare_fn`` and emit one JSON line on stdout. Handles all
    exceptions by emitting a DNF result (so the parent harness keeps going)."""
    args = parse_argv()
    out: Dict[str, object] = {
        "tool": args.tool, "scenario": args.scenario, "rows": args.rows,
        "elapsed_sec": None, "peak_rss_bytes": None, "rows_per_sec": None,
        "reported_missing_in_left": None,
        "reported_missing_in_right": None,
        "reported_changed_rows": None,
        "dnf": None,
    }
    try:
        gc.collect()
        t0 = time.perf_counter()
        counts = compare_fn(args)
        elapsed = time.perf_counter() - t0
        out["elapsed_sec"] = elapsed
        out["peak_rss_bytes"] = _peak_rss_bytes()
        out["rows_per_sec"] = (args.rows / elapsed) if elapsed > 0 else None
        for k in ("reported_missing_in_left", "reported_missing_in_right", "reported_changed_rows"):
            out[k] = counts.get(k)
    except MemoryError:
        out["dnf"] = "OOM"
    except ImportError as e:
        out["dnf"] = f"MISSING_DEP: {e}"
    except Exception as e:
        out["dnf"] = f"ERROR: {e.__class__.__name__}: {e}"
        traceback.print_exc(file=sys.stderr)

    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
