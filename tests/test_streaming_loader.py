"""Streaming SQL loader: verify cursor streaming + Arrow conversion."""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pyarrow as pa
from sqlalchemy import create_engine, text

from fastrecon import SqlQuery, SqlTable, compare
from fastrecon.sources._sql_loader import load_via_sqlalchemy


def _seed(tmp_path: Path, n: int = 1000) -> str:
    db = tmp_path / "stream.db"
    url = f"sqlite:///{db}"
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER, val INTEGER)"))
        if n > 0:
            c.execute(text("INSERT INTO t VALUES " + ",".join(f"({i},{i*2})" for i in range(n))))
    e.dispose()
    return url


def test_loader_returns_streaming_reader(tmp_path: Path):
    """Loader returns a pa.RecordBatchReader (true streaming, single-pass)."""
    url = _seed(tmp_path, n=300)
    reader = load_via_sqlalchemy(url, "SELECT id, val FROM t", chunk_size=100)
    assert isinstance(reader, pa.RecordBatchReader)
    table = reader.read_all()
    assert table.num_rows == 300
    assert table.column_names == ["id", "val"]


def test_loader_handles_empty_result(tmp_path: Path):
    url = _seed(tmp_path, n=0)
    reader = load_via_sqlalchemy(url, "SELECT id, val FROM t", chunk_size=100)
    assert isinstance(reader, pa.RecordBatchReader)
    table = reader.read_all()
    assert table.num_rows == 0
    assert table.column_names == ["id", "val"]


def test_streaming_table_compares_correctly(tmp_path: Path):
    url = _seed(tmp_path, n=2500)
    res = compare(
        SqlTable(conn=url, table="t", chunk_size=500),
        SqlQuery(conn=url, query="SELECT id, val FROM t", chunk_size=500),
        keys=["id"],
    )
    assert res.status == "MATCH"
    assert res.row_count_left == 2500
    assert res.row_count_right == 2500


def test_chunk_size_does_not_affect_correctness(tmp_path: Path):
    url = _seed(tmp_path, n=500)
    for cs in (50, 137, 500, 5000):
        res = compare(
            SqlTable(conn=url, table="t", chunk_size=cs),
            SqlTable(conn=url, table="t", chunk_size=cs),
            keys=["id"],
        )
        assert res.status == "MATCH", f"chunk_size={cs} -> {res.summary()}"


def test_streaming_iteration_does_not_buffer_full_result(tmp_path: Path):
    """Iterating the reader one batch at a time must not hold the whole result.

    Probe: stream a 50K-row dataset with a small ``chunk_size`` and measure
    peak Python allocation while pulling one batch at a time, releasing each
    before fetching the next. Peak should be small (<10MB) — proving the DB
    cursor and Arrow conversion are both single-batch bounded.
    """
    url = _seed(tmp_path, n=50_000)
    reader = load_via_sqlalchemy(url, "SELECT id, val FROM t", chunk_size=500)

    tracemalloc.start()
    total_rows = 0
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            break
        total_rows += batch.num_rows
        del batch  # release before next fetch
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert total_rows == 50_000
    # 50K rows of (int, int) eager-loaded would be ~5-10MB of pyarrow plus
    # SQLAlchemy row buffers. A truly streaming reader at chunk_size=500
    # should peak well under 10MB during single-batch iteration.
    assert peak < 10_000_000, f"peak={peak} bytes — loader appears to buffer full result"


def test_streaming_full_consumption_works(tmp_path: Path):
    """Sanity check: the reader can also be drained via read_all()."""
    url = _seed(tmp_path, n=50_000)
    reader = load_via_sqlalchemy(url, "SELECT id, val FROM t", chunk_size=10_000)
    table = reader.read_all()
    assert table.num_rows == 50_000
