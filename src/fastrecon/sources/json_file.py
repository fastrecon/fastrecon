"""JSON / NDJSON file source backed by DuckDB ``read_json_auto``.

Handles both newline-delimited JSON (the common analytics shape) and
top-level JSON arrays. ``path`` may be a glob (``data/*.json``) or a
remote URL (``s3://bucket/file.json``, ``https://...``) — DuckDB's
``httpfs`` extension is loaded by the engine on first use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import duckdb

from ..exceptions import SourceError
from .base import Source
from .csv_file import _render_sql_literal


@dataclass
class JsonFile(Source):
    path: str
    options: Dict[str, Any] = field(default_factory=dict)
    """Forwarded to ``read_json_auto`` (e.g. ``format='newline_delimited'``)."""

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
                f"SELECT * FROM read_json_auto({args})"
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(f"Failed to register JSON {self.path!r}: {e}") from e
        return f'SELECT * FROM "{view_name}"'
