"""Fixed-width text file source.

Mainframe / banking exports are still everywhere. We read the file as
one ``line`` column via DuckDB's CSV reader (with a delimiter that won't
appear), then carve substrings out per the user-provided column spec.

Parsing is lossless by default — leading/trailing spaces inside each
fixed-width slot are preserved. Set ``trim_values=True`` to strip them
(common with COBOL exports where numeric fields are space-padded).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import duckdb

from ..exceptions import SourceError
from .base import Source
from .csv_file import _render_sql_literal

ColSpec = Tuple[str, int, int]
"""``(column_name, start_1_indexed, length)``."""


def _quote_ident(name: str) -> str:
    """Quote an identifier for DuckDB (double-quote, escape internal quotes)."""
    return '"' + name.replace('"', '""') + '"'


@dataclass
class FixedWidthFile(Source):
    path: str
    columns: List[ColSpec] = field(default_factory=list)
    """One ``(name, start, length)`` tuple per output column. ``start`` is
    1-indexed (matches COBOL / RPG conventions); ``length`` is in
    characters. Columns may overlap if the layout requires it."""
    skip_rows: int = 0
    """Number of header lines to skip before parsing data."""
    trim_values: bool = False
    """If True, ``TRIM()`` each extracted slot. Off by default so parsing
    is lossless — turn it on when your producer space-pads numeric
    fields and you don't want those spaces to count as differences."""
    encoding: str = "utf-8"

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        if not self.columns:
            raise SourceError(
                f"FixedWidthFile {self.path!r}: at least one column spec required"
            )
        for name, start, length in self.columns:
            if start < 1:
                raise SourceError(
                    f"FixedWidthFile column {name!r}: start must be >=1, got {start}"
                )
            if length < 1:
                raise SourceError(
                    f"FixedWidthFile column {name!r}: length must be >=1, got {length}"
                )

        # Read every line as a single string column. Use a delimiter that
        # is illegal in real fixed-width data so the whole line lands in
        # one cell; \x1F (Unit Separator) is the canonical choice.
        try:
            select_parts = []
            for name, start, length in self.columns:
                expr = f"SUBSTRING(line, {int(start)}, {int(length)})"
                if self.trim_values:
                    expr = f"TRIM({expr})"
                select_parts.append(f"{expr} AS {_quote_ident(name)}")
            select_clause = ",\n  ".join(select_parts)
            skip_clause = f", skip={int(self.skip_rows)}" if self.skip_rows else ""
            path_lit = _render_sql_literal(self.path)
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f"SELECT\n  {select_clause}\n"
                f"FROM read_csv({path_lit}, "
                f"columns={{'line': 'VARCHAR'}}, "
                f"delim='\\x1F', header=false, quote='', escape=''"
                f"{skip_clause})"
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(
                f"Failed to register FixedWidth {self.path!r}: {e}"
            ) from e
        return f'SELECT * FROM "{view_name}"'
