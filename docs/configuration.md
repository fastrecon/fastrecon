# Configuration & normalization

`ReconConfig` controls how strict the compare is. Defaults are
intentionally **strict** — fastrecon flags every difference unless you
explicitly opt in to tolerating it.

```python
from fastrecon import ReconConfig

config = ReconConfig(
    trim_strings=False,
    case_insensitive=False,
    decimal_tolerance=0.0,
    timezone_aware=True,
    null_equals_null=True,
    ignore_columns=[],
    column_mapping={},
    row_hash=False,
    sample_size=20,
)
```

## Field reference

| Field                | Default | Effect |
| -------------------- | ------- | ------ |
| `trim_strings`       | `False` | If `True`, trim leading/trailing whitespace from every string column on both sides before comparing. |
| `case_insensitive`   | `False` | If `True`, fold strings to upper case before comparing. Applies to keys *and* values. |
| `decimal_tolerance`  | `0.0`   | Numeric values within `±tolerance` are treated as equal. Use for FX rounding (`0.01`) or floating-point fuzz (`1e-9`). |
| `timezone_aware`     | `True`  | If `True`, timestamps with offsets are normalized to UTC before comparing; if `False`, the wall-clock value is used as-is. |
| `null_equals_null`   | `True`  | When `True`, two `NULL`s on the same side of a key match (SQL `IS NOT DISTINCT FROM`). When `False`, any `NULL` on either side counts as a mismatch. |
| `ignore_columns`     | `[]`    | Columns excluded from value comparison entirely. Schema-only differences in these columns do not count as mismatches. |
| `column_mapping`     | `{}`    | `{"left_col": "right_col", ...}` — rename right-side columns to match the left. Useful when two systems disagree only on column names. |
| `row_hash`           | `False` | Keyed mode only. Compare entire rows via a single hash instead of column-by-column. See [Compare modes](compare-modes.md). |
| `sample_size`        | `20`    | Maximum number of rows captured in each `sample_mismatches` bucket (`left_only`, `right_only`, `changed`). |

## Recipes

### "FX rounding is fine, but everything else is strict"

```python
ReconConfig(decimal_tolerance=0.01)
```

### "Vendor sometimes uppercases, sometimes doesn't"

```python
ReconConfig(case_insensitive=True, trim_strings=True)
```

### "These columns drift constantly, ignore them"

```python
ReconConfig(ignore_columns=["loaded_at", "etl_run_id", "_modified"])
```

### "Same data, different column names"

```python
ReconConfig(column_mapping={
    "customer_id": "cust_id",
    "amount":      "value",
})
```

### "Treat NULLs as wildcards"

```python
ReconConfig(null_equals_null=False)  # any NULL is now a mismatch
```

### "Wide table, just tell me which keys changed"

```python
ReconConfig(row_hash=True)
```

### "Show me everything that changed, not just 20"

```python
ReconConfig(sample_size=10_000)
```

## Order of operations

Normalization is applied in this order, on both sides, in pure SQL
inside DuckDB (no Python in the hot path):

1. `column_mapping` — rename right-side columns.
2. `ignore_columns` — drop excluded columns from the projection.
3. `trim_strings` → `case_insensitive` — applied to every string column.
4. `timezone_aware` — convert timestamps to UTC if true.
5. `decimal_tolerance` — folded into the per-column equality predicate
   as `abs(left - right) <= tolerance`.
6. `null_equals_null` — controls whether `IS NOT DISTINCT FROM` or `=`
   is used for the equality.

The same normalization rules drive `keyed`, `profile`, and `hash` modes,
so a MATCH in one mode is semantically consistent with a MATCH in any
other.
