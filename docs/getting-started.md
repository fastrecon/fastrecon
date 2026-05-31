# Getting started

## Install

```bash
pip install fastrecon                   # core (CSV/TSV/PSV/Parquet/JSON/Excel/ORC/fixed-width/EBCDIC + SQLite)
pip install "fastrecon[xml]"            # adds lxml for XmlFile
pip install "fastrecon[avro]"           # adds fastavro for AvroFile
pip install "fastrecon[all-files]"      # every file format above
pip install "fastrecon[postgres]"       # add a database driver as needed
pip install "fastrecon[all-databases]"  # every database driver in one shot
```

Python 3.9+ is required.

## Your first compare

Two CSV files, keyed on `order_id`:

```python
import fastrecon as fr

result = fr.compare(
    fr.CsvFile("orders_left.csv"),
    fr.CsvFile("orders_right.csv"),
    keys=["order_id"],
)
print(result.summary())
```

The output tells you exactly:

- How many rows each side had
- How many matched, were missing on each side, or had per-column changes
- A bounded sample of the actual differing rows (with `__left` / `__right`
  values per column)
- Per-column drift counts so you can see *which* fields are noisy

If the datasets agree, `result.status == "MATCH"` and `result.exit_code == 0`.

## Two warehouses, one key column

```python
result = fr.compare(
    fr.SqlTable(conn="postgresql://prod/...", table="public.orders"),
    fr.SqlQuery(conn="snowflake://...",       query="SELECT * FROM ORDERS"),
    keys=["order_id"],
    config=fr.ReconConfig(
        decimal_tolerance=0.01,    # ignore sub-cent rounding
        case_insensitive=True,     # 'USD' == 'usd'
        ignore_columns=["loaded_at"],
    ),
)
```

## Save a report

```python
result.to_html("recon.html")
result.to_junit("recon.junit.xml")    # for CI gates
result.to_json("recon.json")          # for dashboards / logs
```

## CI gate

`compare()` returns a `ReconResult` whose `exit_code` follows
`fail-on=mismatch` semantics: `0` MATCH, `1` MISMATCH, `2` ERROR.
Wire it straight into `sys.exit`:

```python
import sys
sys.exit(result.exit_code)
```

Or use the CLI — see [CLI reference](cli.md).

## Where to next

- Different source on each side? See [Database connectors](databases.md)
  and [File formats](files.md).
- Files in S3 / GCS / Azure? See [Cloud storage](cloud-storage.md).
- Don't have stable keys? Use `compare_mode="profile"` or `"hash"` —
  see [Compare modes](compare-modes.md).
