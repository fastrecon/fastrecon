# Compare modes

fastrecon supports four compare modes. Pick the one that matches what
you actually have in front of you.

| Mode      | Needs keys? | What it tells you                                  | When to use |
| --------- | ----------- | -------------------------------------------------- | ----------- |
| `keyed`   | yes         | Per-row matched / left-only / right-only / changed, sample of differences, per-column drift counts. | Default. Anytime you have stable keys. |
| `profile` | no          | Distribution-level stats per column: row counts, null counts, distinct counts, min/max, sum/avg for numerics. | Schema drift checks; pre-key-design exploration. |
| `hash`    | no          | A single 64-bit checksum per side. MATCH or MISMATCH, no per-row info. | Whole-file integrity at scale. |
| partition | yes         | Same as `keyed`, but compare runs per slice and the result is aggregated. | Multi-billion-row compares; bounded memory. |

## Keyed compare (default)

```python
import fastrecon as fr

result = fr.compare(left, right, keys=["order_id"])
```

You get back a `ReconResult` with:

- `status` — `"MATCH"` or `"MISMATCH"`
- `row_count_left`, `row_count_right`, `matched_rows`, `changed_rows`,
  `left_only_rows`, `right_only_rows`
- `column_stats` — per-column drift counts in the changed-row set
- `sample_mismatches` — bounded sample of `left_only`, `right_only`, and
  `changed` rows; the latter carry every key + every column's `__left`
  and `__right` value side-by-side

Multiple keys are supported:

```python
fr.compare(left, right, keys=["tenant_id", "order_id"])
```

### Per-row hashing for wide tables

When the payload has many columns and you only care *which* keys
differ, set `row_hash=True` in the config:

```python
fr.compare(
    left, right,
    keys=["order_id"],
    config=fr.ReconConfig(row_hash=True),
)
```

Internally the compare is one INNER JOIN with a single
`hash(...) IS DISTINCT FROM hash(...)` predicate instead of N
per-column equality checks. The trade-off: `sample_mismatches["changed"]`
carries only the keys (no per-column `__left` / `__right` values, since
the engine never computed per-column equality).

## Profile compare

When the two sides don't share a stable key — say a CSV from one vendor
and a Parquet export from another — keyed comparison is meaningless.
Profile mode compares column distributions instead:

```python
result = fr.compare(left, right, compare_mode="profile")
```

For each column on both sides you get: row count, null count, distinct
count, and (for numerics) min, max, sum, mean. The result's `status`
flips to `MISMATCH` when any profile metric differs by more than the
configured tolerance.

Use this for:

- Detecting schema drift after a vendor format change.
- Sanity-checking ETL output volumes day over day.
- Picking the right key column before switching to `keyed` mode.

## Hash / checksum compare

A single whole-dataset 64-bit checksum per side. Order-independent:
re-shuffling rows doesn't change the digest. No join, no sort, no
sample.

```python
result = fr.compare(left, right, compare_mode="hash", keys=["order_id"])
print(result.column_stats["hash"])
# {'algo': 'xxhash64', 'left_checksum': '...', 'right_checksum': '...',
#  'columns_hashed': ['amount', 'currency', 'status', ...]}
```

- `keys=` columns are excluded from the fingerprint by default — a
  recon-keyed dataset's identity is its non-key payload. Pass `keys=None`
  to fingerprint every column.
- Honors the same normalization (`trim_strings`, `case_insensitive`,
  `decimal_tolerance`, etc.) as `keyed` mode, so a hash MATCH is
  semantically equivalent to a keyed MATCH.
- The `algo` label is `"xxhash64"`. The implementation uses DuckDB's
  built-in `hash()` — a 64-bit non-cryptographic hash in the same
  performance class — running entirely inside the engine.

CLI shortcut:

```bash
fastrecon compare --left ... --right ... --hash-only
```

## Partition-wise compare

For datasets that don't fit comfortably in memory, partition the compare:

```python
from fastrecon import PartitionSpec

result = fr.compare(
    left, right,
    keys=["order_id"],
    partition=PartitionSpec(
        column="order_date",
        strategy="value",        # or "range" / "hash"
    ),
)
```

The compare runs once per partition slice, each slice's stats are
aggregated, and `sample_mismatches` is drawn proportionally across
slices. Strategies:

- **value** — one slice per distinct value of `column`. Best when the
  column has a small, bounded cardinality (date, region, tenant).
- **range** — equi-width buckets. Good for monotonic numeric columns
  (id ranges, timestamps).
- **hash** — `mod(hash(column), N)` buckets. Use when the column is
  high-cardinality but you want N evenly-balanced slices.

Partitioning is transparent to the result — you get a single
`ReconResult` back.

## Choosing a mode — quick guide

```text
Do you have stable keys on both sides?
├── Yes
│   ├── Both sides fit comfortably in memory? → keyed
│   ├── Wide payload, only need "which keys differ"? → keyed + row_hash=True
│   └── Multi-billion rows? → keyed + partition=PartitionSpec(...)
└── No
    ├── Need to know which columns drifted? → profile
    └── Just need MATCH/MISMATCH at scale? → hash
```
