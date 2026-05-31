"""Partition-wise reconciliation example.

Demonstrates how to compare a SQL table against a Parquet file by splitting
the work into per-region partitions. Per-partition results pinpoint where
the mismatch lives.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

from fastrecon import ParquetFile, PartitionSpec, SqlQuery, compare

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
db_path = DATA / "partition_demo.db"
pq_path = DATA / "partition_demo.parquet"

# Seed source: 12 sales across 3 regions
url = f"sqlite:///{db_path}"
eng = create_engine(url)
with eng.begin() as c:
    c.execute(text("DROP TABLE IF EXISTS sales"))
    c.execute(text("CREATE TABLE sales (sale_id INTEGER, region TEXT, amount REAL)"))
    rows = []
    for region in ("EU", "US", "APAC"):
        for i in range(1, 5):
            rows.append(f"({len(rows) + 1}, '{region}', {i * 100}.0)")
    c.execute(text(f"INSERT INTO sales VALUES {', '.join(rows)}"))
eng.dispose()

# Target: same data but with one mutation in EU and a missing US row
data = {"sale_id": [], "region": [], "amount": []}
for sid in range(1, 13):
    region = "EU" if sid <= 4 else ("US" if sid <= 8 else "APAC")
    if sid == 6:        # drop one US row
        continue
    amount = float(((sid - 1) % 4 + 1) * 100)
    if sid == 2:        # mutate one EU row
        amount = 999.0
    data["sale_id"].append(sid)
    data["region"].append(region)
    data["amount"].append(amount)
pq.write_table(pa.table(data), pq_path)

result = compare(
    left=SqlQuery(conn=url, query="SELECT sale_id, region, amount FROM sales"),
    right=ParquetFile(str(pq_path)),
    keys=["sale_id"],
    partition=PartitionSpec(column="region", strategy="value"),
)

print(result.summary())
print()
print("Per-partition breakdown:")
for p in result.column_stats["partitions"]:
    flag = "OK " if p["match"] else "FAIL"
    print(
        f"  [{flag}] region={p['partition']!s:<6} "
        f"left={p['row_count_left']:>3} right={p['row_count_right']:>3} "
        f"missing_in_right={p['missing_in_right']} changed={p['changed_rows']}"
    )
