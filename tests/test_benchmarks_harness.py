"""Unit tests for the benchmark harness *itself* (not the timings).

We don't shell out to datacompy / data-diff here — that's covered by
``benchmarks/test_benchmarks.py`` which is gated behind tier flags. This
module just verifies the wiring: dataset determinism, ground-truth math,
and that ``run_one`` happily invokes fastrecon on a tiny scale.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# benchmarks/ lives at the repo root (next to src/), not under src/.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.datasets import SCENARIOS, generate  # noqa: E402
from benchmarks.harness import _percentile, run_one  # noqa: E402
from benchmarks.result import BenchResult  # noqa: E402
from benchmarks.run_matrix import _fmt_cell, _markdown_table  # noqa: E402


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_dataset_generation_is_deterministic(tmp_path, scenario):
    a1, b1, gt1 = generate(scenario, 200, tmp_path / "run1")
    a2, b2, gt2 = generate(scenario, 200, tmp_path / "run2")
    assert a1.read_bytes() == a2.read_bytes()
    assert b1.read_bytes() == b2.read_bytes()
    assert gt1 == gt2


def test_ground_truth_matches_scenario_intent(tmp_path):
    _, _, gt = generate("all_match", 500, tmp_path / "am")
    assert gt == gt.__class__(500, 500, 0, 0, 0)

    # large_mismatch: id%20==0 over [0, 1000) → ids 0,20,...,980 = 50 rows
    _, _, gt = generate("large_mismatch", 1000, tmp_path / "lm")
    assert gt.changed_rows == 50
    assert (gt.missing_in_left, gt.missing_in_right) == (0, 0)

    # precision_diff: every row drifts on right
    _, _, gt = generate("precision_diff", 300, tmp_path / "pd")
    assert gt == gt.__class__(300, 300, 0, 0, 300)

    # small_mismatch @ 10000:
    #   id%5000==1 → ids {1, 5001} → drop_right=2
    #   id%5000==2 → ids {2, 5002} → drop_left=2
    #   id%1000==0 AND id%5000 ∉ {1,2} → all 10 (id ∈ {0,1000,...,9000}) since
    #   none of those have id%5000 in {1,2}.
    _, _, gt = generate("small_mismatch", 10_000, tmp_path / "sm")
    assert gt.missing_in_left == 2
    assert gt.missing_in_right == 2
    assert gt.changed_rows == 10
    assert gt.rows_left == 10_000 - 2
    assert gt.rows_right == 10_000 - 2


def test_run_one_against_fastrecon_smoke(tmp_path):
    res = run_one("fastrecon", "small_mismatch", 500, data_dir=tmp_path)
    assert isinstance(res, BenchResult)
    if res.is_dnf:
        pytest.skip(f"adapter DNF in this env: {res.dnf}")
    assert res.elapsed_sec is not None and res.elapsed_sec >= 0
    assert res.peak_rss_bytes is not None and res.peak_rss_bytes > 0
    assert res.correct is True
    # repeat defaults to 1: stats degenerate to the single sample.
    assert res.repeat == 1
    assert len(res.samples_sec) == 1
    assert res.median_sec == res.p95_sec == res.min_sec == res.max_sec
    assert res.elapsed_sec == res.median_sec


def test_run_one_repeat_collects_samples_and_stats(tmp_path):
    res = run_one("fastrecon", "all_match", 500, data_dir=tmp_path, repeat=3)
    if res.is_dnf:
        pytest.skip(f"adapter DNF in this env: {res.dnf}")
    assert res.repeat == 3
    assert len(res.samples_sec) == 3
    assert res.min_sec <= res.median_sec <= res.max_sec
    assert res.median_sec <= res.p95_sec <= res.max_sec
    assert res.elapsed_sec == res.median_sec
    # rows_per_sec is derived from the median, not any individual sample.
    assert res.rows_per_sec == pytest.approx(500 / res.median_sec)
    # Cell formatter shows median and p95 for visible spread.
    cell = _fmt_cell(res)
    assert "p95" in cell and "✓" in cell


def test_percentile_helper():
    assert _percentile([1.0], 95) == 1.0
    assert _percentile([1.0, 2.0, 3.0], 50) == 2.0
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 95) == 5.0
    # Unsorted input is fine.
    assert _percentile([3.0, 1.0, 2.0], 50) == 2.0


def test_markdown_table_marks_dnf(tmp_path):
    results = [
        BenchResult(tool="datacompy", scenario="all_match", rows=1_000_000,
                    dnf="OOM"),
        BenchResult(tool="fastrecon", scenario="all_match", rows=1_000_000,
                    elapsed_sec=2.5, peak_rss_bytes=500_000_000,
                    rows_per_sec=400_000.0, correct=True),
    ]
    md = _markdown_table(results, [1_000_000])
    assert "DNF (OOM)" in md
    assert "fastrecon" in md and "datacompy" in md
