"""CSV file source backed by DuckDB ``read_csv_auto``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import duckdb

from ..exceptions import SourceError
from .base import Source


@dataclass
class CsvFile(Source):
    path: str
    options: Dict[str, Any] = field(default_factory=dict)
    """Extra kwargs forwarded to DuckDB ``read_csv_auto`` (e.g. ``delim=';'``)."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            opts = ", ".join(
                f"{k}={_render_sql_literal(v)}" for k, v in self.options.items()
            )
            args = _render_sql_literal(self.path)
            if opts:
                args = f"{args}, {opts}"
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f"SELECT * FROM read_csv_auto({args})"
            )
        except Exception as e:  # pragma: no cover - DuckDB errors
            raise SourceError(f"Failed to register CSV {self.path!r}: {e}") from e
        return f'SELECT * FROM "{view_name}"'


def _render_sql_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"
