"""Result types for the benchmark harness."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class BenchResult:
    """One (tool × scenario × scale) measurement.

    When a cell is run multiple times (``repeat > 1``), ``elapsed_sec`` is the
    **median** of the samples (not the mean), and the spread is captured in
    ``median_sec`` / ``p95_sec`` / ``min_sec`` / ``max_sec``. Every raw timing
    is kept in ``samples_sec`` so downstream tooling can recompute statistics
    or plot distributions.
    """
    tool: str
    scenario: str
    rows: int
    elapsed_sec: Optional[float] = None  # = median_sec, kept for back-compat
    peak_rss_bytes: Optional[int] = None  # max across samples
    rows_per_sec: Optional[float] = None  # derived from median_sec
    correct: Optional[bool] = None
    # Tool-reported counts; harness compares to GroundTruth
    reported_missing_in_left: Optional[int] = None
    reported_missing_in_right: Optional[int] = None
    reported_changed_rows: Optional[int] = None
    # DNF reasons: "OOM", "TIMEOUT", "ERROR: <msg>", "MISSING_DEP"
    dnf: Optional[str] = None
    # Sampling stats (populated by the harness when repeat > 1; with
    # repeat == 1 the median/p95/min/max all equal elapsed_sec).
    samples_sec: List[float] = field(default_factory=list)
    median_sec: Optional[float] = None
    p95_sec: Optional[float] = None
    min_sec: Optional[float] = None
    max_sec: Optional[float] = None
    repeat: int = 1
    extra: dict = field(default_factory=dict)

    @property
    def is_dnf(self) -> bool:
        return self.dnf is not None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)
