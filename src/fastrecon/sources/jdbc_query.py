"""JDBC query source — runs a SELECT through a JDBC driver via JayDeBeApi.

Use when only a JDBC driver is available for the target system (some
mainframe DB2 setups, Hive, Impala, Sybase ASE/IQ, Greenplum, custom
in-house JDBC drivers, etc.). Requires a JVM on the host because the
driver is a ``.jar``.

Install the optional dependency::

    pip install fastrecon[jdbc]

You also need to download the vendor's JDBC ``.jar`` and pass its path
in ``driver_jar``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union

import duckdb

from ..exceptions import SourceError
from .base import Source


@dataclass
class JdbcQuery(Source):
    jdbc_url: str
    """JDBC URL, e.g. ``jdbc:db2://host:50000/MYDB``."""

    driver_class: str
    """Fully-qualified Java class, e.g. ``com.ibm.db2.jcc.DB2Driver``."""

    driver_jar: Union[str, List[str]] = ""
    """Path (or list of paths) to the JDBC ``.jar`` file(s)."""

    query: str = ""
    """SQL ``SELECT`` statement to execute."""

    user: Optional[str] = None
    password: Optional[str] = None
    properties: dict = field(default_factory=dict)
    """Extra driver properties passed to ``jaydebeapi.connect``."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            import jaydebeapi  # type: ignore
        except ImportError as e:  # pragma: no cover - optional dep
            raise SourceError(
                "JdbcQuery requires jaydebeapi. Install with: pip install fastrecon[jdbc]. "
                "A working JVM is also required (set JAVA_HOME)."
            ) from e
        try:
            import pandas as pd  # type: ignore
        except ImportError as e:  # pragma: no cover - optional dep
            raise SourceError(
                "JdbcQuery requires pandas. Install with: pip install fastrecon[jdbc]"
            ) from e

        if not self.query:
            raise SourceError("JdbcQuery.query is required")
        if not self.driver_jar:
            raise SourceError("JdbcQuery.driver_jar is required (path to vendor JDBC .jar)")

        # JayDeBeApi accepts either (user, password) tuple or a properties
        # dict. Build the most generous form so callers can pass either.
        creds: object
        if self.properties:
            props = dict(self.properties)
            if self.user is not None:
                props.setdefault("user", self.user)
            if self.password is not None:
                props.setdefault("password", self.password)
            creds = props
        elif self.user is not None or self.password is not None:
            creds = [self.user or "", self.password or ""]
        else:
            creds = None

        try:
            cnx = jaydebeapi.connect(self.driver_class, self.jdbc_url, creds, self.driver_jar)
            try:
                df = pd.read_sql(self.query, cnx)
            finally:
                cnx.close()
            from .sql_table import _materialize
            _materialize(con, view_name, df)
        except SourceError:
            raise
        except Exception as e:
            raise SourceError(f"JDBC query failed: {e}") from e
        return f'SELECT * FROM "{view_name}"'
