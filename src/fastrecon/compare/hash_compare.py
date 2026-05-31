"""Whole-dataset checksum comparison.

The fastest possible answer to "are these two datasets identical?".

We compute a single, order-independent fingerprint per side using
``bit_xor(hash(normalized_col1, normalized_col2, ...))`` over the common
columns, then compare the two BIGINT digests. XOR aggregation makes the
fingerprint independent of row order, so a re-sorted file still hashes
to the same value.

This mode does not produce per-row mismatch samples — by design. It
trades resolution for speed: one pass per side, no join, no shuffle.
For per-row diffs, use ``compare_mode="keyed"`` (optionally with
``ReconConfig.row_hash=True`` to keep the join cheap on wide tables).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..config import ReconConfig
from ..engines import DuckDBEngine
from ..utils.normalization import normalize_expr, quote_ident


@dataclass
class HashCompareResult:
    left_checksum: str
    right_checksum: str
    match: bool
    columns_hashed: List[str]
    algo: str = "xxhash64"


def _hash_args(cols: List[str], dtypes: Dict[str, str], cfg: ReconConfig) -> str:
    """Build the comma-separated argument list for DuckDB ``hash(...)``.

    Each column is normalized per ``ReconConfig`` (trim/case/decimal/tz)
    so the fingerprint reflects the same equivalence relation that
    ``keyed`` mode uses — otherwise "match" / "mismatch" answers from
    the two modes could disagree on the same data.
    """
    if not cols:
        # hash() needs at least one argument; fall back to a constant so
        # bit_xor still returns a deterministic per-row value.
        return "0::BIGINT"
    parts = [normalize_expr(c, dtypes.get(c, "varchar"), cfg) for c in cols]
    return ", ".join(parts)


def hash_compare(
    engine: DuckDBEngine,
    left_view: str,
    right_view: str,
    common_columns: List[str],
    left_dtypes: Dict[str, str],
    right_dtypes: Dict[str, str],
    config: ReconConfig,
    keys: Optional[List[str]] = None,
) -> HashCompareResult:
    """Compute one fingerprint per side and compare.

    ``keys`` are excluded from the hash by default when provided — a
    keyed dataset's identity is its non-key payload, and including the
    key would make every reordering of inserts visible as a "mismatch"
    even when row-content is identical. Pass ``keys=None`` (or an empty
    list) to fingerprint the entire row instead.
    """
    excluded = set(config.exclude_columns) | set(keys or [])
    cols = [c for c in common_columns if c not in excluded]
    if config.columns is not None:
        allow = set(config.columns)
        cols = [c for c in cols if c in allow]

    right_cols = [config.column_mapping.get(c, c) for c in cols]

    l_args = _hash_args(cols, left_dtypes, config)
    r_args = _hash_args(right_cols, right_dtypes, config)

    # bit_xor over hash(...) is order-independent and runs in a single
    # streaming pass per side.
    l_sql = f'SELECT bit_xor(hash({l_args})) FROM "{left_view}"'
    r_sql = f'SELECT bit_xor(hash({r_args})) FROM "{right_view}"'

    l_val = engine.fetchall(l_sql)[0][0]
    r_val = engine.fetchall(r_sql)[0][0]

    # Normalize NULL (empty side) to 0 for stable display.
    l_int = int(l_val) if l_val is not None else 0
    r_int = int(r_val) if r_val is not None else 0

    return HashCompareResult(
        left_checksum=f"{l_int:016x}",
        right_checksum=f"{r_int:016x}",
        match=(l_int == r_int),
        columns_hashed=cols,
        algo="xxhash64",
    )


def row_hash_expr(cols: List[str], dtypes: Dict[str, str], cfg: ReconConfig) -> str:
    """SQL expression that computes a per-row 64-bit hash.

    Used by ``keyed_compare`` when ``ReconConfig.row_hash=True`` to
    collapse a wide per-column compare into a single integer compare
    after the join — the win grows with column count.
    """
    args = _hash_args(cols, dtypes, cfg)
    return f"hash({args})"


__all__ = ["hash_compare", "HashCompareResult", "row_hash_expr", "quote_ident"]
