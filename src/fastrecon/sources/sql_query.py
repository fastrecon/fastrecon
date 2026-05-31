"""SQL query source — runs an arbitrary SELECT and registers the result."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from ..exceptions import SourceError
from .base import Source
from ._sql_loader import load_via_sqlalchemy, load_via_sqlalchemy_eager


@dataclass
class SqlQuery(Source):
    conn: str
    query: str
    chunk_size: int = 50_000
    streaming: bool = True
    """If ``True`` (default), stream Arrow batches. ``False`` uses the legacy eager loader."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            if self.streaming:
                obj = load_via_sqlalchemy(self.conn, self.query, chunk_size=self.chunk_size)
            else:
                obj = load_via_sqlalchemy_eager(self.conn, self.query)
            from .sql_table import _materialize
            _materialize(con, view_name, obj)
        except Exception as e:
            raise SourceError(f"Failed to execute SQL query: {e}") from e
        return f'SELECT * FROM "{view_name}"'
