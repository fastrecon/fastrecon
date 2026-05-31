"""Schema comparison."""

from __future__ import annotations

from typing import Dict, Optional

from ..config import ReconConfig
from ..types import SchemaDiff


def compare_schemas(
    left: Dict[str, str],
    right: Dict[str, str],
    config: ReconConfig,
    logical_left: Optional[Dict[str, str]] = None,
    logical_right: Optional[Dict[str, str]] = None,
) -> SchemaDiff:
    excluded = set(config.exclude_columns)
    left_cols = {c: t for c, t in left.items() if c not in excluded}
    right_cols = {c: t for c, t in right.items() if c not in excluded}

    # Apply user-supplied column mapping (left -> right) before diffing.
    # Mirror the rename onto the inferred logical types so the per-column
    # logical lookup uses the right-side names.
    if config.column_mapping:
        renamed = {}
        for c, t in left_cols.items():
            renamed[config.column_mapping.get(c, c)] = t
        left_cols = renamed
        if logical_left:
            logical_left = {
                config.column_mapping.get(c, c): t for c, t in logical_left.items()
            }

    missing_in_right = sorted(set(left_cols) - set(right_cols))
    missing_in_left = sorted(set(right_cols) - set(left_cols))
    common = sorted(set(left_cols) & set(right_cols))

    type_mismatches: Dict[str, Dict[str, str]] = {}
    for c in common:
        if _normalize_type(left_cols[c]) != _normalize_type(right_cols[c]):
            type_mismatches[c] = {"left": left_cols[c], "right": right_cols[c]}

    logical_type_mismatches: Dict[str, Dict[str, str]] = {}
    if logical_left and logical_right:
        for c in common:
            ll = logical_left.get(c)
            lr = logical_right.get(c)
            if ll and lr and ll != lr and "null" not in (ll, lr):
                # ``null`` (all-NULL column) is treated as compatible with
                # anything — we have no evidence to disagree.
                logical_type_mismatches[c] = {"left": ll, "right": lr}

    # Schema match logic depends on whether logical inference ran:
    #   - Inference ON  → trust logical types. Ignore physical-dtype
    #     noise (BIGINT vs INTEGER, VARCHAR-of-numbers vs INT) so the
    #     match flag reflects what the data actually agrees on.
    #   - Inference OFF → fall back to physical ``type_mismatches`` so
    #     ``ReconConfig(infer_logical_types=False)`` continues to be
    #     strict, as advertised. Without this fallback, disabling
    #     inference would silently treat every physical-dtype drift as
    #     a match.
    inference_ran = bool(logical_left) and bool(logical_right)
    drift = logical_type_mismatches if inference_ran else type_mismatches
    match = not missing_in_left and not missing_in_right and not drift
    return SchemaDiff(
        match=match,
        missing_in_left=missing_in_left,
        missing_in_right=missing_in_right,
        type_mismatches=type_mismatches,
        common_columns=common,
        logical_left=dict(logical_left or {}),
        logical_right=dict(logical_right or {}),
        logical_type_mismatches=logical_type_mismatches,
    )


# Loose type normalization so ``BIGINT`` vs ``INTEGER`` etc. don't trip the diff
# in Mode 1 (schema only). Numeric types collapse to "number"; strings collapse
# to "string"; everything else compared lowercased verbatim.
_NUMERIC = {"tinyint", "smallint", "integer", "int", "bigint", "hugeint", "double", "real", "float"}
_STRING = {"varchar", "text", "string", "char"}


def _normalize_type(t: str) -> str:
    base = t.lower().split("(")[0].strip()
    if base in _NUMERIC:
        return "number"
    if base in _STRING:
        return "string"
    if "decimal" in base or "numeric" in base:
        return "number"
    return base
