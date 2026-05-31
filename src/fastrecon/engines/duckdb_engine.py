"""DuckDB execution engine — owns the connection and registered views."""

from __future__ import annotations

from typing import Dict, List, Tuple

import duckdb

from ..sources.base import Source


class DuckDBEngine:
    """Wraps a single in-memory DuckDB connection and tracks registered views."""

    def __init__(self) -> None:
        self.con = duckdb.connect(database=":memory:")
        # Reasonable defaults for analytical workloads
        try:
            self.con.execute("PRAGMA threads=4")
        except Exception:
            pass
        # Lazy-load the bundled extensions we depend on:
        #   - httpfs: enables s3://, gs://, azure://, https:// paths in
        #     CsvFile / ParquetFile / JsonFile (cloud storage support).
        #   - excel:  enables read_xlsx() for ExcelFile.
        # Both ship with DuckDB; INSTALL is a no-op after the first call,
        # and we swallow failures so air-gapped installs (no extension
        # repo access) still work for purely-local file + SQL recon.
        for ext in ("httpfs", "excel"):
            try:
                self.con.execute(f"INSTALL {ext}")
                self.con.execute(f"LOAD {ext}")
            except Exception:
                pass

    def register_source(self, source: Source, view_name: str) -> str:
        return source.register(self.con, view_name)

    def schema(self, view_name: str) -> List[Tuple[str, str]]:
        """Return ``[(column, duckdb_type), ...]`` for ``view_name``."""
        rows = self.con.execute(f'DESCRIBE SELECT * FROM "{view_name}"').fetchall()
        return [(r[0], r[1]) for r in rows]

    def schema_dict(self, view_name: str) -> Dict[str, str]:
        return dict(self.schema(view_name))

    def row_count(self, view_name: str) -> int:
        return int(self.con.execute(f'SELECT COUNT(*) FROM "{view_name}"').fetchone()[0])

    def execute(self, sql: str):
        return self.con.execute(sql)

    def fetchall(self, sql: str):
        return self.con.execute(sql).fetchall()

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass
