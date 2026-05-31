"""Parquet vs SQL query reconciliation."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

from fastrecon import ParquetFile, SqlQuery, compare


def test_parquet_vs_sql_query(tmp_path: Path):
    pq_path = tmp_path / "data.parquet"
    table = pa.table({"id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]})
    pq.write_table(table, pq_path)

    db_path = tmp_path / "x.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER, amount REAL)"))
        c.execute(text("INSERT INTO t VALUES (1,10.0),(2,20.0),(3,30.0)"))
    eng.dispose()

    res = compare(
        ParquetFile(str(pq_path)),
        SqlQuery(conn=url, query="SELECT id, amount FROM t"),
        keys=["id"],
    )
    assert res.status == "MATCH", res.summary()
