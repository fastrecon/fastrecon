"""Reconciliation configuration model."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


class ReconConfig(BaseModel):
    """Normalization and comparison rules.

    Pass an instance to ``compare(..., config=...)`` or rely on defaults.
    """

    model_config = ConfigDict(extra="forbid")

    # Normalization rules
    trim_strings: bool = False
    case_sensitive: bool = True
    null_equals_empty: bool = False
    decimal_scale: Optional[int] = None
    timestamp_tz: Optional[str] = None  # e.g. "UTC"

    # Tolerances per column (absolute)
    tolerances: Dict[str, float] = Field(default_factory=dict)

    # Column selection
    columns: Optional[List[str]] = None
    exclude_columns: List[str] = Field(default_factory=list)
    column_mapping: Dict[str, str] = Field(default_factory=dict)
    """Map left column name -> right column name when names differ."""

    ignore_column_order: bool = True

    # Sampling / limits
    sample_limit: int = 100
    """How many mismatch sample rows to include in the result."""

    sample_size_keyed: int = 1_000
    """Number of distinct keys to sample from the left side when
    ``compare_mode='sampled'``. The keyed comparison then runs only on
    rows whose key falls in that sample. Useful for quick spot-checks
    on huge tables where a full keyed compare would be too expensive."""

    chunk_size: Optional[int] = None
    """Reserved for future partitioned/chunked compare."""

    # Logical type inference
    infer_logical_types: bool = True
    """If True (default), profile a sample of each side's data to assign
    each column a *logical* type (integer, decimal, date, timestamp,
    bool, text). The schema diff then reports column-name and
    logical-type drift instead of physical-dtype noise (so BIGINT vs
    INTEGER no longer shows up, and a CSV column of integers stored as
    VARCHAR matches a real INTEGER column on the other side). Set False
    to skip the profiling pass — useful only when you want strict
    physical-dtype checking or have extremely large textual columns
    where even a 10k-row sample is too expensive."""
    infer_sample_size: int = 10_000
    """Rows sampled per side for logical type inference."""

    # Fast-path short-circuit
    fast_path: bool = True
    """If True (default), ``keyed`` and ``sampled`` modes first compute a
    cheap whole-dataset fingerprint per side — ``(row_count, bit_xor(hash(...)),
    sum(hash(...)))`` over keys+values in a single streaming pass — and
    skip the duplicate detection, anti-joins, and per-column diff entirely
    when the fingerprints match.

    On identical data this turns a multi-stage join workload into two
    parallel scans (≈10-100× speedup on multi-GB inputs). When the
    fingerprints differ the comparator falls through to the full path,
    so the only cost is two extra streaming scans (~10% overhead on
    mismatched data). The triple-fingerprint (count + bit_xor + sum)
    makes false-positive collisions astronomically unlikely (~2⁻¹²⁸).

    Set False to force the full per-column compare even when the
    datasets are identical (useful when you specifically want to see
    ``columns_compared`` populated, or for benchmarking)."""

    # Hash / checksum compare
    row_hash: bool = False
    """If True, ``keyed`` mode replaces per-column equality with a
    single per-row 64-bit hash compare after the join. Massively cheaper
    on wide tables (one integer compare instead of N column compares),
    at the cost of losing per-column ``__left/__right`` values in
    ``sample_mismatches['changed']`` — the sample only carries the keys
    of the rows that differ. Whole-dataset ``compare_mode='hash'``
    always uses this fingerprint regardless of this flag."""
