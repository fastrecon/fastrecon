"""Live PostgreSQL integration tests.

Uses the session-scoped ``pg_url`` fixture from conftest.py. Skips if
PostgreSQL binaries are not available in the environment.

Covers:
- PostgresSource.register against a real database
- Postgres↔Parquet recon proves the scanner path is used (no Python row
  marshalling — the recon engine reads the relation through DuckDB only)
- Schema, type, and nullability preservation through the streaming loader
- First-batch-null edge case
- Empty result handling
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, text

from fastrecon import (
    ParquetFile, PartitionSpec, PostgresSource,
    SqlQuery, SqlTable, compare,
)


def _seed_pg(url: str, name: str = "orders") -> None:
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text(f"DROP TABLE IF EXISTS {name}"))
        c.execute(text(f"""
            CREATE TABLE {name} (
                order_id INTEGER PRIMARY KEY,
                region   TEXT NOT NULL,
                amount   NUMERIC(10,2),
                note     TEXT
            )
        """))
        rows = []
        for i in range(1, 201):
            region = "EU" if i <= 100 else "US"
            note = "NULL" if i % 7 == 0 else f"'note-{i}'"
            rows.append(f"({i}, '{region}', {i*1.5}, {note})")
        c.execute(text(f"INSERT INTO {name} VALUES " + ", ".join(rows)))
    e.dispose()


# ---------------------------------------------------------- PostgresSource

def test_postgres_source_register_and_compare_against_parquet(pg_url: str, tmp_path: Path):
    _seed_pg(pg_url)

    # Build a Parquet "snapshot" of the same data, mutate two rows
    e = create_engine(pg_url)
    with e.connect() as c:
        rows = c.execute(text("SELECT order_id, region, amount, note FROM orders")).fetchall()
    e.dispose()

    pq_path = tmp_path / "orders.parquet"
    data = {
        "order_id": [r[0] for r in rows],
        "region":   [r[1] for r in rows],
        "amount":   [float(r[2]) for r in rows],
        "note":     [r[3] for r in rows],
    }
    # Mutate one row, drop another to create a known mismatch
    data["amount"][9] = 9999.0
    for k in data:
        del data[k][50]
    pq.write_table(pa.table(data), pq_path)

    res = compare(
        left=PostgresSource(conn=pg_url, table="public.orders"),
        right=ParquetFile(str(pq_path)),
        keys=["order_id"],
    )
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1
    assert res.missing_in_right == 1
    assert res.row_count_left == 200
    assert res.row_count_right == 199


def test_two_postgres_sources_on_same_connection_no_alias_collision(pg_url: str):
    """Two PostgresSource registrations against different databases on the
    same DuckDB connection must each remain bound to *their own* attached DB.

    Regression: previously both sides shared ``attach_alias='fr_pg'`` and
    the second registration would DETACH/re-ATTACH the alias used by the
    first source, silently swapping the data behind the first view.
    """
    # Create a second database in the same Postgres instance
    base_url = pg_url.rsplit("/", 1)[0]
    e0 = create_engine(base_url + "/postgres", isolation_level="AUTOCOMMIT")
    with e0.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS fastrecon_test_alt"))
        c.execute(text("CREATE DATABASE fastrecon_test_alt"))
    e0.dispose()
    alt_url = base_url + "/fastrecon_test_alt"

    # Same schema, *different* contents on each side
    for url, val in [(pg_url, "LEFT"), (alt_url, "RIGHT")]:
        e = create_engine(url)
        with e.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS twosrc"))
            c.execute(text("CREATE TABLE twosrc (id INT PRIMARY KEY, side TEXT)"))
            c.execute(text(f"INSERT INTO twosrc VALUES (1, '{val}'), (2, '{val}')"))
        e.dispose()

    left = PostgresSource(conn=pg_url, table="twosrc")
    right = PostgresSource(conn=alt_url, table="twosrc")

    # Share ONE DuckDB connection across both registrations to mimic compare()
    con = duckdb.connect(":memory:")
    try:
        left.register(con, "left_view")
        right.register(con, "right_view")

        l_rows = sorted(con.execute('SELECT id, side FROM "left_view" ORDER BY id').fetchall())
        r_rows = sorted(con.execute('SELECT id, side FROM "right_view" ORDER BY id').fetchall())

        assert l_rows == [(1, "LEFT"), (2, "LEFT")], (
            f"left view contaminated by right alias re-attach: {l_rows}"
        )
        assert r_rows == [(1, "RIGHT"), (2, "RIGHT")], r_rows
    finally:
        con.close()

    # And the full compare() path must reflect the real mismatch
    res = compare(left=left, right=right, keys=["id"])
    assert res.status == "MISMATCH"
    assert res.changed_rows == 2


def test_postgres_source_via_query(pg_url: str):
    _seed_pg(pg_url)
    res = compare(
        left=PostgresSource(conn=pg_url, query="SELECT order_id, region FROM public.orders"),
        right=PostgresSource(conn=pg_url, query="SELECT order_id, region FROM public.orders"),
        keys=["order_id"],
    )
    assert res.status == "MATCH"


def test_compare_postgres_to_parquet_uses_scanner_path(pg_url: str, tmp_path: Path):
    """End-to-end: a real compare(PostgresSource, ParquetFile, ...) must
    register the Postgres side via the native scanner — no SQLAlchemy
    fallback, no Python row marshalling."""
    _seed_pg(pg_url)
    e = create_engine(pg_url)
    with e.connect() as c:
        rows = c.execute(text("SELECT order_id, region, amount FROM orders")).fetchall()
    e.dispose()
    pq.write_table(pa.table({
        "order_id": [r[0] for r in rows],
        "region": [r[1] for r in rows],
        "amount": [float(r[2]) for r in rows],
    }), tmp_path / "snap.parquet")

    PostgresSource.last_register_path = None
    res = compare(
        left=PostgresSource(conn=pg_url, table="public.orders"),
        right=ParquetFile(str(tmp_path / "snap.parquet")),
        keys=["order_id"],
    )
    # Row data matches; schema may differ trivially (Postgres NUMERIC vs Parquet DOUBLE).
    assert res.row_count_left == 200 and res.row_count_right == 200
    assert res.changed_rows == 0 and res.missing_in_left == 0 and res.missing_in_right == 0
    assert PostgresSource.last_register_path == "scanner", (
        "compare() did not exercise the postgres scanner path "
        f"(last_register_path={PostgresSource.last_register_path!r})"
    )


def test_postgres_scanner_pushdown_no_python_marshalling(pg_url: str, tmp_path: Path):
    """Regression: the scanner path must not pull rows through Python.

    Verified by checking that DuckDB's EXPLAIN plan for a query against
    the registered relation references the postgres extension's scan
    function (POSTGRES_SCAN / postgres_scan), not arrow_scan or
    parquet_scan or a Python registered function.
    """
    _seed_pg(pg_url)
    src = PostgresSource(conn=pg_url, table="public.orders")
    con = duckdb.connect(":memory:")
    try:
        src.register(con, "v")
        plan = con.execute('EXPLAIN SELECT order_id FROM "v" WHERE region = \'EU\'').fetchall()
        plan_text = "\n".join(r[1] for r in plan).upper()
        assert "POSTGRES" in plan_text, f"expected postgres scanner in plan, got:\n{plan_text}"
        # Sanity: the data is reachable
        n = con.execute('SELECT COUNT(*) FROM "v" WHERE region = \'EU\'').fetchone()[0]
        assert n == 100
    finally:
        con.close()


# --------------------------------------- Streaming loader: type preservation

def test_streaming_preserves_types_and_nullability(pg_url: str):
    """Numeric / TEXT / NULL values survive the Arrow streaming roundtrip."""
    _seed_pg(pg_url, name="typed")
    src = SqlTable(conn=pg_url, table="typed")
    con = duckdb.connect(":memory:")
    try:
        src.register(con, "v")
        schema = {col: dtype for col, dtype, *_ in
                  con.execute('DESCRIBE SELECT * FROM "v"').fetchall()}
        assert "INT" in schema["order_id"].upper()
        assert "VARCHAR" in schema["region"].upper() or "TEXT" in schema["region"].upper()
        # NULLs preserved: rows where i % 7 == 0 -> 28 nulls in 200 (i ∈ [1,200])
        n_null = con.execute('SELECT COUNT(*) FROM "v" WHERE note IS NULL').fetchone()[0]
        assert n_null == 28
    finally:
        con.close()


def test_streaming_preserves_decimal_precision(pg_url: str):
    """NUMERIC columns must round-trip as Arrow decimal128, not float64.

    Otherwise reconciliation on monetary / financial columns silently loses
    precision (e.g. ``1234567890.12345`` -> ``1234567890.1234500408...``).
    """
    e = create_engine(pg_url)
    with e.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS money"))
        c.execute(text("CREATE TABLE money (id INT, amt NUMERIC(18, 4))"))
        c.execute(text("INSERT INTO money VALUES "
                       "(1, 1234567890.1234), (2, -0.0001), (3, 99999999999999.9999)"))
    e.dispose()

    src = SqlTable(conn=pg_url, table="money")
    con = duckdb.connect(":memory:")
    try:
        src.register(con, "v")
        schema = {col: dtype for col, dtype, *_ in
                  con.execute('DESCRIBE SELECT * FROM "v"').fetchall()}
        assert "DECIMAL" in schema["amt"].upper(), schema
        rows = con.execute('SELECT id, CAST(amt AS VARCHAR) FROM "v" ORDER BY id').fetchall()
        # Exact string representation — no float drift
        assert rows == [(1, "1234567890.1234"),
                        (2, "-0.0001"),
                        (3, "99999999999999.9999")]
    finally:
        con.close()


def test_streaming_handles_first_batch_all_null(pg_url: str):
    """Edge case: first batch's column may be entirely NULL — schema inference
    must not crash and subsequent non-null batches must coexist."""
    e = create_engine(pg_url)
    with e.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS edge"))
        c.execute(text("CREATE TABLE edge (id INT, opt TEXT)"))
        # First 50: opt is NULL; rest: opt has value
        rows = [f"({i}, NULL)" for i in range(50)] + [f"({i}, 'v{i}')" for i in range(50, 100)]
        c.execute(text("INSERT INTO edge VALUES " + ", ".join(rows)))
    e.dispose()

    src = SqlQuery(conn=pg_url, query="SELECT id, opt FROM edge ORDER BY id", chunk_size=25)
    con = duckdb.connect(":memory:")
    try:
        src.register(con, "v")
        n = con.execute('SELECT COUNT(*) FROM "v"').fetchone()[0]
        n_null = con.execute('SELECT COUNT(*) FROM "v" WHERE opt IS NULL').fetchone()[0]
        n_set = con.execute('SELECT COUNT(*) FROM "v" WHERE opt IS NOT NULL').fetchone()[0]
        assert n == 100 and n_null == 50 and n_set == 50
    finally:
        con.close()


def test_streaming_handles_empty_result(pg_url: str):
    e = create_engine(pg_url)
    with e.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS empties"))
        c.execute(text("CREATE TABLE empties (id INT, name TEXT)"))
    e.dispose()

    src = SqlTable(conn=pg_url, table="empties")
    con = duckdb.connect(":memory:")
    try:
        src.register(con, "v")
        assert con.execute('SELECT COUNT(*) FROM "v"').fetchone()[0] == 0
        # Schema is still readable
        cols = [r[0] for r in con.execute('DESCRIBE SELECT * FROM "v"').fetchall()]
        assert cols == ["id", "name"]
    finally:
        con.close()


def test_postgres_source_with_partitioned_compare(pg_url: str, tmp_path: Path):
    """End-to-end: PostgresSource + value-partitioned recon against Parquet."""
    _seed_pg(pg_url)
    e = create_engine(pg_url)
    with e.connect() as c:
        rows = c.execute(text("SELECT order_id, region, amount FROM orders")).fetchall()
    e.dispose()
    pq.write_table(pa.table({
        "order_id": [r[0] for r in rows],
        "region": [r[1] for r in rows],
        "amount": [float(r[2]) for r in rows],
    }), tmp_path / "snap.parquet")

    res = compare(
        left=PostgresSource(conn=pg_url, query="SELECT order_id, region, amount FROM public.orders"),
        right=ParquetFile(str(tmp_path / "snap.parquet")),
        keys=["order_id"],
        partition=PartitionSpec(column="region", strategy="value"),
    )
    assert res.status == "MATCH"
    parts = {p["partition"]: p for p in res.column_stats["partitions"]}
    assert parts["EU"]["row_count_left"] == 100
    assert parts["US"]["row_count_left"] == 100
