"""Verify the ``streaming=False`` opt-out uses the legacy eager loader."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
from sqlalchemy import create_engine, text

from fastrecon import SqlQuery, SqlTable, compare
from fastrecon.sources._sql_loader import (
    load_via_sqlalchemy,
    load_via_sqlalchemy_eager,
)


def _seed(tmp_path: Path, n: int = 200) -> str:
    db = tmp_path / "opt.db"
    url = f"sqlite:///{db}"
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER, val INTEGER)"))
        c.execute(text("INSERT INTO t VALUES " + ",".join(f"({i},{i*3})" for i in range(n))))
    e.dispose()
    return url


def test_eager_loader_returns_arrow_table(tmp_path: Path):
    url = _seed(tmp_path, n=50)
    out = load_via_sqlalchemy_eager(url, "SELECT id, val FROM t")
    assert isinstance(out, pa.Table)
    assert out.num_rows == 50
    assert out.column_names == ["id", "val"]


def test_streaming_loader_returns_record_batch_reader(tmp_path: Path):
    url = _seed(tmp_path, n=50)
    out = load_via_sqlalchemy(url, "SELECT id, val FROM t", chunk_size=10)
    assert isinstance(out, pa.RecordBatchReader)
    assert out.read_all().num_rows == 50


def test_compare_works_with_streaming_off(tmp_path: Path):
    url = _seed(tmp_path, n=100)
    res = compare(
        SqlTable(conn=url, table="t", streaming=False),
        SqlQuery(conn=url, query="SELECT id, val FROM t", streaming=False),
        keys=["id"],
    )
    assert res.status == "MATCH"
    assert res.row_count_left == 100


def test_compare_works_mixing_streaming_modes(tmp_path: Path):
    """Streaming on one side and eager on the other must produce identical results."""
    url = _seed(tmp_path, n=100)
    res = compare(
        SqlTable(conn=url, table="t", streaming=True, chunk_size=25),
        SqlQuery(conn=url, query="SELECT id, val FROM t", streaming=False),
        keys=["id"],
    )
    assert res.status == "MATCH"
    assert res.row_count_left == res.row_count_right == 100
