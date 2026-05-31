"""Shared SQLAlchemy -> Arrow loader for SqlTable / SqlQuery sources.

Streams server-side cursor results into Arrow batches without buffering
the full result set. Two entry points:

* :func:`load_via_sqlalchemy` (default) — opens a streaming cursor
  (``stream_results=True``), discovers the column schema from
  ``cursor.description`` (Postgres OID mapping; falls back to first-batch
  inference for drivers that don't expose type codes), and returns a
  :class:`pyarrow.RecordBatchReader` that yields one ``RecordBatch`` per
  ``fetchmany(chunk_size)`` call. Memory stays bounded by ``chunk_size``.
* :func:`load_via_sqlalchemy_eager` — legacy ``fetchall`` path, kept as
  an opt-out for drivers that don't support server-side cursors.
"""

from __future__ import annotations

from typing import Iterator, List, Optional

import pyarrow as pa
from sqlalchemy import create_engine, text


# PostgreSQL OID -> Arrow type. Covers the common types reconciliation
# users encounter; anything else falls through to first-batch inference.
_PG_OID_TO_ARROW = {
    16:   pa.bool_(),                 # bool
    20:   pa.int64(),                 # int8
    21:   pa.int16(),                 # int2
    23:   pa.int32(),                 # int4
    25:   pa.string(),                # text
    700:  pa.float32(),               # float4
    701:  pa.float64(),               # float8
    1043: pa.string(),                # varchar
    # 1700 = numeric -> handled specially below using precision/scale
    1082: pa.date32(),                # date
    1114: pa.timestamp("us"),         # timestamp
    1184: pa.timestamp("us", tz="UTC"),  # timestamptz
    17:   pa.binary(),                # bytea
    2950: pa.string(),                # uuid -> string
    114:  pa.string(),                # json
    3802: pa.string(),                # jsonb
}

#: NUMERIC / DECIMAL OIDs across drivers that we map to Arrow decimal128.
_DECIMAL_OIDS = {1700}


def _schema_from_cursor(cursor, names: List[str]) -> Optional[pa.Schema]:
    """Build an Arrow schema from a DB-API ``cursor.description``.

    Returns ``None`` when the driver doesn't expose usable type codes
    (e.g. sqlite3) or when any column maps to an unknown OID — in which
    case the caller falls back to first-batch inference.

    NUMERIC/DECIMAL columns are mapped to ``pa.decimal128(precision, scale)``
    using the precision/scale fields from ``cursor.description`` so
    reconciliation does not lose precision. When precision is unknown or
    out of range for decimal128, returns ``None`` to force first-batch
    inference (which yields Python ``Decimal`` -> Arrow decimal128 with the
    actual values' precision).
    """
    desc = getattr(cursor, "description", None)
    if not desc:
        return None
    fields = []
    for col_name, col_desc in zip(names, desc):
        type_code = col_desc[1] if len(col_desc) > 1 else None
        if type_code is None or not isinstance(type_code, int):
            return None
        if type_code in _DECIMAL_OIDS:
            precision = col_desc[4] if len(col_desc) > 4 else None
            scale = col_desc[5] if len(col_desc) > 5 else None
            if (precision is None or scale is None or
                    not isinstance(precision, int) or not isinstance(scale, int) or
                    precision <= 0 or precision > 38 or scale < 0 or scale > precision):
                # Unconstrained NUMERIC: defer to first-batch inference, which
                # builds a decimal type from actual Decimal values.
                return None
            fields.append(pa.field(col_name, pa.decimal128(precision, scale), nullable=True))
            continue
        arrow_type = _PG_OID_TO_ARROW.get(type_code)
        if arrow_type is None:
            return None
        fields.append(pa.field(col_name, arrow_type, nullable=True))
    return pa.schema(fields)


def _batch_from_rows(rows, cols, schema: pa.Schema) -> pa.RecordBatch:
    """Build a ``RecordBatch`` matching ``schema`` from a list of row tuples."""
    arrays = []
    for i, name in enumerate(cols):
        col_data = [r[i] for r in rows]
        target = schema.field(i).type
        try:
            arrays.append(pa.array(col_data, type=target))
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            # Fall back: let pyarrow infer then cast (e.g. Decimal -> float64)
            inferred = pa.array(col_data)
            arrays.append(inferred.cast(target, safe=False))
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def load_via_sqlalchemy(
    conn_url: str,
    query: str,
    chunk_size: int = 50_000,
):
    """Stream ``query`` against ``conn_url`` as a :class:`RecordBatchReader`.

    The cursor uses ``stream_results=True`` so the driver does not buffer
    the whole result set client-side. Each :meth:`fetchmany` chunk is
    converted to a single ``RecordBatch`` matching a schema discovered up
    front from ``cursor.description``. When the driver doesn't expose
    type codes, the first batch is consumed eagerly to infer types and
    null-only columns are promoted to ``string`` — yielded batches still
    stream from there.
    """
    engine = create_engine(conn_url)
    connection = engine.connect().execution_options(stream_results=True)

    try:
        result = connection.execute(text(query))
    except Exception:
        connection.close()
        engine.dispose()
        raise

    cols = list(result.keys())
    schema = _schema_from_cursor(result.cursor, cols)

    first_rows = None
    if schema is None:
        # Fallback: infer from first batch
        first_rows = result.fetchmany(chunk_size)
        if not first_rows:
            schema = pa.schema([pa.field(c, pa.string(), nullable=True) for c in cols])
        else:
            data = {c: [r[i] for r in first_rows] for i, c in enumerate(cols)}
            inferred = pa.RecordBatch.from_pydict(data).schema
            promoted = []
            for f in inferred:
                t = f.type
                if pa.types.is_null(t):
                    t = pa.string()
                promoted.append(pa.field(f.name, t, nullable=True))
            schema = pa.schema(promoted)

    def _gen() -> Iterator[pa.RecordBatch]:
        try:
            if first_rows:
                yield _batch_from_rows(first_rows, cols, schema)
            while True:
                rows = result.fetchmany(chunk_size)
                if not rows:
                    break
                yield _batch_from_rows(rows, cols, schema)
        finally:
            try:
                result.close()
            except Exception:
                pass
            connection.close()
            engine.dispose()

    return pa.RecordBatchReader.from_batches(schema, _gen())


def load_via_sqlalchemy_eager(conn_url: str, query: str) -> pa.Table:
    """Legacy eager loader — runs ``query`` and ``fetchall`` into one Arrow table.

    Kept as an opt-out (``streaming=False`` on SqlTable/SqlQuery) for drivers
    that don't support server-side cursors or for very small result sets where
    per-batch overhead isn't worth it. Schema is inferred from the data; empty
    results fall back to all-string columns (use streaming for typed empties).
    """
    engine = create_engine(conn_url)
    try:
        with engine.connect() as connection:
            result = connection.execute(text(query))
            cols = list(result.keys())
            rows = result.fetchall()
    finally:
        engine.dispose()

    if not rows:
        return pa.table({c: pa.array([], type=pa.string()) for c in cols})
    data = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    return pa.table(data)
