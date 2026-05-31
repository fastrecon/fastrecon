"""Compare a SQL query result against a Parquet file."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

from fastrecon import ParquetFile, SqlQuery, compare

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
db_path = DATA / "demo.db"
pq_path = DATA / "demo.parquet"

# Seed SQLite
url = f"sqlite:///{db_path}"
eng = create_engine(url)
with eng.begin() as c:
    c.execute(text("DROP TABLE IF EXISTS sales"))
    c.execute(text("CREATE TABLE sales (sale_id INTEGER, region TEXT, amount REAL)"))
    c.execute(text(
        "INSERT INTO sales VALUES (1,'EU',100.0),(2,'US',200.0),(3,'APAC',150.0)"
    ))
eng.dispose()

# Write Parquet (slightly different)
pq.write_table(
    pa.table({
        "sale_id": [1, 2, 3, 4],
        "region": ["EU", "US", "APAC", "LATAM"],
        "amount": [100.0, 201.0, 150.0, 80.0],
    }),
    pq_path,
)

result = compare(
    left=SqlQuery(conn=url, query="SELECT sale_id, region, amount FROM sales"),
    right=ParquetFile(str(pq_path)),
    keys=["sale_id"],
    tolerances={"amount": 0.5},
)
print(result.summary())
