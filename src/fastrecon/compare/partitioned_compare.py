"""Partition-wise reconciliation for big data.

Splits the comparison into independent partitions so we never join the full
left × right datasets at once. Each partition runs the standard keyed
compare; per-partition results are aggregated.

Strategies
----------
``"value"``
    Group rows by ``column`` (and optionally ``right_column``) and run one
    compare per distinct value. Best when the column has bounded cardinality
    (e.g. ``country``, ``load_date``).

``"hash"``
    Bucket rows by ``hash(column) % buckets``. Works for any column
    (including the primary key) and bounds memory by ``buckets`` size.

``"range"``
    User-supplied list of ``(lo, hi)`` boundaries (half-open: ``lo <= col < hi``).
    Best for ordered columns like dates or sequential ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import ReconConfig
from ..engines import DuckDBEngine
from ..exceptions import CompareError
from ..utils.logging import get_logger
from ..utils.normalization import quote_ident
from .keyed_compare import keyed_compare

log = get_logger(__name__)


@dataclass
class PartitionSpec:
    """How to partition the comparison.

    Parameters
    ----------
    column : str
        Partition column on the *left* side. Use ``right_column`` if the
        right side has a different name (otherwise the same name is assumed).
    strategy : {"value", "hash", "range"}
    buckets : int
        Number of hash buckets when ``strategy="hash"``.
    boundaries : list[tuple]
        Half-open ``(lo, hi)`` pairs when ``strategy="range"``.
    right_column : str | None
        Right-side column name, defaults to ``column``.
    max_partitions : int | None
        Safety cap for ``strategy="value"``. ``None`` = no cap.
    """

    column: str
    strategy: str = "value"
    buckets: int = 16
    boundaries: List[Tuple[Any, Any]] = field(default_factory=list)
    right_column: Optional[str] = None
    max_partitions: Optional[int] = 1000

    def right(self) -> str:
        return self.right_column or self.column


@dataclass
class PartitionResult:
    partition: Any
    row_count_left: int = 0
    row_count_right: int = 0
    missing_in_left: int = 0
    missing_in_right: int = 0
    changed_rows: int = 0
    duplicate_keys_left: int = 0
    duplicate_keys_right: int = 0
    match: bool = True


@dataclass
class PartitionedCompareResult:
    partitions: List[PartitionResult] = field(default_factory=list)
    row_count_left: int = 0
    row_count_right: int = 0
    missing_in_left: int = 0
    missing_in_right: int = 0
    changed_rows: int = 0
    duplicate_keys_left: int = 0
    duplicate_keys_right: int = 0
    sample_missing_in_left: List[Dict[str, Any]] = field(default_factory=list)
    sample_missing_in_right: List[Dict[str, Any]] = field(default_factory=list)
    sample_changed: List[Dict[str, Any]] = field(default_factory=list)
    columns_compared: List[str] = field(default_factory=list)


def partitioned_compare(
    engine: DuckDBEngine,
    left_view: str,
    right_view: str,
    keys: List[str],
    common_columns: List[str],
    left_dtypes: Dict[str, str],
    right_dtypes: Dict[str, str],
    config: ReconConfig,
    spec: PartitionSpec,
    logical_types: Optional[Dict[str, str]] = None,
) -> PartitionedCompareResult:
    """Run a key-based compare partition-by-partition."""
    if not keys:
        raise CompareError("partitioned_compare requires `keys`")
    if spec.column not in left_dtypes:
        raise CompareError(f"Partition column {spec.column!r} not in left source")
    if spec.right() not in right_dtypes:
        raise CompareError(f"Partition column {spec.right()!r} not in right source")

    partitions = _enumerate_partitions(engine, left_view, right_view, spec)
    log.info("partitioned_compare: %d partition(s) using strategy=%s", len(partitions), spec.strategy)

    out = PartitionedCompareResult()
    sample_cap = max(int(config.sample_limit), 0)

    # Use unique view names per partition to avoid clobbering registered views.
    for idx, (label, left_filter, right_filter) in enumerate(partitions):
        plview = f"{left_view}__p{idx}"
        prview = f"{right_view}__p{idx}"
        engine.execute(
            f'CREATE OR REPLACE VIEW "{plview}" AS '
            f'SELECT * FROM "{left_view}" WHERE {left_filter}'
        )
        engine.execute(
            f'CREATE OR REPLACE VIEW "{prview}" AS '
            f'SELECT * FROM "{right_view}" WHERE {right_filter}'
        )

        try:
            kc = keyed_compare(
                engine, plview, prview, keys,
                common_columns, left_dtypes, right_dtypes, config,
                logical_types=logical_types,
            )
            lc = engine.row_count(plview)
            rc = engine.row_count(prview)
        finally:
            engine.execute(f'DROP VIEW IF EXISTS "{plview}"')
            engine.execute(f'DROP VIEW IF EXISTS "{prview}"')

        out.columns_compared = kc.columns_compared
        pr = PartitionResult(
            partition=label,
            row_count_left=lc,
            row_count_right=rc,
            missing_in_left=kc.missing_in_left,
            missing_in_right=kc.missing_in_right,
            changed_rows=kc.changed_rows,
            duplicate_keys_left=kc.duplicate_keys_left,
            duplicate_keys_right=kc.duplicate_keys_right,
            match=(
                kc.missing_in_left == 0
                and kc.missing_in_right == 0
                and kc.changed_rows == 0
                and kc.duplicate_keys_left == 0
                and kc.duplicate_keys_right == 0
                and lc == rc
            ),
        )
        out.partitions.append(pr)

        out.row_count_left += lc
        out.row_count_right += rc
        out.missing_in_left += kc.missing_in_left
        out.missing_in_right += kc.missing_in_right
        out.changed_rows += kc.changed_rows
        out.duplicate_keys_left += kc.duplicate_keys_left
        out.duplicate_keys_right += kc.duplicate_keys_right

        # Sample aggregation, capped globally
        _extend_capped(out.sample_missing_in_left, kc.sample_missing_in_left, sample_cap)
        _extend_capped(out.sample_missing_in_right, kc.sample_missing_in_right, sample_cap)
        _extend_capped(out.sample_changed, kc.sample_changed, sample_cap)

    return out


# --------------------------------------------------------------------- helpers
def _extend_capped(dst: list, src: list, cap: int) -> None:
    if cap <= 0 or len(dst) >= cap:
        return
    dst.extend(src[: cap - len(dst)])


def _sql_literal(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _enumerate_partitions(
    engine: DuckDBEngine,
    left_view: str,
    right_view: str,
    spec: PartitionSpec,
) -> List[Tuple[Any, str, str]]:
    """Return a list of ``(label, left_filter_sql, right_filter_sql)``."""
    lcol = quote_ident(spec.column)
    rcol = quote_ident(spec.right())

    if spec.strategy == "value":
        sql = (
            f"SELECT DISTINCT {lcol} AS v FROM \"{left_view}\" "
            f"UNION SELECT DISTINCT {rcol} AS v FROM \"{right_view}\" "
            f"ORDER BY 1 NULLS LAST"
        )
        rows = engine.fetchall(sql)
        if spec.max_partitions is not None and len(rows) > spec.max_partitions:
            raise CompareError(
                f"value partition strategy produced {len(rows)} partitions "
                f"(max_partitions={spec.max_partitions}). Use 'hash' for high-cardinality columns."
            )
        out = []
        for (v,) in rows:
            if v is None:
                out.append((None, f"{lcol} IS NULL", f"{rcol} IS NULL"))
            else:
                lit = _sql_literal(v)
                out.append((v, f"{lcol} = {lit}", f"{rcol} = {lit}"))
        return out

    if spec.strategy == "hash":
        n = max(int(spec.buckets), 1)
        return [
            (
                f"hash_bucket_{b}_of_{n}",
                f"abs(hash(CAST({lcol} AS VARCHAR))) % {n} = {b}",
                f"abs(hash(CAST({rcol} AS VARCHAR))) % {n} = {b}",
            )
            for b in range(n)
        ]

    if spec.strategy == "range":
        if not spec.boundaries:
            raise CompareError("range partition strategy requires `boundaries`")
        out = []
        for lo, hi in spec.boundaries:
            label = f"[{lo}, {hi})"
            out.append((
                label,
                f"{lcol} >= {_sql_literal(lo)} AND {lcol} < {_sql_literal(hi)}",
                f"{rcol} >= {_sql_literal(lo)} AND {rcol} < {_sql_literal(hi)}",
            ))
        return out

    raise CompareError(f"Unknown partition strategy: {spec.strategy!r}")
