# fastrecon

**A focused, high-performance reconciliation engine** for comparing SQL tables, SQL queries, CSV files, and Parquet files at scale. Built on **DuckDB**, **Polars**, and **Apache Arrow**.

> fastrecon is not a pandas replacement. It is a *reconciliation engine* — built specifically for proving that two datasets are (or aren't) the same.

## Why fastrecon

Most data teams hand-roll reconciliation with pandas, ad-hoc SQL, or shell scripts. None scale. fastrecon gives you one consistent API across every common combination:

| Left           | Right         |
| -------------- | ------------- |
| SQL table      | SQL table     |
| SQL table      | SQL query     |
| SQL query      | SQL query     |
| SQL table/query| CSV / Parquet |
| CSV / Parquet  | CSV / Parquet |

Everything is normalized into a single internal **relation** (a DuckDB view), then compared with pushdown-friendly SQL — no whole-dataset materialization in Python.

## Install

```bash
pip install fastrecon                  # core (all file formats + SQLite)
pip install "fastrecon[postgres]"      # + psycopg
pip install "fastrecon[mysql]"         # + pymysql (also covers MariaDB)
pip install "fastrecon[mssql]"         # + pyodbc
pip install "fastrecon[oracle]"        # + oracledb
pip install "fastrecon[snowflake]"     # + snowflake-sqlalchemy
pip install "fastrecon[redshift]"      # + redshift-connector
pip install "fastrecon[bigquery]"      # + sqlalchemy-bigquery
pip install "fastrecon[databricks]"    # + databricks-sqlalchemy
pip install "fastrecon[hana]"          # + sqlalchemy-hana
pip install "fastrecon[teradata]"      # + teradatasqlalchemy
pip install "fastrecon[all-databases]" # every operational + warehouse driver
```

Requires Python 3.9+.

## Documentation

Full documentation lives in [`docs/`](docs/index.md) and is structured as
a MkDocs Material site:

- [Getting started](docs/getting-started.md) — install + first compare
- [Database connectors](docs/databases.md) — all 12 supported backends
- [File formats](docs/files.md) — CSV, TSV, Parquet, JSON, Excel, fixed-width
- [Cloud storage](docs/cloud-storage.md) — `s3://`, `gs://`, `azure://` paths
- [Compare modes](docs/compare-modes.md) — keyed / profile / hash / partition
- [Configuration](docs/configuration.md) — normalization & tolerances
- [CLI reference](docs/cli.md)
- [API reference](docs/api-reference.md)
- [Recipes](docs/recipes.md) — common end-to-end workflows
- [Benchmarks](docs/benchmarks.md)

Build the site locally:

```bash
pip install "fastrecon[docs]"
mkdocs serve         # http://localhost:8000
mkdocs build         # static site in ./site
```

## Quick start

```python
from fastrecon import compare, SqlTable, ParquetFile

result = compare(
    left=SqlTable(conn="postgresql://user:pw@host/db", table="public.orders"),
    right=ParquetFile(path="orders.parquet"),
    keys=["order_id"],
    compare_mode="keyed",
    exclude_columns=["load_ts"],
    tolerances={"amount": 0.01},
)

print(result.summary())
print(result.to_json(indent=True))
```

Sample output:

```
status               : MISMATCH
compare_mode         : keyed
row_count_left       : 1,000,001
row_count_right      : 1,000,000
schema_match         : True
data_match           : False
missing_in_left      : 0
missing_in_right     : 1
changed_rows         : 4
duplicate_keys_left  : 0
duplicate_keys_right : 0
elapsed_sec          : 1.842
engine               : duckdb+polars
```

## Compare modes

| Mode       | What it does                                                    |
| ---------- | --------------------------------------------------------------- |
| `schema`   | Column names, types, missing/extra columns                      |
| `rowcount` | Schema + row counts on both sides                               |
| `keyed`    | Schema + counts + key-based diff (missing / changed / dup keys) |
| `profile`  | Schema + counts + per-column null/distinct/min/max              |
| `hash`     | Schema + counts + one whole-dataset checksum per side           |

`keyed` mode is the default and supports **partition-wise execution** for big-data workloads — see below. `hash` mode is the fastest path when you only need a yes/no answer (see "Hash & checksum compare").

## Partition-wise compare (big data)

Joining 100M+ rows in one shot is dangerous. `fastrecon` can split a keyed compare into independent partitions and aggregate the results. Each partition runs as its own filtered SQL job inside DuckDB, so memory stays bounded by the *partition* size, not the dataset size.

```python
from fastrecon import compare, SqlTable, ParquetFile, PartitionSpec

# Partition by a low-cardinality column (e.g. country, status, load_date)
result = compare(
    left=SqlTable(conn=SRC, table="orders"),
    right=ParquetFile("orders/*.parquet"),
    keys=["order_id"],
    partition=PartitionSpec(column="region", strategy="value"),
)

# Or hash-bucket any column (works for high-cardinality keys too)
result = compare(
    left=..., right=..., keys=["order_id"],
    partition=PartitionSpec(column="order_id", strategy="hash", buckets=64),
)

# Or explicit ranges (great for dates / sequential ids)
result = compare(
    left=..., right=..., keys=["order_id"],
    partition=PartitionSpec(
        column="order_dt", strategy="range",
        boundaries=[("2026-01-01", "2026-02-01"),
                    ("2026-02-01", "2026-03-01"),
                    ("2026-03-01", "2026-04-01")],
    ),
)

print(result.summary())
for p in result.column_stats["partitions"]:
    print(p)   # per-partition counts + match flag
```

### Strategies

| Strategy | Best for | Notes |
| -------- | -------- | ----- |
| `value`  | Low-cardinality partition keys (region, status, load_date) | Auto-discovers distinct values from both sides; capped by `max_partitions` (default 1000) |
| `hash`   | Any column, especially high-cardinality keys | `buckets=N` controls partition count and memory footprint |
| `range`  | Ordered columns (dates, sequential ids) | Half-open `[lo, hi)` boundaries; you supply them |

### What you get back

When you pass `partition=...`, the result includes a per-partition breakdown under `column_stats`:

```python
result.column_stats["partitioned_by"]
# {"column": "region", "strategy": "value", "n_partitions": 5}

result.column_stats["partitions"]
# [
#   {"partition": "EU", "row_count_left": 312_054, "row_count_right": 312_054,
#    "missing_in_left": 0, "missing_in_right": 0, "changed_rows": 2,
#    "duplicate_keys_left": 0, "duplicate_keys_right": 0, "match": False},
#   ...
# ]
```

Top-level counts (`missing_in_left`, `changed_rows`, etc.) are aggregated across partitions; `sample_mismatches` is a globally capped sample drawn from any partition.

### Choosing a strategy

- **You know the data has natural partitions** (`load_date`, `region`, `tenant_id`) → use `value`.
- **You don't, and just want bounded memory** → use `hash` with `buckets` ≈ `dataset_rows / 5_000_000`.
- **The data is time-series and you want to reconcile per window** → use `range` with date boundaries.

## Hash & checksum compare

Two complementary fast paths for the question "are these the same?":

**1. Whole-dataset checksum (`compare_mode="hash"`)** — one fingerprint per
side, single pass, no join. Fastest possible answer when you don't need to
know *which* rows differ:

```python
res = compare(left, right, keys=["order_id"], compare_mode="hash")
print(res.status)                       # MATCH or MISMATCH
print(res.column_stats["hash"])
# {'algo': 'xxhash64',
#  'left_checksum':  '8ac3…f12d',
#  'right_checksum': '8ac3…f12d',
#  'columns_hashed': ['name', 'amount', 'qty']}
```

The fingerprint is `bit_xor(hash(normalized_col1, ...))` aggregated per side,
so it's **order-independent** — re-sorting a file or swapping ETL load
order does not change the digest. Key columns are excluded by default
(pass `keys=None` to fingerprint the whole row instead). All
`ReconConfig` normalization (trim / case / decimal scale / timezone /
exclude_columns / column_mapping) is applied so a `hash`-mode MATCH means
the same thing as a `keyed`-mode MATCH on identical data.

**2. Per-row hash inside keyed mode (`ReconConfig.row_hash=True`)** — keeps
the per-row diff but replaces the per-column equality check with a single
64-bit integer compare after the join. Big win on wide tables (50+
columns); the only trade-off is that `sample_mismatches["changed"]` carries
just the keys of differing rows, not the per-column `__left/__right` values.

```python
cfg = ReconConfig(row_hash=True)
res = compare(left, right, keys=["order_id"], config=cfg)
```

CLI:

```bash
fastrecon compare --left ... --right ... --keys id --hash-only       # mode 1
fastrecon compare --left ... --right ... --keys id --row-hash        # mode 2
```

> **Algorithm.** The default fingerprint is DuckDB's built-in
> `hash()` — a 64-bit non-cryptographic hash in the same performance
> class as xxhash64. It runs entirely inside the engine with zero
> Python overhead. md5 / sha256 variants are deliberately not exposed
> here; if you need a cryptographic digest for audit, run a custom SQL
> via `SqlQuery`.

## Configuration & normalization

Reconciliation is mostly about handling the messy reality of "the same" data:

```python
from fastrecon import ReconConfig, compare

cfg = ReconConfig(
    trim_strings=True,
    case_sensitive=False,
    null_equals_empty=True,
    decimal_scale=2,
    timestamp_tz="UTC",
    column_mapping={"orderId": "order_id"},   # left -> right rename
    exclude_columns=["load_ts", "etl_batch"],
    tolerances={"amount": 0.01, "tax": 0.01},
    sample_limit=200,
)

result = compare(left, right, keys=["order_id"], config=cfg)
```

## Result object

`compare()` returns a `ReconResult` with:

- `status` — `MATCH` / `MISMATCH` / `ERROR`
- `row_count_left`, `row_count_right`
- `schema_match`, `data_match`, `schema_diff`
- `missing_in_left`, `missing_in_right`, `changed_rows`
- `duplicate_keys_left`, `duplicate_keys_right`
- `sample_mismatches` — sample rows for each mismatch class
- `column_stats` — populated in `profile` mode
- `execution_metrics` — `elapsed_sec`, `engine`

Use `result.summary()` for a printable report or `result.to_json()` / `result.to_dict()` to ship it to a logger, dashboard, or CI gate.

## Sources

```python
from fastrecon import (
    SqlTable, SqlQuery, PostgresSource,
    CsvFile, ParquetFile, JsonFile, ExcelFile, FixedWidthFile,
)

# --- SQL ---
SqlTable(conn="postgresql://...", table="schema.orders")
SqlQuery(conn="postgresql://...", query="SELECT * FROM orders WHERE dt >= '2026-01-01'")
PostgresSource(conn="postgresql://...", table="orders")   # native DuckDB scanner

# --- Files (local or cloud URL) ---
CsvFile("/path/to/orders.csv", options={"delim": ","})    # TSV: options={"delim": "\t"}
ParquetFile("/path/to/orders.parquet")                    # globs: 'data/*.parquet'
ParquetFile("data/year=2026/month=*/*.parquet")           # partitioned dataset
JsonFile("events.ndjson")                                 # NDJSON or top-level array
ExcelFile("workbook.xlsx", sheet="Q1")                    # specific sheet
FixedWidthFile("export.txt", columns=[
    ("id",     1,  5),
    ("name",   6, 10),
    ("amount", 16, 10),
], skip_rows=1)
```

### Coverage matrix

#### Database connections

Every operational database and cloud warehouse below works through
`SqlTable` / `SqlQuery` once you `pip install` the matching extra and
pass the SQLAlchemy URL. Views and materialized views are queryable as
tables — pass the view name to `SqlTable(table=...)`.

| Database               | Extra                           | URL prefix |
| ---------------------- | ------------------------------- | ---------- |
| PostgreSQL             | `[postgres]` (or `PostgresSource` for native scan) | `postgresql://` |
| MySQL                  | `[mysql]`                       | `mysql+pymysql://` |
| MariaDB                | `[mariadb]`                     | `mysql+pymysql://` |
| SQL Server             | `[mssql]`                       | `mssql+pyodbc://` |
| Oracle                 | `[oracle]`                      | `oracle+oracledb://` |
| SQLite                 | core (stdlib)                   | `sqlite:///` |
| Snowflake              | `[snowflake]`                   | `snowflake://` |
| Amazon Redshift        | `[redshift]`                    | `redshift+redshift_connector://` |
| Google BigQuery        | `[bigquery]`                    | `bigquery://` |
| Databricks SQL Warehouse | `[databricks]`                | `databricks://` |
| SAP HANA               | `[hana]`                        | `hana://` |
| Teradata               | `[teradata]`                    | `teradatasql://` |

#### File formats

| Format                  | Source class       | Notes |
| ----------------------- | ------------------ | ----- |
| CSV                     | `CsvFile`          | DuckDB `read_csv_auto`; pass `options=` for delim/quote/etc. |
| TSV                     | `CsvFile`          | `options={"delim": "\t"}` |
| Parquet (file or glob)  | `ParquetFile`      | Globs and partitioned datasets supported natively. |
| JSON / NDJSON           | `JsonFile`         | DuckDB `read_json_auto`. |
| Excel (.xlsx)           | `ExcelFile`        | Via DuckDB's bundled `excel` extension; one sheet per source. |
| Fixed-width             | `FixedWidthFile`   | Pass `columns=[(name, start, length), ...]`. |
| Avro                    | _follow-up_        | Tracked as a roadmap item. |
| ORC                     | _follow-up_        | Tracked as a roadmap item. |
| XML                     | _follow-up_        | Tracked as a roadmap item. |

#### Cloud storage / remote paths

DuckDB's `httpfs` extension is loaded on engine init, so any file source
above accepts a remote URL in place of a local path:

| Backend              | URL form                                        |
| -------------------- | ----------------------------------------------- |
| Local filesystem     | `/abs/path` or `./relative/path`                |
| Network share        | mount it, then use the local path               |
| AWS S3               | `s3://bucket/key.parquet` (creds via AWS env vars / `aws_*` DuckDB SECRETs) |
| Google Cloud Storage | `gs://bucket/key.parquet`                       |
| Azure Blob / ADLS    | `azure://container/key.parquet`                 |
| HTTPS                | `https://host/path/file.csv`                    |
| SFTP / FTP           | _follow-up_ — not natively supported by DuckDB. |

#### Query / source types

| Source type                       | How                                  |
| --------------------------------- | ------------------------------------ |
| SQL table                         | `SqlTable(conn=..., table="t")`      |
| SQL query                         | `SqlQuery(conn=..., query="...")`    |
| View                              | `SqlTable(conn=..., table="my_view")` |
| Materialized view                 | `SqlTable(conn=..., table="my_mv")`   |
| Stored procedure output           | `SqlQuery(conn=..., query="CALL my_proc(...)")` (driver-dependent) |
| File as table                     | any file source above                |
| Folder / dataset of files         | `ParquetFile("dir/*.parquet")` or `CsvFile("dir/*.csv")` |
| Partitioned Parquet dataset       | `ParquetFile("dir/year=*/month=*/*.parquet")` |

## Architecture

```
fastrecon/
├── api.py                  # public compare()
├── config.py               # ReconConfig
├── sources/                # SqlTable / SqlQuery / CsvFile / ParquetFile
├── engines/                # DuckDB execution engine
├── compare/                # schema / rowcount / keyed / profile
├── output/                 # ReconResult (summary, to_dict, to_json)
└── utils/                  # normalization, logging
```

Internally:

1. Each source is registered into an in-memory DuckDB connection as a view (zero-copy from Arrow when possible).
2. Schema is read with `DESCRIBE`.
3. Row counts, anti-joins, and inner joins run in DuckDB — no full Python materialization.
4. Mismatch samples are pulled lazily, capped by `sample_limit`.

## CLI

`fastrecon` ships with a first-class `typer`-built command-line interface — drop it into any CI pipeline:

```bash
fastrecon compare \
  --left  csv:./orders_today.csv \
  --right 'postgres:postgresql://u:p@h/db#public.orders' \
  --keys order_id \
  --tolerance amount=0.01 \
  --partition region:value \
  --report html:./report.html \
  --report junit:./report.xml \
  --fail-on mismatch
```

**Source URI grammar** (passed to `--left` / `--right`):

| URI                                                | Meaning                              |
| -------------------------------------------------- | ------------------------------------ |
| `csv:<path>`                                       | CSV file (paths may be `s3://`, `gs://`, `azure://`, `https://`) |
| `tsv:<path>`                                       | Tab-separated CSV shortcut           |
| `parquet:<path>`                                   | Parquet file or glob                 |
| `json:<path>`                                      | JSON / NDJSON file                   |
| `excel:<path>[#<sheet>]`                           | Excel `.xlsx` (optional sheet name)  |
| `fixedwidth:<path>#<name:start:len,...>`           | Fixed-width text file                |
| `sqltable:<sqlalchemy_url>#<table>`                | SQL table or view (any SQLAlchemy backend) |
| `sqlquery:<sqlalchemy_url>#<SELECT ...>`           | Arbitrary SQL                        |
| `postgres:<sqlalchemy_url>#<table>`                | Native DuckDB postgres_scanner       |
| `postgres-query:<sqlalchemy_url>#<SELECT ...>`     | Native scanner with custom SQL       |

**Reports:** `--report <fmt>:<path>` is repeatable; supported formats are `html`, `junit`, `json`.

**Exit codes** (driven by `--fail-on {never,mismatch,error}`, default `mismatch`): `0` MATCH, `1` MISMATCH, `2` ERROR. The same semantics are exposed on `ReconResult.exit_code`.

Add `--verbose` to enable `rich`-formatted structured logging of source loads, partition timings, and report writes.

> **Backwards compatibility.** The legacy `--left-type/--left-path/--left-conn/...` flag set from 0.3.x still works and is hidden from `--help`.

## Use fastrecon in CI

The CLI returns a non-zero exit code on mismatch, so CI pipelines fail builds automatically. Both reports are uploaded as artifacts so engineers can inspect them after the fact.

### GitHub Actions

```yaml
name: nightly-recon
on:
  schedule: [{ cron: "0 6 * * *" }]
jobs:
  recon:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install 'fastrecon[postgres]'
      - name: Reconcile orders snapshot vs warehouse
        env:
          PG_URL: ${{ secrets.WAREHOUSE_URL }}
        run: |
          fastrecon compare \
            --left  parquet:./snapshots/orders.parquet \
            --right "postgres:${PG_URL}#public.orders" \
            --keys order_id \
            --tolerance amount=0.01 \
            --partition region:value \
            --report html:./recon.html \
            --report junit:./recon.xml \
            --fail-on mismatch
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: recon-report
          path: |
            recon.html
            recon.xml
      - if: always()
        uses: mikepenz/action-junit-report@v4
        with:
          report_paths: recon.xml
```

### GitLab CI

```yaml
recon:
  image: python:3.11-slim
  script:
    - pip install 'fastrecon[postgres]'
    - |
      fastrecon compare \
        --left  "parquet:./snapshots/orders.parquet" \
        --right "postgres:${WAREHOUSE_URL}#public.orders" \
        --keys order_id \
        --tolerance amount=0.01 \
        --partition region:value \
        --report html:./recon.html \
        --report junit:./recon.xml \
        --fail-on mismatch
  artifacts:
    when: always
    paths: [recon.html]
    reports:
      junit: recon.xml
```

## Reports

Self-contained HTML and JUnit XML reports — no template engine, no external assets, perfect for emailing or attaching to a CI build:

```python
res = compare(left, right, keys=["id"])
res.to_html("report.html")           # standalone HTML, embeddable in CI artifacts
res.to_junit("report.xml")           # JUnit XML — Jenkins / GitLab / Buildkite read this natively
res.exit_code                        # 0 / 1 / 2 for shell scripts
```

The HTML report includes the summary, schema diff, per-partition heatmap (when partitioned), and tables of mismatch samples. The JUnit report emits one `<testcase>` per partition so dashboards pinpoint *which* slice failed.

## Streaming SQL loader & native Postgres scanner

Both SQL sources stream batches via a server-side cursor (Arrow `RecordBatchReader` → DuckDB), so you don't `fetchall()` 100M rows into Python before doing anything useful.

```python
SqlTable(conn=URL, table="orders", chunk_size=200_000)   # batch size
SqlQuery(conn=URL, query="SELECT ...", chunk_size=200_000)

# Opt out for drivers that don't support server-side cursors:
SqlTable(conn=URL, table="orders", streaming=False)
```

| When to use | Setting |
| ----------- | ------- |
| Default — large or unknown size | `streaming=True` (default), tune `chunk_size` |
| Tiny result set, want to avoid per-batch overhead | `streaming=False` |
| Driver doesn't support `stream_results=True` | `streaming=False` |

For Postgres specifically, use `PostgresSource` to bypass SQLAlchemy entirely. DuckDB's native `postgres_scanner` extension talks libpq directly, pushes filters down, and zero-copies result batches into the engine:

```python
from fastrecon import PostgresSource, ParquetFile, compare

result = compare(
    left=PostgresSource(conn="postgresql://u:p@h/db", table="public.orders"),
    right=ParquetFile("orders.parquet"),
    keys=["order_id"],
)
```

Use `PostgresSource` whenever both speed and memory matter — it's the recommended path for production warehouses.

## Benchmarks

A reproducible head-to-head suite lives under [`benchmarks/`](./benchmarks).
fastrecon is benchmarked against four other tools at 10K / 1M / 10M / 100M
rows across four canonical reconciliation scenarios:

| Tool          | What it is                                                  |
| ------------- | ----------------------------------------------------------- |
| `fastrecon`   | This library (DuckDB + Arrow).                              |
| `datacompy`   | Capital One's pandas-bound recon library.                   |
| `data-diff`   | Datafold's now-maintenance-mode in-DB differ.               |
| `pandas-merge`| Hand-rolled `pd.merge(..., indicator=True)` baseline.       |
| `pyspark`     | Spark `local[*]` full-outer join (eqNullSafe).              |

Each tool runs in its own isolated venv. Every cell is run **multiple times**
(smoke=1, PR=3, nightly/full=5) and reported as `median (p95) · peak RSS · rows/sec · ✓`,
so the headline numbers aren't single-shot noise. Cells that OOM, time out,
or hit a missing dependency surface as `DNF` with a reason — never as a silent
zero.

- **Smoke (10K) reference table** is checked in at [`benchmarks/results/reference.md`](./benchmarks/results/reference.md).
- **PR (1M)** runs on every pull request; download the `bench-pr-1m` artifact.
- **Nightly (10M)** runs on cron and publishes to a static dashboard
  ([`benchmarks/dashboard/`](./benchmarks/dashboard)) on the `gh-pages` branch.
- **Full (100M)** is manual-trigger only — `datacompy` and `pandas-merge`
  typically OOM at this tier.

See [`docs/benchmarks.md`](./docs/benchmarks.md) for the full methodology,
metric definitions, and instructions for reproducing locally.

## Roadmap

- ✅ MVP: package, sources, schema/rowcount/keyed/profile compare, JSON result, tests
- ✅ Partition-wise compare (value / hash / range strategies)
- ✅ Streaming SQL loader (Arrow `RecordBatchReader`)
- ✅ Native Postgres scanner via DuckDB `postgres` extension
- ✅ HTML + JUnit XML reports + CLI with exit codes
- ✅ Hash / checksum compare mode + per-row hash opt-in for keyed mode
- ✅ File sources: CSV, TSV, Parquet (incl. globs / partitioned), JSON, Excel, fixed-width
- ✅ Cloud storage URLs (S3, GCS, Azure, HTTPS) via DuckDB `httpfs`
- ✅ Database extras for Postgres, MySQL, MariaDB, SQL Server, Oracle, SQLite, Snowflake, Redshift, BigQuery, Databricks, SAP HANA, Teradata
- ⏳ Avro / ORC / XML file sources
- ⏳ SFTP / FTP / SMB transports (via fsspec)
- ✅ Multi-tool benchmark suite (`datacompy`, `data-diff`, `pandas-merge`, `pyspark`)
- ✅ Multi-sample timing with median / p95 / per-sample CI spread summary
- ✅ Static benchmark trend dashboard (Chart.js, published to `gh-pages`)
- ⏳ Parallel partition execution (thread pool)
- ⏳ Snowflake / BigQuery / Delta / Iceberg sources
- ⏳ Rust extension (PyO3) for hashing / normalization hot path

## License

MIT
