"""Postgres source backed by DuckDB's native ``postgres_scanner`` extension.

Pushes filters and projections directly to Postgres via libpq — no Python
materialization, no SQLAlchemy round-trip. Use this when both sides of a
recon involve large Postgres tables or when you want predicate pushdown
inside DuckDB-driven joins.

Requires DuckDB ≥ 0.10 with the ``postgres`` extension available
(``INSTALL postgres; LOAD postgres;`` is run lazily).

Each :class:`PostgresSource` registration derives a **unique attach alias**
from the caller-supplied ``view_name``, so two ``PostgresSource`` instances
pointing at different databases can coexist on the same DuckDB connection
(e.g. left/right of a ``compare()`` call) without clobbering each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import duckdb

from ..exceptions import SourceError
from .base import Source


_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


def _safe_alias(view_name: str) -> str:
    """Derive a DuckDB-safe attach alias from a view name."""
    sanitized = _IDENT_RE.sub("_", view_name) or "v"
    return f"fr_pg_{sanitized}"


def _libpq_quote(value: str) -> str:
    """Escape a value for libpq keyword=value syntax.

    libpq treats single-quoted values specially: the value is wrapped in
    single quotes and any embedded single quote or backslash is escaped
    with a backslash. Quoting is unconditional so values containing
    spaces / ``=`` / special chars are always safe.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _to_libpq(conn_url: str) -> str:
    """Convert a SQLAlchemy postgres URL to a libpq keyword string.

    URL query parameters (e.g. ``?sslmode=require&connect_timeout=10``)
    are preserved as additional libpq keywords, so production deployments
    that depend on TLS or timeout options still work through the scanner.
    Values are always single-quote escaped to defend against passwords
    or hostnames containing spaces / quotes / special characters.

    Examples
    --------
    >>> _to_libpq("postgresql://u:p@h:5432/db")
    "host='h' port='5432' dbname='db' user='u' password='p'"
    >>> _to_libpq("postgresql://u@h/db?sslmode=require&connect_timeout=10")
    "host='h' dbname='db' user='u' sslmode='require' connect_timeout='10'"
    """
    if " " in conn_url and "=" in conn_url:
        return conn_url  # already libpq-style; trust the caller
    u = urlparse(conn_url)
    if u.scheme not in ("postgres", "postgresql", "postgresql+psycopg",
                        "postgresql+psycopg2", "postgresql+psycopg3"):
        raise SourceError(f"Unsupported postgres URL scheme: {u.scheme!r}")
    parts = []
    from urllib.parse import unquote, parse_qsl
    if u.hostname: parts.append(f"host={_libpq_quote(u.hostname)}")
    if u.port:     parts.append(f"port={_libpq_quote(str(u.port))}")
    if u.path and u.path != "/":
        parts.append(f"dbname={_libpq_quote(unquote(u.path.lstrip('/')))}")
    if u.username: parts.append(f"user={_libpq_quote(unquote(u.username))}")
    if u.password: parts.append(f"password={_libpq_quote(unquote(u.password))}")
    if u.query:
        for k, v in parse_qsl(u.query, keep_blank_values=True):
            parts.append(f"{k}={_libpq_quote(v)}")
    return " ".join(parts)


@dataclass
class PostgresSource(Source):
    """Native DuckDB <-> Postgres source (zero-copy via postgres_scanner).

    Either ``table`` or ``query`` must be set. ``table`` is preferred because
    it lets DuckDB push down filters; use ``query`` only when you need
    arbitrary SQL.
    """

    conn: str
    """SQLAlchemy URL (``postgresql://...``) or libpq keyword string."""

    table: Optional[str] = None
    """Table name; may be schema-qualified (``public.orders``)."""

    query: Optional[str] = None
    """Arbitrary SELECT to wrap when ``table`` is not suitable."""

    schema: Optional[str] = None
    #: Optional alias override. When ``None`` (default), an alias unique to
    #: each ``register`` call is derived from ``view_name`` so two
    #: ``PostgresSource`` instances on the same DuckDB connection never
    #: clobber each other. Pin this only when you explicitly want to share
    #: an attached database across multiple sources.
    attach_alias: Optional[str] = None

    #: Class-level diagnostic — set to ``"scanner"`` every time
    #: :meth:`register` succeeds via the native postgres extension. Tests
    #: read this to assert no Python row marshalling occurred for a given
    #: compare. Reset to ``None`` before each compare you want to assert on.
    last_register_path: Optional[str] = None

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        if not self.table and not self.query:
            raise SourceError("PostgresSource requires either `table` or `query`")
        alias = self.attach_alias or _safe_alias(view_name)
        try:
            try:
                con.execute("INSTALL postgres;")
            except Exception:
                pass
            con.execute("LOAD postgres;")
            libpq = _to_libpq(self.conn)
            # Detach only *this* source's alias if it lingers from a prior
            # registration of the same view. Never touch other sources' aliases.
            try:
                con.execute(f"DETACH DATABASE {alias};")
            except Exception:
                pass
            # Escape any single quotes in the libpq string before wrapping
            # it as a SQL string literal for ATTACH. Defends against
            # passwords / hosts containing quote characters.
            attach_literal = "'" + libpq.replace("'", "''") + "'"
            con.execute(
                f"ATTACH {attach_literal} AS {alias} (TYPE postgres, READ_ONLY);"
            )
            if self.table:
                full = self.table if "." in self.table else (
                    f"{self.schema}.{self.table}" if self.schema else f"public.{self.table}"
                )
                con.execute(
                    f'CREATE OR REPLACE VIEW "{view_name}" AS '
                    f"SELECT * FROM {alias}.{full};"
                )
            else:
                con.execute(
                    f'CREATE OR REPLACE VIEW "{view_name}" AS '
                    f"SELECT * FROM postgres_query({alias!r}, {self.query!r});"
                )
        except Exception as e:
            raise SourceError(f"Failed to attach Postgres source: {e}") from e
        type(self).last_register_path = "scanner"
        return f'SELECT * FROM "{view_name}"'
