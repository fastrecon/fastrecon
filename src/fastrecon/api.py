"""Public ``compare()`` entry point."""

from __future__ import annotations

import time
import uuid
from typing import Iterable, List, Optional

from .compare import (
    PartitionSpec,
    compare_profiles,
    compare_row_counts,
    compare_schemas,
    hash_compare,
    keyed_compare,
    partitioned_compare,
)
from .config import ReconConfig
from .engines import DuckDBEngine
from .exceptions import CompareError, FastreconError
from .output.result import ReconResult
from .sources.base import Source
from .types import ExecutionMetrics
from .utils.logging import get_logger

log = get_logger(__name__)


def compare(
    left: Source,
    right: Source,
    keys: Optional[Iterable[str]] = None,
    compare_mode: str = "keyed",
    columns: Optional[List[str]] = None,
    exclude_columns: Optional[List[str]] = None,
    tolerances: Optional[dict] = None,
    chunk_size: Optional[int] = None,
    partition: Optional[PartitionSpec] = None,
    config: Optional[ReconConfig] = None,
) -> ReconResult:
    """Reconcile two sources.

    Parameters
    ----------
    left, right : Source
        Any combination of ``SqlTable``, ``SqlQuery``, ``CsvFile``, ``ParquetFile``.
    keys : list[str] | None
        Required for ``compare_mode="keyed"``. Column names that uniquely
        identify a row on the left side. Use ``ReconConfig.column_mapping`` if
        the right side names differ.
    compare_mode : {"schema", "rowcount", "names_only", "keyed", "sampled", "profile", "hash"}
        ``schema``      — column-level diff only (names + dtypes/logical types).
        ``rowcount``    — schema + row counts.
        ``names_only``  — column-name diff + row counts only. Skips the
                          logical-type inference pass entirely, so it's
                          the cheapest "are these the same shape?" check.
        ``keyed``       — schema + counts + key-based row diff (default).
                          Set ``ReconConfig.row_hash=True`` to use a per-row
                          64-bit hash compare instead of per-column equality
                          (much faster on wide tables).
        ``sampled``     — keyed compare on a random sample of
                          ``ReconConfig.sample_size_keyed`` keys from the
                          left side. Quick spot-check for huge tables.
                          Requires ``keys=...``.
        ``profile``     — schema + counts + per-column profile compare.
        ``hash``        — single whole-dataset checksum per side
                          (``bit_xor(hash(...))``). Fastest mode; reports
                          MATCH/MISMATCH and the two digests, no per-row
                          detail. Pass ``keys=...`` to exclude key columns
                          from the fingerprint.
    """
    cfg = (config or ReconConfig()).model_copy()
    if columns is not None:
        cfg.columns = list(columns)
    if exclude_columns is not None:
        cfg.exclude_columns = list(exclude_columns)
    if tolerances is not None:
        cfg.tolerances = dict(tolerances)
    if chunk_size is not None:
        cfg.chunk_size = chunk_size

    keys_list = list(keys) if keys else []
    started = time.perf_counter()
    engine = DuckDBEngine()
    result: ReconResult

    try:
        result = _run_compare(engine, left, right, keys_list, compare_mode, cfg, partition)
    except FastreconError as e:
        log.error("Reconciliation failed: %s", e)
        result = ReconResult(
            status="ERROR", error=str(e), compare_mode=compare_mode, keys=keys_list
        )
    except Exception as e:  # pragma: no cover - safety net
        log.exception("Unexpected reconciliation error")
        result = ReconResult(
            status="ERROR",
            error=f"{type(e).__name__}: {e}",
            compare_mode=compare_mode,
            keys=keys_list,
        )
    finally:
        engine.close()

    result.execution_metrics = ExecutionMetrics(
        elapsed_sec=round(time.perf_counter() - started, 6),
        engine="duckdb+polars",
    )
    return result


def _run_compare(
    engine: DuckDBEngine,
    left: Source,
    right: Source,
    keys_list: List[str],
    compare_mode: str,
    cfg: ReconConfig,
    partition: Optional[PartitionSpec] = None,
) -> ReconResult:
    suffix = uuid.uuid4().hex[:8]
    lview, rview = f"left_{suffix}", f"right_{suffix}"
    engine.register_source(left, lview)
    engine.register_source(right, rview)

    left_schema = engine.schema_dict(lview)
    right_schema = engine.schema_dict(rview)

    # Data-driven logical type inference: lets us treat a CSV column of
    # numbers stored as VARCHAR as the same thing as an INT column on
    # the other side, while still flagging genuine type drift like
    # "free-text vs integer". Skipped for ``names_only`` mode where the
    # caller has explicitly opted out of dtype checks.
    logical_left: dict = {}
    logical_right: dict = {}
    if cfg.infer_logical_types and compare_mode != "names_only":
        from .utils.type_inference import infer_logical_types
        try:
            logical_left = infer_logical_types(
                engine.con, lview, left_schema, cfg.infer_sample_size
            )
            logical_right = infer_logical_types(
                engine.con, rview, right_schema, cfg.infer_sample_size
            )
        except Exception as e:  # pragma: no cover - defensive
            log.warning("Logical type inference failed (%s); falling back to physical types", e)

    schema_diff = compare_schemas(
        left_schema, right_schema, cfg, logical_left, logical_right,
    )

    # Common logical type per column (both sides agree). Used by the
    # keyed comparator to pick a numeric/temporal cast when the
    # *physical* dtypes disagree but the data is the same shape.
    common_logical_types = {
        c: schema_diff.logical_left[c]
        for c in schema_diff.common_columns
        if schema_diff.logical_left.get(c) == schema_diff.logical_right.get(c)
        and schema_diff.logical_left.get(c) not in (None, "null", "text")
    }

    result = ReconResult(
        status="MATCH",
        schema_match=schema_diff.match,
        schema_diff=schema_diff,
        compare_mode=compare_mode,
        keys=keys_list,
    )

    if compare_mode == "schema":
        result.data_match = True
        return _finalize(result)

    result.row_count_left, result.row_count_right = compare_row_counts(engine, lview, rview)

    if compare_mode == "rowcount":
        result.data_match = result.row_count_left == result.row_count_right
        return _finalize(result)

    if compare_mode == "names_only":
        # Cheapest "same shape?" check: column names + row counts only.
        # Schema match here ignores dtype drift entirely (we never ran
        # inference in this mode), so callers explicitly asking for a
        # name-only check get exactly that.
        name_match = (
            not schema_diff.missing_in_left and not schema_diff.missing_in_right
        )
        result.schema_match = name_match
        result.data_match = result.row_count_left == result.row_count_right
        return _finalize(result)

    if compare_mode == "hash":
        hc = hash_compare(
            engine, lview, rview, schema_diff.common_columns,
            left_schema, right_schema, cfg, keys=keys_list,
        )
        result.data_match = hc.match
        result.column_stats = {
            "hash": {
                "algo": hc.algo,
                "left_checksum": hc.left_checksum,
                "right_checksum": hc.right_checksum,
                "columns_hashed": hc.columns_hashed,
            }
        }
        return _finalize(result)

    if compare_mode == "profile":
        result.column_stats = compare_profiles(engine, lview, rview, schema_diff.common_columns)
        result.data_match = result.row_count_left == result.row_count_right
        return _finalize(result)

    if compare_mode == "sampled":
        if not keys_list:
            raise CompareError("compare_mode='sampled' requires `keys`")
        # Fast-path: a sampled run on identical data is wasted work — the
        # whole-dataset fingerprint takes one streaming pass per side and
        # tells us MATCH definitively, no sampling needed. On disagreement
        # we still run the sampled compare so the caller gets per-row diffs
        # (on the sampled subset).
        _fp_diag_sampled = None
        if cfg.fast_path:
            fp_match, fp_info = _fast_path_check(
                engine, lview, rview, keys_list,
                schema_diff.common_columns, left_schema, right_schema, cfg,
                result.row_count_left, result.row_count_right,
            )
            if fp_match:
                result.data_match = True
                result.column_stats = {"fast_path": fp_info}
                return _finalize(result)
            # Mismatch — preserve the fingerprint diagnostic so callers
            # can see WHY fast-path declined (count diff, hash diff)
            # alongside the sampled-mode statistics below.
            _fp_diag_sampled = fp_info
        # Sample N distinct key tuples from the LEFT side, then filter
        # both views to rows whose key falls in that sample. The keyed
        # comparator runs unchanged on the filtered subviews, so all
        # mismatch reporting (missing/changed/duplicates) still works.
        from .utils.normalization import quote_ident as _q
        sample_n = max(1, int(cfg.sample_size_keyed))
        # Right side may have a different name for each key (per
        # ``column_mapping``). Build a parallel right-key list so the
        # filter on the right view uses the right's actual column names.
        right_keys_list = [cfg.column_mapping.get(k, k) for k in keys_list]
        # Identifier quoting via quote_ident protects against keys that
        # contain a literal '"' character — never f-string-interpolate
        # untrusted column names directly into SQL.
        left_key_cols = ", ".join(_q(k) for k in keys_list)
        sample_tbl = f"sample_keys_{suffix}"
        engine.con.execute(
            f'CREATE OR REPLACE TEMP TABLE {_q(sample_tbl)} AS '
            f'SELECT DISTINCT {left_key_cols} FROM {_q(lview)} USING SAMPLE {sample_n} ROWS'
        )
        # Use ``=`` (not IS NOT DISTINCT FROM) for the filter predicate
        # so NULL-keyed rows are excluded from sampled subviews. The
        # downstream keyed_compare also joins/anti-joins with ``=``,
        # so this keeps the two stages consistent — otherwise NULL-key
        # rows would appear in the filter, then disappear in the join,
        # and be falsely counted as missing on both sides.
        left_pred = " AND ".join(
            f't.{_q(k)} = s.{_q(k)}' for k in keys_list
        )
        right_pred = " AND ".join(
            f't.{_q(rk)} = s.{_q(lk)}' for lk, rk in zip(keys_list, right_keys_list)
        )
        lview_s = f"left_s_{suffix}"
        rview_s = f"right_s_{suffix}"
        engine.con.execute(
            f'CREATE OR REPLACE TEMP VIEW {_q(lview_s)} AS '
            f'SELECT t.* FROM {_q(lview)} t WHERE EXISTS '
            f'(SELECT 1 FROM {_q(sample_tbl)} s WHERE {left_pred})'
        )
        engine.con.execute(
            f'CREATE OR REPLACE TEMP VIEW {_q(rview_s)} AS '
            f'SELECT t.* FROM {_q(rview)} t WHERE EXISTS '
            f'(SELECT 1 FROM {_q(sample_tbl)} s WHERE {right_pred})'
        )
        # Re-count on the filtered subviews so the user sees how many
        # rows actually participated in the sampled compare, not the
        # full-table totals (which would be misleading).
        result.row_count_left, result.row_count_right = compare_row_counts(engine, lview_s, rview_s)
        kc = keyed_compare(
            engine, lview_s, rview_s, keys_list,
            schema_diff.common_columns, left_schema, right_schema, cfg,
            logical_types=common_logical_types,
        )
        result.missing_in_left = kc.missing_in_left
        result.missing_in_right = kc.missing_in_right
        result.changed_rows = kc.changed_rows
        result.duplicate_keys_left = kc.duplicate_keys_left
        result.duplicate_keys_right = kc.duplicate_keys_right
        result.sample_mismatches = {
            "missing_in_left": kc.sample_missing_in_left,
            "missing_in_right": kc.sample_missing_in_right,
            "changed": kc.sample_changed,
        }
        actual = engine.con.execute(
            f'SELECT COUNT(*) FROM {_q(sample_tbl)}'
        ).fetchone()[0]
        sampled_stats = {
            "sampled": {
                "requested_keys": sample_n,
                "actual_keys_sampled": actual,
                # Empty-sample callout: when the left view is empty (or
                # all keys are NULL), sampled compare reports MATCH on
                # zero filtered rows. Surface that explicitly so callers
                # can distinguish "nothing sampled" from "sample matched".
                "empty_sample": actual == 0,
            }
        }
        if _fp_diag_sampled is not None:
            # Symmetric with the keyed branch: keep the fast-path
            # diagnostic in column_stats so users can debug why the
            # short-circuit didn't fire.
            sampled_stats["fast_path"] = _fp_diag_sampled
        result.column_stats = sampled_stats
        result.data_match = (
            result.missing_in_left == 0
            and result.missing_in_right == 0
            and result.changed_rows == 0
            and result.duplicate_keys_left == 0
            and result.duplicate_keys_right == 0
            and result.row_count_left == result.row_count_right
        )
        return _finalize(result)

    if compare_mode == "keyed":
        if not keys_list:
            raise CompareError("compare_mode='keyed' requires `keys`")

        # Fast-path short-circuit: if both sides have the same row count
        # AND identical (bit_xor + sum) fingerprints over keys+values, we
        # can skip the duplicate scan, both anti-joins, and the per-column
        # diff entirely. Two streaming scans replace a multi-stage join
        # workload — the speedup grows linearly with row count and column
        # count. Falls through to the full path on any disagreement, so
        # mismatched data only pays the ~10% overhead of the extra scans.
        if cfg.fast_path and partition is None:
            fp_match, fp_info = _fast_path_check(
                engine, lview, rview, keys_list,
                schema_diff.common_columns, left_schema, right_schema, cfg,
                result.row_count_left, result.row_count_right,
            )
            if fp_match:
                result.data_match = True
                result.column_stats = {"fast_path": fp_info}
                return _finalize(result)
            # Inconclusive (counts differ, or hashes differ) — fall through
            # to the full keyed compare so we get per-row mismatch detail.
            # Stash the fingerprint info for diagnostics either way.
            _fp_diag = fp_info
        else:
            _fp_diag = None

        if partition is not None:
            pc = partitioned_compare(
                engine, lview, rview, keys_list,
                schema_diff.common_columns, left_schema, right_schema, cfg, partition,
                logical_types=common_logical_types,
            )
            result.missing_in_left = pc.missing_in_left
            result.missing_in_right = pc.missing_in_right
            result.changed_rows = pc.changed_rows
            result.duplicate_keys_left = pc.duplicate_keys_left
            result.duplicate_keys_right = pc.duplicate_keys_right
            result.sample_mismatches = {
                "missing_in_left": pc.sample_missing_in_left,
                "missing_in_right": pc.sample_missing_in_right,
                "changed": pc.sample_changed,
            }
            result.column_stats = {
                "partitioned_by": {
                    "column": partition.column,
                    "strategy": partition.strategy,
                    "n_partitions": len(pc.partitions),
                },
                "partitions": [
                    {
                        "partition": p.partition,
                        "row_count_left": p.row_count_left,
                        "row_count_right": p.row_count_right,
                        "missing_in_left": p.missing_in_left,
                        "missing_in_right": p.missing_in_right,
                        "changed_rows": p.changed_rows,
                        "duplicate_keys_left": p.duplicate_keys_left,
                        "duplicate_keys_right": p.duplicate_keys_right,
                        "match": p.match,
                    }
                    for p in pc.partitions
                ],
            }
        else:
            kc = keyed_compare(
                engine, lview, rview, keys_list,
                schema_diff.common_columns, left_schema, right_schema, cfg,
                logical_types=common_logical_types,
            )
            result.missing_in_left = kc.missing_in_left
            result.missing_in_right = kc.missing_in_right
            result.changed_rows = kc.changed_rows
            result.duplicate_keys_left = kc.duplicate_keys_left
            result.duplicate_keys_right = kc.duplicate_keys_right
            result.sample_mismatches = {
                "missing_in_left": kc.sample_missing_in_left,
                "missing_in_right": kc.sample_missing_in_right,
                "changed": kc.sample_changed,
            }

        result.data_match = (
            result.missing_in_left == 0
            and result.missing_in_right == 0
            and result.changed_rows == 0
            and result.duplicate_keys_left == 0
            and result.duplicate_keys_right == 0
            and result.row_count_left == result.row_count_right
        )
        if _fp_diag is not None:
            # Preserve the fingerprint diagnostic alongside any column_stats
            # the partitioned/keyed path may have set, so callers can see
            # *why* the fast path declined to short-circuit.
            existing = result.column_stats or {}
            existing["fast_path"] = _fp_diag
            result.column_stats = existing
        return _finalize(result)

    raise CompareError(f"Unknown compare_mode: {compare_mode!r}")


def _fast_path_check(
    engine: DuckDBEngine,
    lview: str,
    rview: str,
    keys: List[str],
    common_columns: List[str],
    left_dtypes: Dict[str, str],
    right_dtypes: Dict[str, str],
    cfg: ReconConfig,
    count_l: int,
    count_r: int,
) -> tuple:
    """Whole-dataset fingerprint comparison for the fast-path short-circuit.

    Returns ``(matched, info_dict)`` where ``matched`` is True only if
    the fingerprints prove the two views are identical (under the
    configured normalization rules). Any disagreement — different row
    count, different XOR digest, or different SUM digest — returns
    False, and the caller should fall through to the full per-row diff.

    Why three fingerprints, not one:
      - ``COUNT(*)`` rules out the trivial cardinality mismatch in O(1)
        (already computed by the caller, free).
      - ``bit_xor(hash(...))`` is order-independent (a re-sorted file
        still hashes the same) but a row appearing twice cancels out,
        so a duplicated row could XOR to the same digest as the
        deduplicated counterpart.
      - ``sum(hash(...))`` is also order-independent but *additive*, so
        duplicates DO change it. Combining both makes a collision
        require simultaneous agreement on two independent functions of
        the row multiset — effectively 2⁻¹²⁸.

    Hash inputs include the keys (so two datasets that swap which row
    gets which key still mismatch) and use the same per-column
    ``normalize_expr`` as ``keyed`` mode (so the answers stay consistent).
    """
    from .compare.hash_compare import _hash_args
    if count_l != count_r:
        return False, {
            "used": True,
            "matched": False,
            "reason": "row_count_diff",
            "row_count_left": count_l,
            "row_count_right": count_r,
        }

    # Build the column list: keys + (common non-excluded value cols).
    # We include keys in the hash so identical-payload-but-different-key
    # datasets are correctly flagged as mismatched.
    #
    # We walk LEFT columns by their actual left-side name (not via a
    # reverse map of column_mapping) and check each one's mapped right
    # name against the right schema and the common-columns set. This
    # avoids ambiguity when the user has both ``column_mapping={"a":"b"}``
    # AND a column literally named "b" on the left — a reverse map
    # would silently swallow the standalone "b" column. With this
    # iteration order, "a" maps to right-"b" and is included; left-"b"
    # would only be included if it ALSO has a right-side counterpart
    # (i.e. another column named "b" on right after mapping), which is
    # impossible if "a" is the only thing mapped to "b". Either way, no
    # column gets silently skipped or double-counted.
    excluded = set(cfg.exclude_columns)
    common_set = set(common_columns)  # right-side names per schema_compare
    allow = set(cfg.columns) if cfg.columns is not None else None
    # Process EXPLICITLY mapped left columns first so they always win
    # the right-side counterpart, even if an unmapped left column
    # happens to share the same name as the mapping target. Then walk
    # the remaining left columns. ``used_right`` prevents two left
    # columns from claiming the same right column (which would hash
    # the right column twice and produce a false mismatch).
    left_value_cols: List[str] = []
    right_value_cols: List[str] = []
    used_right: set = set(cfg.column_mapping.get(k, k) for k in keys)
    ordered_left = (
        [c for c in cfg.column_mapping if c in left_dtypes]
        + [c for c in left_dtypes if c not in cfg.column_mapping]
    )
    for lc in ordered_left:
        if lc in keys or lc in excluded:
            continue
        rc = cfg.column_mapping.get(lc, lc)
        if rc in used_right:
            continue
        if rc not in right_dtypes or rc not in common_set:
            continue
        if allow is not None and lc not in allow:
            continue
        left_value_cols.append(lc)
        right_value_cols.append(rc)
        used_right.add(rc)
    left_cols = list(keys) + left_value_cols
    right_cols = [cfg.column_mapping.get(k, k) for k in keys] + right_value_cols

    l_args = _hash_args(left_cols, left_dtypes, cfg)
    r_args = _hash_args(right_cols, right_dtypes, cfg)

    # SUM(hash(...)) on BIGINT promotes to HUGEINT in DuckDB so we don't
    # silently overflow on >~10M rows. Cast explicit for clarity.
    l_sql = (
        f'SELECT bit_xor(hash({l_args})) AS x, '
        f'CAST(SUM(hash({l_args})) AS HUGEINT) AS s '
        f'FROM "{lview}"'
    )
    r_sql = (
        f'SELECT bit_xor(hash({r_args})) AS x, '
        f'CAST(SUM(hash({r_args})) AS HUGEINT) AS s '
        f'FROM "{rview}"'
    )
    l_xor, l_sum = engine.fetchall(l_sql)[0]
    r_xor, r_sum = engine.fetchall(r_sql)[0]

    # Normalize NULLs (empty side) to 0 so (empty == empty) → MATCH.
    l_xor_i = int(l_xor) if l_xor is not None else 0
    r_xor_i = int(r_xor) if r_xor is not None else 0
    l_sum_i = int(l_sum) if l_sum is not None else 0
    r_sum_i = int(r_sum) if r_sum is not None else 0

    matched = (l_xor_i == r_xor_i) and (l_sum_i == r_sum_i)
    return matched, {
        "used": True,
        "matched": matched,
        "row_count": count_l,
        "left_xor": f"{l_xor_i:016x}",
        "right_xor": f"{r_xor_i:016x}",
        "left_sum": str(l_sum_i),
        "right_sum": str(r_sum_i),
        "columns_hashed": left_cols,
        "algo": "xxhash64",
    }


def _finalize(result: ReconResult) -> ReconResult:
    if not result.schema_match or not result.data_match:
        result.status = "MISMATCH"
    else:
        result.status = "MATCH"
    return result
