"""ODBC query source — runs a SELECT through a pyodbc connection.

Use when the target system has no native SQLAlchemy driver but does
have an ODBC driver installed (mainframe DB2, Sybase, Teradata,
Vertica, Informix, etc.). The query result is materialized once into
DuckDB for re-scannable comparison.

Install the optional dependency::

    pip install fastrecon[odbc]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import duckdb

from ..exceptions import SourceError
from .base import Source


@dataclass
class OdbcQuery(Source):
    conn_str: str
    """Standard ODBC connection string. Example::

        DRIVER={ODBC Driver 18 for SQL Server};SERVER=host;DATABASE=db;UID=u;PWD=p
    """

    query: str
    """SQL ``SELECT`` statement to execute."""

    chunk_size: int = 50_000
    """Reserved for future chunked fetch. Currently materializes once."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            import pyodbc  # type: ignore
        except ImportError as e:  # pragma: no cover - optional dep
            raise SourceError(
                "OdbcQuery requires pyodbc. Install with: pip install fastrecon[odbc]"
            ) from e
        try:
            import pandas as pd  # type: ignore
        except ImportError as e:  # pragma: no cover - optional dep
            raise SourceError(
                "OdbcQuery requires pandas. Install with: pip install fastrecon[odbc]"
            ) from e

        try:
            cnx = pyodbc.connect(self.conn_str)
            try:
                df = pd.read_sql(self.query, cnx)
            finally:
                cnx.close()
            from .sql_table import _materialize
            _materialize(con, view_name, df)
        except SourceError:
            raise
        except Exception as e:
            raise SourceError(f"ODBC query failed: {e}") from e
        return f'SELECT * FROM "{view_name}"'
