# API reference

Everything below is importable from the top-level `fastrecon` package.

```python
from fastrecon import (
    compare, hash_compare, source,
    ReconConfig, ReconResult, HashCompareResult, PartitionSpec,
    SqlTable, SqlQuery, PostgresSource,
    CsvFile, ParquetFile, JsonFile, ExcelFile, FixedWidthFile,
    XmlFile, AvroFile, OrcFile, MainframeFile,
    FastreconError, SourceError, CompareError,
)
```

## Top-level functions

### `compare(left, right, keys=None, *, compare_mode="keyed", config=None, partition=None) -> ReconResult`

Reconcile two sources.

- `left`, `right` — any `Source` instance.
- `keys` — list of column names. Required for `compare_mode="keyed"`;
  used as the row identity in `hash` mode (excluded from the
  fingerprint by default).
- `compare_mode` — `"keyed"` (default), `"profile"`, or `"hash"`.
- `config` — a `ReconConfig`. Defaults to a strict `ReconConfig()`.
- `partition` — optional `PartitionSpec` for partition-wise compare.

Returns a `ReconResult` (or `HashCompareResult` for hash mode — both
share the same `status` / `exit_code` / serialization API).

### `hash_compare(left, right, keys=None, *, config=None) -> HashCompareResult`

Convenience wrapper around `compare(..., compare_mode="hash")`.

### `source(path, *, fixed_width=False, mainframe=False, **kwargs) -> Source`

Pick the right `Source` subclass from the file extension and return it
already constructed. See [File formats → Auto-detect](files.md#auto-detect-sourcepath).

```python
from fastrecon import source, compare

compare(
    source("orders.parquet"),
    source("orders.xml", record_path="order"),
    keys=["order_id"],
)
```

Recognized extensions:
`.csv`, `.tsv`, `.psv`, `.txt`, `.dat`, `.parquet`, `.pq`,
`.json`, `.ndjson`, `.jsonl`, `.xml`, `.xlsx`, `.xls`, `.avro`, `.orc`.
For ambiguous `.dat` / `.txt` files pass `fixed_width=True` or
`mainframe=True`. Unknown extensions raise `SourceError`.

## Sources

All sources are dataclasses that subclass `Source` — they know how to
register themselves as a DuckDB view inside the engine. You instantiate
them and hand them to `compare`; you never call `register` yourself.

### SQL

| Class                          | Constructor                                                                  |
| ------------------------------ | ---------------------------------------------------------------------------- |
| `SqlTable(conn, table)`        | `conn`: SQLAlchemy URL or `Engine`. `table`: `"schema.table"` or view name.  |
| `SqlQuery(conn, query)`        | `conn` as above. `query`: any `SELECT` (or driver-specific `CALL` / `EXEC`). |
| `PostgresSource(conn, table)`  | Postgres-only. Uses DuckDB's bundled `postgres` extension for native scan.   |

### Files

| Class                                              | Notable kwargs |
| -------------------------------------------------- | -------------- |
| `CsvFile(path, options=None)`                      | `options` is forwarded to DuckDB `read_csv_auto`. |
| `ParquetFile(path)`                                | `path` may be a glob or partitioned-dataset glob. |
| `JsonFile(path, options=None)`                     | `options` is forwarded to `read_json_auto`. |
| `ExcelFile(path, sheet=None, has_header=True)`     | `sheet` defaults to the first sheet. |
| `FixedWidthFile(path, columns, skip_rows=0, trim_values=False, encoding="utf-8")` | `columns`: `list[(name, start_1_indexed, length)]`. |
| `XmlFile(path, record_path=".", columns={}, namespaces=None, encoding=None)` | XPath-driven flatten. Streaming when `record_path` is a simple step expression. |
| `AvroFile(path, batch_size=100000, flatten_unions=True)` | Schema is read from the file. |
| `OrcFile(path, columns=None)`                      | Optional column projection. |
| `MainframeFile(path, fields, record_length=None, encoding="cp037", skip_bytes=0, chunk_records=50000, trim=True)` | EBCDIC + COBOL `COMP-3`/zoned/binary. See [Files → MainframeFile](files.md#mainframefile-ebcdic-cobol-packed-decimal). |

`path` may be local or any URL the loaded DuckDB extensions support
(`s3://`, `gs://`, `azure://`, `https://`).

## Configuration

### `ReconConfig`

See [Configuration](configuration.md) for full field semantics.

```python
ReconConfig(
    trim_strings: bool = False,
    case_insensitive: bool = False,
    decimal_tolerance: float = 0.0,
    timezone_aware: bool = True,
    null_equals_null: bool = True,
    ignore_columns: list[str] = [],
    column_mapping: dict[str, str] = {},
    row_hash: bool = False,
    sample_size: int = 20,
    infer_logical_types: bool = True,   # data-driven type inference
    infer_sample_size: int = 10_000,    # rows sampled per side
)
```

`infer_logical_types` (new in 0.7.0) controls whether each side is
profiled to assign each column a *logical* type — `integer`, `decimal`,
`date`, `timestamp`, `bool`, `text`, or `null` — based on a sample of
the actual data, instead of trusting the raw declared dtype. When on
(the default), the schema diff and the keyed comparator both use the
logical type, so a CSV column of integers stored as `VARCHAR` matches
a real `INTEGER` on the other side. Set False for strict
physical-dtype checking, or to skip the profiling pass on extremely
large textual columns.

### `PartitionSpec`

```python
PartitionSpec(
    column: str,
    strategy: Literal["value", "range", "hash"] = "value",
    n_buckets: int | None = None,   # required for "hash" / "range"
)
```

See [Compare modes → Partition-wise compare](compare-modes.md#partition-wise-compare).

## Results

### `ReconResult`

Returned from `compare()` for `keyed` and `profile` modes.

Key attributes:

- `status: Literal["MATCH", "MISMATCH", "ERROR"]`
- `exit_code: int` — 0 / 1 / 2 by `fail-on=mismatch` semantics
- `compare_mode: str`
- `keys: list[str]`
- `row_count_left: int`, `row_count_right: int`
- `changed_rows: int`
- `missing_in_left: int`, `missing_in_right: int`
- `column_stats: dict[str, dict]` — per-column drift counts
- `sample_mismatches: dict[str, list[dict]]` — `"left_only"`,
  `"right_only"`, `"changed"` buckets
- `execution_metrics: dict` — `elapsed_sec`, `engine`

Methods:

- `summary() -> str` — printable, multi-line report
- `to_dict() -> dict`
- `to_json(path: str | Path) -> None`
- `to_html(path: str | Path) -> None`
- `to_junit(path: str | Path) -> None`

### `HashCompareResult`

Returned from `compare(..., compare_mode="hash")` and `hash_compare()`.
Same `status` / `exit_code` API; `column_stats["hash"]` carries
`{algo, left_checksum, right_checksum, columns_hashed}`. By design,
`sample_mismatches` is empty for this mode.

### `SchemaDiff`

Attached to every `ReconResult` as `result.schema_diff`. Captures both
the *physical* and *logical* type drift between the two sides:

| Field                     | Meaning                                                                 |
| ------------------------- | ----------------------------------------------------------------------- |
| `match`                   | True when no missing columns and no logical-type drift.                  |
| `missing_in_left`         | Columns present on the right but not the left.                           |
| `missing_in_right`        | Columns present on the left but not the right.                           |
| `common_columns`          | Columns shared by both sides (after `column_mapping`).                   |
| `type_mismatches`         | Raw physical-dtype drift (e.g. `INTEGER` vs `BIGINT`). Informational.    |
| `logical_left`            | `{col: logical_type}` inferred from left-side data.                      |
| `logical_right`           | `{col: logical_type}` inferred from right-side data.                     |
| `logical_type_mismatches` | Columns where the two sides disagree on logical type — real drift.       |

Logical types: `integer`, `decimal`, `date`, `timestamp`, `bool`,
`text`, `null` (all-NULL textual column — treated as compatible with
anything).

## Behavior notes

### Schema-aware cross-side dtype handling (0.7.0)

`compare()` now profiles a sample of each side's data to assign each
column a logical type, then uses that logical type to drive the
comparison instead of the raw physical dtype. Concretely:

- A `VARCHAR` column of integers (`"100"`, `" 200 "`, `""`) on one
  side compares numerically against an `INTEGER` column on the other —
  whitespace, leading zeros, and empty-string-as-NULL all do the right
  thing.
- A `VARCHAR` of free text vs an `INTEGER` column shows up as
  `logical_type_mismatches` in the schema diff and flips
  `schema_match` to False.
- MSSQL `INT` vs Postgres `BIGINT` is silent — both are logical
  `integer`, so it's not flagged. (The raw drift is still recorded in
  `schema_diff.type_mismatches` for inspection.)

When the logical types disagree on a numeric/temporal column, the
keyed comparator falls back to the pre-0.7 text comparison via
`NULLIF(TRY_CAST(... AS VARCHAR), '')` (introduced in 0.5.3), so
unparseable values become `NULL` instead of crashing the run.

### Optional install extras

| Extra            | Adds            | Required for                                |
| ---------------- | --------------- | ------------------------------------------- |
| `fastrecon[xml]` | `lxml`          | `XmlFile`                                   |
| `fastrecon[avro]`| `fastavro`      | `AvroFile`                                  |
| `fastrecon[orc]` | (none — uses pyarrow) | `OrcFile`                             |
| `fastrecon[mainframe]` | (none — pure Python) | `MainframeFile`                    |
| `fastrecon[all-files]` | `lxml + fastavro` | every file format above             |
| `fastrecon[postgres]` / `[mysql]` / `[mssql]` / `[oracle]` / … | DB driver | corresponding `SqlTable` / `SqlQuery` |
| `fastrecon[all-databases]` | every DB driver | one-shot DB install                |

## Exceptions

| Class            | Raised when                                                  |
| ---------------- | ------------------------------------------------------------ |
| `FastreconError` | Base class — catch this to handle any fastrecon-raised error. |
| `SourceError`    | A source failed to register (bad path, bad SQL, missing extension, missing driver, unknown extension passed to `source()`). |
| `CompareError`   | The compare itself failed (key column missing, schema mismatch, etc.). |

## Engine (advanced)

Most users never touch the engine directly. If you need to share a
single DuckDB connection across many compares — e.g. to register custom
secrets or load extra extensions — instantiate it explicitly:

```python
from fastrecon.engines.duckdb_engine import DuckDBEngine

eng = DuckDBEngine()
eng.execute("CREATE SECRET ...;")
# Pass the same engine to subsequent compare() calls (advanced API;
# see compare's keyword-only `engine=` parameter in source).
```
