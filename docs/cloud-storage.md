# Cloud storage

All file sources (`CsvFile`, `ParquetFile`, `JsonFile`, `ExcelFile`,
`FixedWidthFile`) accept a remote URL anywhere they accept a path. There
is no separate "cloud" class — the URL scheme picks the backend.

| Backend              | URL form                                        | How it works |
| -------------------- | ----------------------------------------------- | ------------ |
| Local filesystem     | `/abs/path` or `./relative/path`                | Direct read. |
| Network share        | mount it (NFS/SMB), then use the local path     | Direct read. |
| AWS S3               | `s3://bucket/key.parquet`                       | DuckDB `httpfs`. |
| Google Cloud Storage | `gs://bucket/key.parquet`                       | DuckDB `httpfs`. |
| Azure Blob / ADLS    | `azure://container/key.parquet`                 | DuckDB `httpfs`. |
| HTTPS                | `https://host/path/file.csv`                    | DuckDB `httpfs`. |
| SFTP / FTP           | _roadmap — not natively supported by DuckDB._   | — |

## How it works

On startup, fastrecon's DuckDB engine lazy-loads the bundled `httpfs`
extension. After that, every file source treats `s3://`, `gs://`,
`azure://`, and `https://` URLs as if they were local paths.

```python
from fastrecon import ParquetFile, JsonFile, compare

compare(
    ParquetFile("s3://prod-warehouse/exports/orders/2026-01-15.parquet"),
    JsonFile("https://staging.example.com/api/dump/orders.ndjson"),
    keys=["order_id"],
)
```

Globs work over remote paths too:

```python
ParquetFile("s3://prod-warehouse/exports/orders/year=2026/month=*/*.parquet")
```

## Authentication

fastrecon does not invent its own credential mechanism — DuckDB's
[secrets manager](https://duckdb.org/docs/configuration/secrets_manager.html)
is the way to authenticate. The most common patterns:

### AWS S3

Anything DuckDB recognizes works:

- The standard AWS environment variables (`AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`) — the
  simplest option in CI / Lambda / EC2.
- An explicit DuckDB SECRET created against the same connection
  fastrecon uses. You can run raw SQL through the engine:

  ```python
  from fastrecon.engines.duckdb_engine import DuckDBEngine
  eng = DuckDBEngine()
  eng.execute("""
      CREATE SECRET my_s3 (
          TYPE S3,
          KEY_ID    'AKIA...',
          SECRET    '...',
          REGION    'us-east-1'
      );
  """)
  # subsequent ParquetFile("s3://...") calls on this engine pick it up.
  ```

### Google Cloud Storage

Use a [HMAC key](https://cloud.google.com/storage/docs/authentication/hmackeys)
with the S3-compatible secret type:

```sql
CREATE SECRET gcs (TYPE GCS, KEY_ID 'GOOG...', SECRET '...');
```

### Azure Blob / ADLS

```sql
CREATE SECRET az (
    TYPE AZURE,
    CONNECTION_STRING 'DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...'
);
```

## Air-gapped installs

If your environment can't reach the DuckDB extension repository,
extension loading is best-effort: fastrecon swallows the install error
so purely-local file + SQL compares keep working. The first attempt to
read an `s3://` URL will then surface a clear "extension not loaded"
error from DuckDB, which is what you want — the failure is explicit at
the point of use.

To pre-stage extensions in a controlled environment, use
[DuckDB's offline extension installation](https://duckdb.org/docs/extensions/extension_distribution.html#offline-installation)
before the first fastrecon call.

## Performance notes

- Range reads are used for Parquet, so `ParquetFile("s3://...")` only
  pulls the columns + row groups needed for the compare keys + payload.
- CSV/JSON/Excel are streamed sequentially — they don't support
  push-down. For large remote CSV files, prefer Parquet upstream.
- For very large multi-shard remote datasets, partition-wise compare
  (`compare(..., partition=PartitionSpec(...))`) reduces peak memory by
  splitting the work into independent slices. See [Compare modes](compare-modes.md).
