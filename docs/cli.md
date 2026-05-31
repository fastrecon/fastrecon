# CLI reference

The `fastrecon` command-line tool is a thin wrapper around the Python
API. It's the easiest way to drop reconciliations into shell pipelines,
cron jobs, and CI gates.

```bash
fastrecon compare --left <uri> --right <uri> [options]
```

## Source URI grammar

Every `--left` / `--right` value is a URI of the form `<kind>:<rest>`.

| URI                                                | Meaning                                              |
| -------------------------------------------------- | ---------------------------------------------------- |
| `csv:<path>`                                       | CSV file. Path may be `s3://`, `gs://`, `azure://`, `https://`. |
| `tsv:<path>`                                       | Tab-separated CSV shortcut.                          |
| `parquet:<path>`                                   | Parquet file or glob.                                |
| `json:<path>`                                      | JSON / NDJSON file.                                  |
| `excel:<path>[#<sheet>]`                           | Excel `.xlsx`. Optional sheet name after `#`.        |
| `fixedwidth:<path>#<name:start:len>,<name:start:len>,...` | Fixed-width text file.                        |
| `sqltable:<sqlalchemy_url>#<table>`                | SQL table or view (any SQLAlchemy backend).          |
| `sqlquery:<sqlalchemy_url>#<SELECT ...>`           | Arbitrary SQL.                                       |
| `postgres:<sqlalchemy_url>#<table>`                | Native DuckDB postgres_scanner.                      |
| `postgres-query:<sqlalchemy_url>#<SELECT ...>`     | Native scanner with custom SQL.                      |

The split between URL and tail uses the **last** `#`, so SQL queries
containing `#` (e.g. table comments) are not affected.

## Common options

| Flag                              | Default       | Purpose |
| --------------------------------- | ------------- | ------- |
| `--keys col1,col2`                | _(none)_      | Required for `keyed` mode. Comma-separated. |
| `--mode {keyed,profile,hash}`     | `keyed`       | Compare strategy. See [Compare modes](compare-modes.md). |
| `--hash-only`                     | _off_         | Shortcut for `--mode hash`. |
| `--row-hash`                      | _off_         | Per-row hash inside keyed mode (wide tables). |
| `--decimal-tolerance 0.01`        | `0.0`         | Numeric tolerance. |
| `--trim-strings`                  | _off_         | Trim string columns before compare. |
| `--case-insensitive`              | _off_         | Fold strings to uppercase before compare. |
| `--ignore col1,col2`              | _none_        | Columns to skip during value compare. |
| `--map left_col=right_col`        | _none_        | Repeatable. Rename a right-side column. |
| `--sample-size 20`                | `20`          | Cap on sample-mismatch rows per bucket. |
| `--report html:<path>`            | _none_        | Repeatable. Formats: `html`, `junit`, `json`. |
| `--fail-on {never,mismatch,error}` | `mismatch`   | Drives the process exit code. |
| `--quiet`                         | _off_         | Suppress the printed summary. |

## Exit codes

| Code | Meaning                                |
| ---- | -------------------------------------- |
| `0`  | MATCH (and any errors didn't trigger). |
| `1`  | MISMATCH (under default `fail-on=mismatch`). |
| `2`  | Source / connection / engine error.    |

## Worked examples

### Two CSV files, keyed

```bash
fastrecon compare \
  --left  csv:exports/left.csv \
  --right csv:exports/right.csv \
  --keys order_id \
  --decimal-tolerance 0.01 \
  --report html:report.html
```

### Postgres view vs Parquet on S3

```bash
fastrecon compare \
  --left  "sqltable:postgresql://prod/...#public.daily_orders_v" \
  --right "parquet:s3://exports/daily_orders/2026-01-15.parquet" \
  --keys order_id
```

### Snowflake query vs BigQuery query

```bash
fastrecon compare \
  --left  "sqlquery:snowflake://...#SELECT * FROM ORDERS" \
  --right "sqlquery:bigquery://project/dataset#SELECT * FROM orders" \
  --keys order_id \
  --case-insensitive \
  --decimal-tolerance 0.01 \
  --report junit:recon.xml
```

### Whole-file checksum

```bash
fastrecon compare \
  --left  parquet:exports/2026-01-15.parquet \
  --right parquet:s3://archive/2026-01-15.parquet \
  --keys order_id \
  --hash-only
```

### Fixed-width export

```bash
fastrecon compare \
  --left  "fixedwidth:legacy.txt#account:1:10,name:11:30,amount:41:15" \
  --right "sqltable:postgresql://...#public.accounts" \
  --keys account
```

### CI gate (GitHub Actions)

```yaml
- name: Reconcile staging vs prod
  run: |
    fastrecon compare \
      --left  "sqltable:postgresql://prod/...#orders" \
      --right "sqltable:postgresql://staging/...#orders" \
      --keys order_id \
      --report junit:recon.xml \
      --fail-on mismatch

- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: recon-report
    path: recon.xml
```

The non-zero exit code on MISMATCH stops the pipeline; the JUnit XML
gets surfaced in the build's test view.
