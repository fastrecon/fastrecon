"""pytest-benchmark integration.

Run the matrix through pytest so the standard CI tooling (JUnit XML,
pytest-benchmark JSON, parameter IDs) "just works":

    pytest benchmarks/test_benchmarks.py --benchmark-only \\
        -k "rows1000000 and fastrecon"

The fixtures are deterministic — first-run cost is dataset generation
(cached on disk), subsequent runs reuse the parquet pair.

Heavy scales (10M, 100M) are gated behind ``--bench-tier`` so a default
``pytest`` invocation only runs the cheap smoke tier. Tools that aren't
installed in the active env appear as DNF (skipped), not failures.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from .datasets import SCENARIOS
from .harness import ADAPTERS, run_one
from .result import BenchResult


# NOTE: ``--bench-tier`` is registered in benchmarks/conftest.py so the
# option is available before this module is imported.

_TIER_ROWS = {"smoke": 10_000, "pr": 1_000_000, "nightly": 10_000_000, "full": 100_000_000}


@pytest.fixture(scope="session")
def rows(request) -> int:
    return _TIER_ROWS[request.config.getoption("--bench-tier")]


def _tool_available(tool: str) -> bool:
    """A tool is "available" in this process iff its primary import works.

    For matrix runs in CI the harness uses per-tool venvs (so this check
    isn't needed), but for the simple in-process pytest path we skip
    cells whose tool isn't installed here.
    """
    if tool == "fastrecon":
        mod = "fastrecon"
    elif tool == "datacompy":
        mod = "datacompy"
    elif tool == "data-diff":
        mod = "data_diff"
    elif tool == "pandas-merge":
        mod = "pandas"
    elif tool == "pyspark":
        mod = "pyspark"
    elif tool == "polars":
        mod = "polars"
    elif tool == "duckdb-sql":
        mod = "duckdb"
    else:
        return False
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("tool", list(ADAPTERS))
def test_bench(benchmark, tool: str, scenario: str, rows: int) -> None:
    """Time one (tool, scenario) cell and assert correctness against ground truth.

    Uses pytest-benchmark's ``benchmark`` fixture for the timing record so
    results show up in pytest-benchmark's standard JSON / histograms.
    """
    if not _tool_available(tool):
        pytest.skip(f"{tool} not installed in this env (DNF: MISSING_DEP)")

    container: dict = {}

    def _go() -> BenchResult:
        return run_one(tool, scenario, rows)

    res: BenchResult = benchmark(_go)
    container["res"] = res
    if res.is_dnf:
        pytest.skip(f"DNF: {res.dnf}")
    assert res.correct is True, (
        f"{tool} reported wrong counts for {scenario}@{rows}: "
        f"missing_in_left={res.reported_missing_in_left}, "
        f"missing_in_right={res.reported_missing_in_right}, "
        f"changed={res.reported_changed_rows}"
    )
