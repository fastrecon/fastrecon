"""Shared type aliases and small data classes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

CompareMode = Literal["schema", "rowcount", "keyed", "profile", "hash"]
Status = Literal["MATCH", "MISMATCH", "ERROR"]


@dataclass
class ColumnInfo:
    name: str
    dtype: str
    nullable: bool = True


@dataclass
class SchemaDiff:
    match: bool
    missing_in_left: List[str] = field(default_factory=list)
    missing_in_right: List[str] = field(default_factory=list)
    type_mismatches: Dict[str, Dict[str, str]] = field(default_factory=dict)
    """Physical-type drift after loose normalization (BIGINT vs INTEGER
    collapse to one bucket). See ``logical_type_mismatches`` for the
    data-driven variant that catches stringly-typed CSV columns."""
    common_columns: List[str] = field(default_factory=list)
    logical_left: Dict[str, str] = field(default_factory=dict)
    """Per-column logical type inferred from data (``integer``,
    ``decimal``, ``date``, ``timestamp``, ``bool``, ``text``, ``null``).
    Empty when ``ReconConfig.infer_logical_types`` is disabled."""
    logical_right: Dict[str, str] = field(default_factory=dict)
    logical_type_mismatches: Dict[str, Dict[str, str]] = field(default_factory=dict)
    """Columns where the two sides disagree on logical type even after
    looking at actual values. This is what users usually mean by 'the
    schemas don't match' — INT vs VARCHAR-of-numbers does NOT show up
    here (both are logical ``integer``); INT vs free-text DOES."""


@dataclass
class ExecutionMetrics:
    elapsed_sec: float = 0.0
    engine: str = "duckdb+polars"
    bytes_scanned: Optional[int] = None
