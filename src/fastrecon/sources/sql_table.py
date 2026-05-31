"""SQL table source — streams data through SQLAlchemy and registers as a view.

By default the loader uses a server-side cursor and pipes Arrow record
batches into DuckDB without materializing the full result set in Python.
Set ``streaming=False`` to fall back to the legacy eager fetchall path
(useful for drivers that don't support server-side cursors).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import duckdb

from ..exceptions import SourceError
from .base import Source
from ._sql_loader import load_via_sqlalchemy, load_via_sqlalchemy_eager


@dataclass
class SqlTable(Source):
    conn: str
    """SQLAlchemy connection URL (e.g. ``postgresql://...``, ``sqlite:///x.db``)."""

    table: str
    """Fully-qualified table name (``schema.table`` or ``table``)."""

    schema: Optional[str] = None
    chunk_size: int = 50_000
    streaming: bool = True
    """If ``True`` (default), stream Arrow batches via a server-side cursor.
    Set to ``False`` to use the legacy eager ``fetchall()`` loader."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        full = self.table if "." in self.table or not self.schema else f"{self.schema}.{self.table}"
        query = f"SELECT * FROM {full}"
        try:
            if self.streaming:
                obj = load_via_sqlalchemy(self.conn, query, chunk_size=self.chunk_size)
            else:
                obj = load_via_sqlalchemy_eager(self.conn, query)
            _materialize(con, view_name, obj)
        except Exception as e:
            raise SourceError(f"Failed to register SQL table {self.table!r}: {e}") from e
        return f'SELECT * FROM "{view_name}"'


def _materialize(con, view_name, obj) -> None:
    """Register an Arrow object into a re-scannable DuckDB table.

    DuckDB scans each registered view multiple times during a recon
    (DESCRIBE, COUNT, JOIN), so we materialize once into a DuckDB-managed
    columnar table. Memory stays bounded: the SQL cursor streams from the
    source DB and DuckDB stores the rows in compressed columnar form.
    """
    tmp = f"_fr_stream_{view_name}"
    con.register(tmp, obj)
    con.execute(f'CREATE OR REPLACE TABLE "{view_name}" AS SELECT * FROM "{tmp}"')
    try:
        con.unregister(tmp)
    except Exception:
        pass
