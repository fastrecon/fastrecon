"""Excel (.xlsx) file source backed by DuckDB's ``excel`` extension.

The extension is loaded lazily by the engine. Pass ``sheet=`` to pick a
specific worksheet (default: the first one). For workbook-wide compares
across multiple sheets, register one ``ExcelFile`` per sheet and run
``compare()`` for each pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import duckdb

from ..exceptions import SourceError
from .base import Source
from .csv_file import _render_sql_literal


@dataclass
class ExcelFile(Source):
    path: str
    sheet: Optional[str] = None
    """Worksheet name; defaults to the first sheet in the workbook."""
    has_header: bool = True

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            # The excel extension exposes read_xlsx(path, ...).
            args = [_render_sql_literal(self.path)]
            if self.sheet:
                args.append(f"sheet={_render_sql_literal(self.sheet)}")
            if not self.has_header:
                args.append("header=false")
            arg_str = ", ".join(args)
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f"SELECT * FROM read_xlsx({arg_str})"
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(
                f"Failed to register Excel {self.path!r} (sheet={self.sheet!r}): {e}"
            ) from e
        return f'SELECT * FROM "{view_name}"'
