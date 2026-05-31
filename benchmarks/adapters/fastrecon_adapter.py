"""Run fastrecon's ``compare`` and report counts.

Invoked via ``python -m benchmarks.adapters.fastrecon_adapter``.
"""

from __future__ import annotations

from typing import Dict, Optional

from ._base import run_adapter


def _compare(args) -> Dict[str, Optional[int]]:
    from fastrecon import ParquetFile, compare
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    res = compare(
        ParquetFile(args.left), ParquetFile(args.right),
        keys=keys, compare_mode="keyed",
    )
    return {
        "reported_missing_in_left": res.missing_in_left,
        "reported_missing_in_right": res.missing_in_right,
        "reported_changed_rows": res.changed_rows,
    }


if __name__ == "__main__":
    run_adapter(_compare)
