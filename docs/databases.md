# Database connectors

All databases below are reached through `SqlTable` and `SqlQuery`, which
accept any SQLAlchemy URL. **Views and materialized views work the same
way as tables** — pass the view's name to `SqlTable(table=...)`.

| Database                 | Install                            | URL prefix                       |
| ------------------------ | ---------------------------------- | -------------------------------- |
| PostgreSQL               | `pip install "fastrecon[postgres]"` | `postgresql://user:pwd@host/db` |
| MySQL                    | `pip install "fastrecon[mysql]"`    | `mysql+pymysql://user:pwd@host/db` |
| MariaDB                  | `pip install "fastrecon[mariadb]"`  | `mysql+pymysql://user:pwd@host/db` |
| SQL Server               | `pip install "fastrecon[mssql]"`    | `mssql+pyodbc://user:pwd@dsn`   |
| Oracle                   | `pip install "fastrecon[oracle]"`   | `oracle+oracledb://user:pwd@host:1521/?service_name=...` |
| SQLite                   | core (stdlib)                      | `sqlite:///path/to/file.db`     |
| Snowflake                | `pip install "fastrecon[snowflake]"` | `snowflake://user:pwd@account/db/schema?warehouse=WH` |
| Amazon Redshift          | `pip install "fastrecon[redshift]"`  | `redshift+redshift_connector://user:pwd@host:5439/db` |
| Google BigQuery          | `pip install "fastrecon[bigquery]"`  | `bigquery://project/dataset` |
| Databricks SQL Warehouse | `pip install "fastrecon[databricks]"` | `databricks://token:<pat>@host?http_path=/sql/1.0/warehouses/<id>` |
| SAP HANA                 | `pip install "fastrecon[hana]"`     | `hana://user:pwd@host:port` |
| Teradata                 | `pip install "fastrecon[teradata]"` | `teradatasql://user:pwd@host` |

Or grab everything at once:

```bash
pip install "fastrecon[all-databases]"
```

## SqlTable — whole tables, views, materialized views

```python
from fastrecon import SqlTable

SqlTable(conn="postgresql://prod/...", table="public.orders")
SqlTable(conn="postgresql://prod/...", table="public.orders_v")    # view
SqlTable(conn="postgresql://prod/...", table="public.orders_mv")   # materialized view
```

`table` may be schema-qualified. Identifiers are quoted by SQLAlchemy.

## SqlQuery — push-down filtering / projections

When you only need part of a table — a date window, a tenant slice, or a
specific projection — push that work to the database:

```python
from fastrecon import SqlQuery

SqlQuery(
    conn="postgresql://prod/...",
    query="SELECT order_id, customer_id, amount FROM orders "
          "WHERE order_date >= '2026-01-01'",
)
```

The query streams in via Arrow batches, so even multi-million-row
windows don't materialize as Python objects.

## Stored procedures

Driver-dependent, but common engines support `CALL` over a query API:

```python
SqlQuery(
    conn="mssql+pyodbc://...",
    query="EXEC dbo.GetDailyTotals @dt='2026-01-15'",
)
```

For Oracle pipelined functions:

```python
SqlQuery(
    conn="oracle+oracledb://...",
    query="SELECT * FROM TABLE(my_pkg.daily_totals(DATE '2026-01-15'))",
)
```

> **Note**: any stored proc that returns more than one result set, opens
> a refcursor, or requires special transaction handling is best wrapped
> behind a real view first; treat fastrecon as a *consumer* of result sets,
> not an orchestrator.

## PostgresSource — native scanner (advanced, Postgres only)

For very large Postgres tables, the bundled DuckDB `postgres` extension
can read directly without the SQLAlchemy round-trip:

```python
from fastrecon import PostgresSource

PostgresSource(conn="postgresql://prod/...", table="public.orders_2026")
```

Significantly lower per-row overhead on multi-hundred-million-row scans.
Otherwise behaves identically to `SqlTable`.

## Connection pooling

Each `SqlTable` / `SqlQuery` uses its own SQLAlchemy engine sized for a
single bulk read. If you reconcile many table pairs in a loop and want
to share a pool, build the source from an existing `sqlalchemy.Engine`:

```python
from sqlalchemy import create_engine
engine = create_engine("postgresql://prod/...", pool_size=5)
SqlTable(conn=engine, table="orders")
```

## Picking the right source

| Situation                                              | Use                                     |
| ------------------------------------------------------ | --------------------------------------- |
| Whole table or view                                    | `SqlTable`                              |
| Filter, projection, join, or stored proc               | `SqlQuery`                              |
| Multi-hundred-million-row Postgres scan, throughput-critical | `PostgresSource`                  |
| Test fixture or quick demo                             | `sqlite:///` URL with `SqlTable`        |
