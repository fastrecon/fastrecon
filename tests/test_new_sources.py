"""Smoke tests for the 0.6.0 source additions: XML, Avro, ORC, Mainframe,
plus the ``source(path)`` auto-detect helper."""

from __future__ import annotations

import struct
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.orc as pa_orc
import pyarrow.parquet as pa_pq
import pytest

from fastrecon import (
    AvroFile,
    MainframeFile,
    OrcFile,
    XmlFile,
    compare,
    source,
)


# ---------- XML ----------

def test_xml_explicit_columns(tmp_path: Path):
    p = tmp_path / "orders.xml"
    p.write_text(
        """<?xml version="1.0"?>
<orders>
  <order id="1"><amount>10.0</amount></order>
  <order id="2"><amount>20.0</amount></order>
</orders>"""
    )
    src = XmlFile(
        path=str(p),
        record_path="order",
        columns={"id": "./@id", "amount": "./amount"},
    )
    res = compare(src, src, keys=["id"])
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 2

def test_xml_auto_columns_diff(tmp_path: Path):
    a = tmp_path / "a.xml"
    b = tmp_path / "b.xml"
    a.write_text("<root><r><id>1</id><v>x</v></r><r><id>2</id><v>y</v></r></root>")
    b.write_text("<root><r><id>1</id><v>x</v></r><r><id>2</id><v>Z</v></r></root>")
    res = compare(
        XmlFile(path=str(a), record_path="r"),
        XmlFile(path=str(b), record_path="r"),
        keys=["id"],
    )
    assert res.changed_rows == 1, res.summary()


# ---------- Avro ----------

def test_avro_roundtrip(tmp_path: Path):
    fastavro = pytest.importorskip("fastavro")
    schema = {
        "type": "record", "name": "Row",
        "fields": [
            {"name": "id", "type": "int"},
            {"name": "name", "type": ["null", "string"], "default": None},
        ],
    }
    p = tmp_path / "data.avro"
    rows = [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
    with open(p, "wb") as fh:
        fastavro.writer(fh, schema, rows)
    res = compare(AvroFile(path=str(p)), AvroFile(path=str(p)), keys=["id"])
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 2


# ---------- ORC ----------

def test_orc_roundtrip(tmp_path: Path):
    table = pa.table({"id": [1, 2, 3], "v": ["a", "b", "c"]})
    p = tmp_path / "t.orc"
    pa_orc.write_table(table, str(p))
    res = compare(OrcFile(path=str(p)), OrcFile(path=str(p)), keys=["id"])
    assert res.status == "MATCH"
    assert res.row_count_left == 3


# ---------- Mainframe / EBCDIC + COMP-3 ----------

def _comp3_encode(value: int, length: int, negative: bool = False) -> bytes:
    """Pack ``value`` (positive int; sign passed separately) into ``length``
    bytes of COMP-3. Mirrors the decoder so we can roundtrip in tests."""
    digits = str(abs(value))
    needed = length * 2 - 1
    digits = digits.rjust(needed, "0")
    sign_nibble = 0x0D if negative else 0x0C
    nibbles = [int(d) for d in digits] + [sign_nibble]
    out = bytearray()
    for i in range(0, len(nibbles), 2):
        out.append((nibbles[i] << 4) | nibbles[i + 1])
    return bytes(out)


def test_mainframe_ebcdic_text_and_comp3(tmp_path: Path):
    # Build a 20-byte fixed-record EBCDIC file with:
    #   bytes  1-10: account id (EBCDIC text, space-padded)
    #   bytes 11-15: balance (5-byte COMP-3, scale=2)
    #   bytes 16-19: txn count (4-byte big-endian binary)
    #   byte    20:  status (EBCDIC 'A' or 'B')
    def record(acct: str, bal_cents: int, neg: bool, txn: int, status: str) -> bytes:
        acct_b = acct.ljust(10).encode("cp037")
        bal_b = _comp3_encode(bal_cents, 5, negative=neg)
        txn_b = struct.pack(">i", txn)
        st_b = status.encode("cp037")
        rec = acct_b + bal_b + txn_b + st_b
        assert len(rec) == 20, len(rec)
        return rec

    p = tmp_path / "ACCT.DAT"
    rows = [
        record("ACC0000001", 12345, False, 7, "A"),
        record("ACC0000002", 99999, True, 42, "B"),
    ]
    p.write_bytes(b"".join(rows))

    src = MainframeFile(
        path=str(p),
        record_length=20,
        encoding="cp037",
        fields=[
            {"name": "acct", "start": 1, "length": 10, "type": "text"},
            {"name": "balance", "start": 11, "length": 5, "type": "comp3", "scale": 2},
            {"name": "txn", "start": 16, "length": 4, "type": "binary"},
            {"name": "status", "start": 20, "length": 1, "type": "text"},
        ],
    )
    # Round-trip: same file vs itself must match.
    res = compare(src, src, keys=["acct"])
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 2

    # Spot-check decoded values via DuckDB directly.
    import duckdb
    con = duckdb.connect()
    src.register(con, "v")
    rows_out = con.execute(
        'SELECT acct, balance, txn, status FROM "v" ORDER BY acct'
    ).fetchall()
    assert rows_out[0][0] == "ACC0000001"
    assert Decimal(str(rows_out[0][1])) == Decimal("123.45")
    assert rows_out[0][2] == 7
    assert rows_out[0][3] == "A"
    assert Decimal(str(rows_out[1][1])) == Decimal("-999.99")


# ---------- source(path) auto-detect ----------

def test_source_autodetect_extensions(tmp_path: Path):
    csv = tmp_path / "x.csv"
    csv.write_text("id,v\n1,a\n2,b\n")
    pq = tmp_path / "x.parquet"
    pa_pq.write_table(pa.table({"id": [1, 2], "v": ["a", "b"]}), str(pq))

    res = compare(source(str(csv)), source(str(pq)), keys=["id"])
    assert res.status == "MATCH", res.summary()


def test_source_unknown_extension():
    from fastrecon.exceptions import SourceError
    with pytest.raises(SourceError):
        source("/tmp/nope.unknownext")


# ---------- regression: 0.6.1 review fixes ----------

def test_source_autodetect_tsv_psv(tmp_path: Path):
    # 0.6.0 bug: source('*.tsv') / source('*.psv') passed an unsupported
    # ``delimiter=`` kwarg into CsvFile and crashed at construction.
    tsv = tmp_path / "a.tsv"
    tsv.write_text("id\tv\n1\ta\n2\tb\n")
    psv = tmp_path / "b.psv"
    psv.write_text("id|v\n1|a\n2|b\n")
    res = compare(source(str(tsv)), source(str(psv)), keys=["id"])
    assert res.status == "MATCH", res.summary()
    assert res.row_count_left == 2


def test_avro_empty_file_keeps_schema_columns(tmp_path: Path):
    # 0.6.0 bug: an empty Avro file replaced schema columns with a
    # placeholder ``_empty`` column, so a downstream compare on key
    # ``id`` blew up. The schema must drive column order even when no
    # rows are present.
    fastavro = pytest.importorskip("fastavro")
    schema = {
        "type": "record", "name": "Row",
        "fields": [
            {"name": "id", "type": "int"},
            {"name": "v", "type": "string"},
        ],
    }
    p = tmp_path / "empty.avro"
    with open(p, "wb") as fh:
        fastavro.writer(fh, schema, [])

    import duckdb
    con = duckdb.connect()
    AvroFile(path=str(p)).register(con, "v")
    desc = con.execute('SELECT * FROM "v"').description
    cols = [d[0] for d in desc]
    assert cols == ["id", "v"], cols
    assert con.execute('SELECT COUNT(*) FROM "v"').fetchone()[0] == 0


def test_avro_union_flatten_preserves_user_records():
    # 0.6.0 bug: any single-key dict was unwrapped, corrupting
    # legitimate user records like {"address": {...}}.
    from fastrecon.sources.avro_file import _is_avro_union_wrapper
    # Avro union envelopes: should flatten.
    assert _is_avro_union_wrapper({"string": "hi"})
    assert _is_avro_union_wrapper({"int": 5})
    assert _is_avro_union_wrapper({"com.example.Address": {"zip": "94110"}})
    # User payloads with one key that isn't an Avro type: must NOT flatten.
    assert not _is_avro_union_wrapper({"address": {"zip": "94110"}})
    assert not _is_avro_union_wrapper({"name": "alice"})
    # Non-dict / multi-key / empty: never flatten.
    assert not _is_avro_union_wrapper(None)
    assert not _is_avro_union_wrapper({"a": 1, "b": 2})
    assert not _is_avro_union_wrapper({})


def test_comp3_invalid_sign_nibble_returns_null():
    # 0.6.0 bug: any sign nibble != D was treated as positive. Invalid
    # nibbles like 0x05 must return None instead of mis-decoding.
    from fastrecon.sources.mainframe_file import _decode_comp3
    assert _decode_comp3(b"\x12\x3C") == Decimal("123")    # +123
    assert _decode_comp3(b"\x12\x3D") == Decimal("-123")   # -123
    assert _decode_comp3(b"\x12\x3F") == Decimal("123")    # +123 (F)
    assert _decode_comp3(b"\x12\x3A") == Decimal("123")    # +123 (A)
    assert _decode_comp3(b"\x12\x3B") == Decimal("-123")   # -123 (B)
    assert _decode_comp3(b"\x12\x35") is None              # invalid sign
    assert _decode_comp3(b"\x1A\x3C") is None              # invalid digit
    assert _decode_comp3(b"") is None                      # empty
