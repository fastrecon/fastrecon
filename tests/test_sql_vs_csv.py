"""SQL (sqlite) vs CSV reconciliation — exercises SqlTable/SqlQuery sources."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from fastrecon import CsvFile, SqlQuery, SqlTable, compare


def _seed_sqlite(db_path: Path) -> str:
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE orders (order_id INTEGER PRIMARY KEY, sku TEXT, qty INTEGER)"))
        c.execute(text("INSERT INTO orders VALUES (1,'A',10),(2,'B',20),(3,'C',30)"))
    eng.dispose()
    return url


def test_sql_table_vs_csv_match(tmp_path: Path):
    db = tmp_path / "x.db"
    url = _seed_sqlite(db)

    csv = tmp_path / "orders.csv"
    csv.write_text("order_id,sku,qty\n1,A,10\n2,B,20\n3,C,30\n")

    res = compare(
        SqlTable(conn=url, table="orders"),
        CsvFile(str(csv)),
        keys=["order_id"],
    )
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 3
    assert res.row_count_right == 3


def test_sql_query_vs_csv_detects_change(tmp_path: Path):
    db = tmp_path / "x.db"
    url = _seed_sqlite(db)

    csv = tmp_path / "orders.csv"
    csv.write_text("order_id,sku,qty\n1,A,10\n2,B,99\n3,C,30\n")

    res = compare(
        SqlQuery(conn=url, query="SELECT order_id, sku, qty FROM orders"),
        CsvFile(str(csv)),
        keys=["order_id"],
    )
    assert res.status == "MISMATCH"
    assert res.changed_rows == 1
