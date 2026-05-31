# File formats

fastrecon reads every common tabular and semi-structured format used in
data engineering, finance, and mainframe pipelines. Pick the right
class — or let `source(path)` pick it for you.

| Extension(s)         | Source class       | Optional install                | Notes |
| -------------------- | ------------------ | ------------------------------- | ----- |
| `.csv`               | `CsvFile`          | —                               | DuckDB `read_csv_auto`; pass `options=` for delim/quote/etc. |
| `.tsv`               | `CsvFile`          | —                               | `options={"delim": "\t"}` (auto-set by `source()`). |
| `.psv`               | `CsvFile`          | —                               | `options={"delim": "|"}` (auto-set by `source()`). |
| `.txt`, `.dat` (delim) | `CsvFile`        | —                               | Pass `options={"delim": ...}`. |
| `.txt`, `.dat` (fixed) | `FixedWidthFile` | —                               | Pass `columns=[(name, start, length), ...]`. |
| `.parquet`, `.pq`    | `ParquetFile`      | —                               | Globs and partitioned datasets supported natively. |
| `.json`, `.ndjson`, `.jsonl` | `JsonFile` | —                               | DuckDB `read_json_auto`; both NDJSON and top-level arrays. |
| `.xml`               | `XmlFile`          | `pip install 'fastrecon[xml]'`  | Hierarchical → flat via XPath. |
| `.xlsx`, `.xls`      | `ExcelFile`        | —                               | DuckDB's `excel` extension. |
| `.avro`              | `AvroFile`         | `pip install 'fastrecon[avro]'` | `fastavro` → Arrow → DuckDB. |
| `.orc`               | `OrcFile`          | —                               | `pyarrow.orc.read_table` → Arrow → DuckDB. |
| EBCDIC binary `.dat` | `MainframeFile`    | —                               | EBCDIC fixed-record + COMP-3 / zoned / binary decoders. |

Every file source accepts a local path **or** a remote URL — see
[Cloud storage](cloud-storage.md).

## Auto-detect: `source(path)`

Skip naming the class — `source()` picks the right one from the
extension and forwards your kwargs:

```python
from fastrecon import source, compare

compare(
    source("orders.parquet"),
    source("orders.xml", record_path="order"),
    keys=["order_id"],
)

# .tsv and .psv get the right delimiter wired up automatically.
source("ledger.tsv")
source("feed.psv")

# Force fixed-width or mainframe for ambiguous .dat / .txt files:
source("ledger.txt", fixed_width=True, columns=[("acct", 1, 10), ("amt", 11, 15)])
source("ACCT.DAT",   mainframe=True,   record_length=120, fields=[...])
```

`source()` raises `SourceError` for unknown extensions, listing what's
recognized. Caller-provided `options=` are deep-merged with the
per-extension defaults (so `source("a.tsv", options={"header": False})`
keeps the tab delimiter and adds your override).

## CsvFile

```python
from fastrecon import CsvFile

CsvFile("orders.csv")                                  # auto-detect everything
CsvFile("orders.csv", options={"delim": ";"})          # European CSVs
CsvFile("orders.tsv", options={"delim": "\t"})         # TSV
CsvFile("orders.csv", options={"header": False})       # headerless
CsvFile("orders.csv", options={"sample_size": 100000}) # bigger sniff window
```

Anything in `options` is forwarded as a keyword to DuckDB's
`read_csv_auto` — quoting, escape char, `decimal_separator`,
`null_padding`, etc.

## ParquetFile

```python
from fastrecon import ParquetFile

ParquetFile("orders.parquet")                         # single file
ParquetFile("data/orders/*.parquet")                  # folder of shards
ParquetFile("data/year=2026/month=*/*.parquet")       # partitioned dataset
ParquetFile("s3://bucket/key.parquet")                # remote (see cloud-storage.md)
```

## JsonFile

```python
from fastrecon import JsonFile

JsonFile("events.ndjson")                                          # NDJSON
JsonFile("events.json")                                            # top-level array
JsonFile("events.ndjson", options={"format": "newline_delimited"}) # be explicit
```

Nested objects become struct columns; nested arrays become list
columns. For a true row-vs-row recon, normalize nested fields into
scalar columns upstream (or use `SqlQuery` against a JSON-aware
database to do it in-engine).

## XmlFile

XML is hierarchical; reconciliation needs flat rows. You point
`record_path` at the node that represents one row and (optionally)
provide a `columns` map of output column → relative XPath.

```python
from fastrecon import XmlFile

# Explicit columns map — recommended.
XmlFile(
    path="orders.xml",
    record_path="./orders/order",
    columns={
        "id":     "./@id",        # attribute
        "amount": "./amount",     # child element
        "zip":    "./addr/zip",   # nested child
    },
)

# Auto mode — every direct child element + every attribute (prefixed @)
# becomes a column. Good for quick exploration; specify `columns` for
# real pipelines so column names are stable.
XmlFile(path="orders.xml", record_path="./order")

# With XML namespaces:
XmlFile(
    path="catalog.xml",
    record_path="./book:item",
    columns={"isbn": "./book:isbn"},
    namespaces={"book": "http://example.com/book"},
)
```

Streaming `iterparse` is used when `record_path` is a simple
step expression like `./order` or `./orders/order`. More complex
XPaths (predicates, axes, `*`) fall back to a full in-memory parse.

Install with `pip install 'fastrecon[xml]'` (adds `lxml`).

## ExcelFile

```python
from fastrecon import ExcelFile

ExcelFile("workbook.xlsx")                  # first sheet
ExcelFile("workbook.xlsx", sheet="Q1 2026") # named sheet
ExcelFile("workbook.xlsx", has_header=False)
```

For multi-sheet workbooks, register one `ExcelFile` per sheet and run
multiple compares.

## AvroFile

```python
from fastrecon import AvroFile

AvroFile("events.avro")                          # schema lives in the file
AvroFile("events.avro", flatten_unions=False)    # keep raw union envelopes
```

`flatten_unions=True` (default) collapses fastavro's
`{"<type-name>": value}` union envelopes to the inner value. Only keys
that match Avro type names (`string`, `int`, `long`, `double`, …) or
qualified record names (`com.example.User`) are unwrapped — legitimate
single-key user records like `{"address": {...}}` are left alone.

Empty Avro files still register a typed view: column names come from
the writer schema, so a downstream compare on the original key column
will succeed (return zero rows, not crash).

Install with `pip install 'fastrecon[avro]'` (adds `fastavro`).

## OrcFile

```python
from fastrecon import OrcFile

OrcFile("export.orc")
OrcFile("export.orc", columns=["id", "amount"])  # column projection
```

Backed by `pyarrow.orc` (already a core dep — no extra install).

## FixedWidthFile

For COBOL / RPG / banking exports where every record is laid out at
fixed character positions:

```python
from fastrecon import FixedWidthFile

FixedWidthFile(
    "ledger.txt",
    columns=[
        ("account_id", 1,   10),  # cols 1-10
        ("name",       11,  30),  # cols 11-40
        ("amount",     41,  15),  # cols 41-55
    ],
    skip_rows=1,            # one header line
    trim_values=False,      # see below
)
```

- `start` is **1-indexed** to match COBOL/RPG conventions.
- `length` is in characters.
- Columns may overlap (occasionally needed for redefined fields).
- `trim_values` defaults to `False` so parsing is **lossless**. Turn it
  on if your producer space-pads numeric or text fields and you don't
  want those spaces to count as differences. (`ReconConfig.trim_strings`
  also exists at the compare layer — pick the level that matches your
  data contract.)

## MainframeFile (EBCDIC + COBOL packed decimal)

For real mainframe `.dat` files: EBCDIC text with COBOL `COMP-3`,
zoned-decimal, or binary fields, no line terminators.

```python
from fastrecon import MainframeFile

MainframeFile(
    path="ACCT.DAT",
    record_length=120,        # bytes per record (no newlines)
    encoding="cp037",         # cp037 US, cp500 intl, cp1047 open systems
    skip_bytes=0,             # skip RDW headers, etc.
    fields=[
        {"name": "acct_id",   "start": 1,   "length": 10, "type": "text"},
        {"name": "balance",   "start": 11,  "length": 6,  "type": "comp3", "scale": 2},
        {"name": "status",    "start": 17,  "length": 1,  "type": "text"},
        {"name": "txn_count", "start": 18,  "length": 4,  "type": "binary"},
        {"name": "score",     "start": 22,  "length": 5,  "type": "zoned", "scale": 0},
    ],
)
```

Field types:

| `type`              | What it is                         | Notes |
| ------------------- | ---------------------------------- | ----- |
| `"text"`            | EBCDIC (or ASCII) text             | Decoded with `encoding`. `TRIM()` unless `trim=False`. |
| `"int"`             | EBCDIC text → BIGINT               | `TRY_CAST`; non-numeric → NULL. |
| `"comp3"` / `"packed"` | COBOL packed decimal (BCD)      | `length` is in **bytes**. `scale` gives implied decimals. |
| `"zoned"`           | COBOL zoned-decimal                | Sign in last byte's high nibble. `scale` ditto. |
| `"binary"` / `"comp"` / `"comp4"` | Big-endian signed integer | Raw 1/2/4/8-byte BIGINT. |

Sign-nibble validation follows the COBOL standard: `C`/`F`/`A`/`E` → +,
`D`/`B` → −. Anything else returns `NULL` rather than silently
mis-decoding.

Per-field decode failures (bad packed digits, undecodable text)
likewise return `NULL` so one corrupt cell doesn't kill the run — the
NULL shows up as a difference in the report and you can investigate.

## Folders, datasets, partitioned tables

There is no separate `FolderSource` class — this is a one-liner with
the existing readers:

```python
ParquetFile("data/*.parquet")                 # mixed shards
ParquetFile("data/year=*/month=*/*.parquet")  # Hive-style partitions
CsvFile("exports/2026-*.csv")                 # multi-CSV folder
```

DuckDB applies projections and filters to every matched file in
parallel.

## Memory note (XML / Avro / ORC / Mainframe)

Today, the XML, Avro, ORC, and Mainframe readers materialize the file
into an in-memory Arrow table before registering it with DuckDB. That's
ideal for typical files (hundreds of MB) but can OOM on multi-GB
inputs. Native streaming for these formats is a tracked follow-up;
SQL/CSV/Parquet/JSON ingestion is already streaming through DuckDB.
