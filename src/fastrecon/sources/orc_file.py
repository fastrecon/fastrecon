"""ORC file source.

DuckDB doesn't read ORC natively (as of 1.x). We use ``pyarrow.orc``
(part of pyarrow, already a hard dep) to materialize as an Arrow table
and register that as a DuckDB view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import duckdb

from ..exceptions import SourceError
from .base import Source


@dataclass
class OrcFile(Source):
    path: str
    columns: Optional[List[str]] = field(default=None)
    """Optional column projection — read only these columns from disk."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            from pyarrow import orc as pa_orc  # type: ignore
        except ImportError as e:  # pragma: no cover - pyarrow.orc ships with pyarrow
            raise SourceError(
                "OrcFile requires pyarrow with the ORC module. Reinstall pyarrow: "
                "pip install --upgrade pyarrow"
            ) from e

        try:
            table = pa_orc.read_table(self.path, columns=self.columns)
        except Exception as e:
            raise SourceError(f"Failed to read ORC {self.path!r}: {e}") from e

        try:
            con.register(f"_arrow_{view_name}", table)
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f'SELECT * FROM "_arrow_{view_name}"'
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(f"Failed to register ORC {self.path!r}: {e}") from e
        return f'SELECT * FROM "{view_name}"'
