"""Unit tests for the native Postgres source.

A live Postgres instance isn't available in this environment, so we
verify the URL parser and module wiring here. Integration testing
against a real Postgres (via testcontainers or compose) lives in the
follow-up that adds Snowflake / BigQuery / Delta sources.
"""

from __future__ import annotations

import pytest

from fastrecon import PostgresSource
from fastrecon.exceptions import SourceError
from fastrecon.sources.postgres_scanner import _to_libpq


def test_to_libpq_parses_full_sqlalchemy_url():
    out = _to_libpq("postgresql://alice:s3cret@db.example.com:5432/orders")
    assert "host='db.example.com'" in out
    assert "port='5432'" in out
    assert "dbname='orders'" in out
    assert "user='alice'" in out
    assert "password='s3cret'" in out


def test_to_libpq_handles_postgres_scheme():
    out = _to_libpq("postgres://u:p@h/db")
    assert "host='h'" in out and "user='u'" in out and "dbname='d'" not in out
    assert "dbname='db'" in out


def test_to_libpq_preserves_query_parameters():
    """sslmode / connect_timeout / etc. must round-trip into libpq keywords."""
    out = _to_libpq(
        "postgresql://u:p@h:5432/db?sslmode=require&connect_timeout=10&application_name=fastrecon"
    )
    assert "sslmode='require'" in out
    assert "connect_timeout='10'" in out
    assert "application_name='fastrecon'" in out


def test_to_libpq_escapes_special_characters():
    """Passwords containing spaces / quotes / backslashes must be escaped."""
    out = _to_libpq("postgresql://u:p@ss%27word@h/db")  # %27 = '
    # The literal single quote in the password is backslash-escaped
    assert "password='p@ss\\'word'" in out, out


def test_to_libpq_passthrough_when_already_libpq():
    raw = "host=h port=5432 dbname=d user=u password=p"
    assert _to_libpq(raw) == raw


def test_to_libpq_rejects_non_postgres_scheme():
    with pytest.raises(SourceError):
        _to_libpq("mysql://u:p@h/db")


def test_postgres_source_requires_table_or_query():
    """Construct a source with neither table nor query — register() must error."""
    src = PostgresSource(conn="postgresql://u:p@h/db")
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        with pytest.raises(SourceError, match="table.*query"):
            src.register(con, "v")
    finally:
        con.close()


def test_postgres_source_is_exported_from_top_level():
    """Public API contract: import path must be stable for users."""
    import fastrecon
    assert fastrecon.PostgresSource is PostgresSource
    assert "PostgresSource" in fastrecon.__all__
