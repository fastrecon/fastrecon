"""Avro file source.

DuckDB has no native Avro reader, so we stream the file with
``fastavro`` (pure-Python, no JVM), materialize as an Arrow table, and
register it as a DuckDB view. Schema lives in the file itself, so no
spec is required.

Install with the optional extra::

    pip install 'fastrecon[avro]'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import duckdb

from ..exceptions import SourceError
from .base import Source


@dataclass
class AvroFile(Source):
    path: str
    batch_size: int = 100_000
    """Records buffered in memory at a time when building the Arrow
    table. Lower for tiny machines; higher for fewer Python round-trips
    on huge files."""
    flatten_unions: bool = True
    """If True, ``union {null, X}`` fields collapse to ``X | None`` (the
    common analytics shape). Only dicts whose single key matches an Avro
    type name (``string``, ``int``, ``long``, ``float``, ``double``,
    ``boolean``, ``bytes``, ``fixed``, ``array``, ``map``) are unwrapped
    — legitimate single-key user records/maps are left alone. Set False
    to keep raw nested dicts."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            import fastavro  # type: ignore
        except ImportError as e:
            raise SourceError(
                "AvroFile requires the 'fastavro' package. Install with: "
                "pip install 'fastrecon[avro]'"
            ) from e
        try:
            import pyarrow as pa
        except ImportError as e:  # pragma: no cover
            raise SourceError("pyarrow is required for AvroFile") from e

        try:
            with open(self.path, "rb") as fh:
                reader = fastavro.reader(fh)
                schema = reader.writer_schema
                rows = list(reader)
        except Exception as e:
            raise SourceError(f"Failed to read Avro {self.path!r}: {e}") from e

        # Prefer the writer schema for column order so an empty file
        # still registers a view with the expected key columns rather
        # than a synthetic ``_empty`` placeholder. Fall back to scanning
        # rows if the schema isn't a record (rare).
        col_order: list = []
        if isinstance(schema, dict) and schema.get("type") == "record":
            col_order = [f["name"] for f in schema.get("fields", [])]
        else:
            seen = set()
            for r in rows:
                for k in r.keys():
                    if k not in seen:
                        seen.add(k)
                        col_order.append(k)

        col_data = {c: [] for c in col_order}
        for r in rows:
            for c in col_order:
                v = r.get(c)
                if self.flatten_unions and _is_avro_union_wrapper(v):
                    v = next(iter(v.values()))
                col_data[c].append(v)

        if not col_order:
            table = pa.table({"_empty": pa.array([], type=pa.string())})
        else:
            try:
                table = pa.table(col_data)
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                # Mixed types within a column — fall back to string.
                table = pa.table(
                    {c: pa.array([_stringify(v) for v in vals], type=pa.string())
                     for c, vals in col_data.items()}
                )

        try:
            con.register(f"_arrow_{view_name}", table)
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f'SELECT * FROM "_arrow_{view_name}"'
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(f"Failed to register Avro {self.path!r}: {e}") from e
        return f'SELECT * FROM "{view_name}"'


_AVRO_TYPE_NAMES = frozenset({
    "null", "boolean", "int", "long", "float", "double",
    "bytes", "string", "fixed", "array", "map", "enum",
})


def _is_avro_union_wrapper(v) -> bool:
    """True only when ``v`` looks like a fastavro non-null union envelope
    (``{<avro-type-name>: value}``). Avoids mis-flattening legitimate
    user records/maps that happen to have one key."""
    if not isinstance(v, dict) or len(v) != 1:
        return False
    only_key = next(iter(v.keys()))
    if not isinstance(only_key, str):
        return False
    # Primitive type names, plus dotted/qualified record names (e.g.
    # ``com.example.User``). User maps with bare alphabetic keys that
    # don't match a known Avro type are left untouched.
    if only_key in _AVRO_TYPE_NAMES:
        return True
    return "." in only_key  # qualified record name like 'ns.RecordName'


def _stringify(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.hex()
    return str(v)
