"""Row count comparison."""

from __future__ import annotations

from typing import Tuple

from ..engines import DuckDBEngine


def compare_row_counts(engine: DuckDBEngine, left_view: str, right_view: str) -> Tuple[int, int]:
    return engine.row_count(left_view), engine.row_count(right_view)
