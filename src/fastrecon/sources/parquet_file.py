"""Parquet file (or glob) source backed by DuckDB ``read_parquet``."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from ..exceptions import SourceError
from .base import Source
from .csv_file import _render_sql_literal


@dataclass
class ParquetFile(Source):
    path: str
    """Single path or DuckDB glob, e.g. ``'data/*.parquet'``."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f"SELECT * FROM read_parquet({_render_sql_literal(self.path)})"
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(f"Failed to register Parquet {self.path!r}: {e}") from e
        return f'SELECT * FROM "{view_name}"'
