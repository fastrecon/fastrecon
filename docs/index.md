# fastrecon

A high-performance reconciliation engine for SQL tables, queries, and
every common file format — **CSV, TSV, PSV, Parquet, JSON, XML, Excel,
Avro, ORC, fixed-width, and EBCDIC mainframe binary** — powered by
**DuckDB**, **Polars**, and **Arrow**.

fastrecon answers one question fast and well: **do these two datasets
agree?** Whether the two sides are tables in different databases, two
Parquet exports, a CSV against a SQL view, or a JSON dump against a
warehouse query — the API is the same.

## At a glance

```python
import fastrecon as fr

result = fr.compare(
    fr.SqlTable(conn="postgresql://...", table="orders"),
    fr.ParquetFile("s3://bucket/exports/orders.parquet"),
    keys=["order_id"],
    config=fr.ReconConfig(decimal_tolerance=0.01),
)
print(result.summary())
result.to_html("recon.html")
```

Or skip the class names and let the extension pick:

```python
from fastrecon import source, compare

compare(source("orders.parquet"),
        source("orders.xml", record_path="order"),
        keys=["order_id"])
```

### Schema-aware compare (new in 0.7.0)

fastrecon now profiles a sample of each side to assign every column a
*logical* type — `integer`, `decimal`, `date`, `timestamp`, `bool`,
`text` — and uses that for the schema diff and the row-by-row compare.
Practical effect: a CSV column where amounts are stored as `"100"`
matches a real `INTEGER` column on the other side; only genuine drift
(free-text vs integer, missing columns) shows up as a schema mismatch.

## Documentation map

| Topic                                       | Read this                          |
| ------------------------------------------- | ---------------------------------- |
| Install + first compare in 60 seconds       | [Getting started](getting-started.md) |
| Connect to PostgreSQL, Snowflake, BigQuery… | [Database connectors](databases.md) |
| Read CSV, Parquet, JSON, XML, Excel, Avro, ORC, fixed-width, EBCDIC | [File formats](files.md) |
| `s3://`, `gs://`, `azure://` paths          | [Cloud storage](cloud-storage.md)  |
| Keyed vs profile vs hash vs partition       | [Compare modes](compare-modes.md)  |
| Trim, case, decimals, NULLs, mappings       | [Configuration](configuration.md)  |
| Run reconciliations from the shell          | [CLI reference](cli.md)            |
| Every public class & function               | [API reference](api-reference.md)  |
| Common workflows end-to-end                 | [Recipes](recipes.md)              |
| Performance vs `datacompy`, `data-diff`, …  | [Benchmarks](benchmarks.md)        |

## Building the documentation locally

The docs are plain Markdown. To render the site with
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/):

```bash
pip install mkdocs mkdocs-material
mkdocs serve         # http://localhost:8000
mkdocs build         # static site in ./site
```

The configuration lives in `mkdocs.yml` at the project root.
