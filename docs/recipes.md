# Recipes

Common end-to-end workflows. Copy, paste, adjust.

## Daily ETL gate: warehouse vs source-of-truth

You've ETL'd `prod.public.orders` into `warehouse.PUBLIC.ORDERS`. Every
night, prove they agree on yesterday's window:

```python
import datetime as dt
import sys
import fastrecon as fr

yesterday = dt.date.today() - dt.timedelta(days=1)

result = fr.compare(
    fr.SqlQuery(
        conn="postgresql://prod/...",
        query=f"SELECT * FROM public.orders "
              f"WHERE order_date = DATE '{yesterday.isoformat()}'",
    ),
    fr.SqlQuery(
        conn="snowflake://...",
        query=f"SELECT * FROM PUBLIC.ORDERS "
              f"WHERE ORDER_DATE = DATE '{yesterday.isoformat()}'",
    ),
    keys=["order_id"],
    config=fr.ReconConfig(
        decimal_tolerance=0.01,
        case_insensitive=True,
        ignore_columns=["loaded_at", "etl_run_id"],
    ),
)
result.to_html(f"recon-{yesterday}.html")
result.to_junit(f"recon-{yesterday}.xml")
sys.exit(result.exit_code)
```

## Vendor-feed reconciliation: CSV vs database

A vendor sends a daily CSV; you've loaded it into a staging table.
Prove the load was lossless:

```python
result = fr.compare(
    fr.CsvFile(
        "s3://vendor-drops/2026-01-15.csv",
        options={"delim": "|", "header": True},
    ),
    fr.SqlTable(conn="postgresql://staging/...", table="staging.vendor_drop"),
    keys=["vendor_record_id"],
    config=fr.ReconConfig(trim_strings=True, case_insensitive=True),
)
print(result.summary())
```

## Excel quarterly close vs warehouse

Finance hands you `Q4_close.xlsx`; warehouse has the same numbers
(allegedly):

```python
result = fr.compare(
    fr.ExcelFile("Q4_close.xlsx", sheet="GL Detail"),
    fr.SqlQuery(
        conn="snowflake://...",
        query="SELECT account, period, amount FROM gl_detail "
              "WHERE period = '2025Q4'",
    ),
    keys=["account", "period"],
    config=fr.ReconConfig(
        decimal_tolerance=0.005,    # half-cent
        column_mapping={"GL Account": "account", "Period": "period",
                        "Amount":     "amount"},
    ),
)
```

## Mainframe export vs modern table

A nightly fixed-width drop from a mainframe must agree with the
operational database it feeds:

```python
result = fr.compare(
    fr.FixedWidthFile(
        "mainframe/ledger_20260115.dat",
        columns=[
            ("account_id",  1, 10),
            ("posting_dt", 11,  8),
            ("amount",     19, 15),
            ("currency",   34,  3),
        ],
        trim_values=True,    # mainframe pads with spaces
        skip_rows=1,
    ),
    fr.SqlTable(conn="postgresql://core/...", table="ledger.posting"),
    keys=["account_id", "posting_dt"],
    config=fr.ReconConfig(decimal_tolerance=0.01, case_insensitive=True),
)
```

## Migration validation: old DB vs new DB

You've migrated from MySQL to Postgres. Prove every table moved
faithfully:

```python
TABLES = ["customers", "orders", "order_items", "payments"]

for tbl in TABLES:
    result = fr.compare(
        fr.SqlTable(conn="mysql+pymysql://old/...",  table=tbl),
        fr.SqlTable(conn="postgresql://new/...",     table=tbl),
        keys=["id"],
        config=fr.ReconConfig(decimal_tolerance=0.0001),
    )
    print(f"{tbl}: {result.status}  "
          f"(matched={result.matched_rows}, changed={result.changed_rows}, "
          f"left_only={result.left_only_rows}, right_only={result.right_only_rows})")
    result.to_html(f"migrate-{tbl}.html")
```

## Big partitioned dataset: bounded memory

A multi-billion-row Parquet dataset partitioned by date. Keyed compare
*per day* so peak memory stays small:

```python
from fastrecon import PartitionSpec

result = fr.compare(
    fr.ParquetFile("s3://lake-a/orders/year=*/month=*/*.parquet"),
    fr.ParquetFile("s3://lake-b/orders/year=*/month=*/*.parquet"),
    keys=["order_id"],
    partition=PartitionSpec(column="order_date", strategy="value"),
)
```

## Stable-checksum smoke test

Catch corrupted exports without the cost of a full keyed compare:

```python
result = fr.compare(
    fr.ParquetFile("export.parquet"),
    fr.ParquetFile("s3://archive/export.parquet"),
    keys=["id"],
    compare_mode="hash",
)
assert result.status == "MATCH", result.column_stats["hash"]
```

## Schema drift watch (no keys)

You don't trust that the new vendor file has the same shape:

```python
result = fr.compare(
    fr.CsvFile("vendor_old.csv"),
    fr.CsvFile("vendor_new.csv"),
    compare_mode="profile",
)
print(result.summary())   # row counts, null counts, distinct counts per column
```

## Reusing a connection across many compares

```python
from sqlalchemy import create_engine
prod = create_engine("postgresql://prod/...", pool_size=4)
stg  = create_engine("postgresql://staging/...", pool_size=4)

for tbl in ["orders", "payments", "shipments"]:
    fr.compare(
        fr.SqlTable(conn=prod, table=tbl),
        fr.SqlTable(conn=stg,  table=tbl),
        keys=["id"],
    ).to_html(f"{tbl}.html")
```
