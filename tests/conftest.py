"""Shared pytest fixtures.

Boots a real PostgreSQL instance for integration tests when the
``postgres`` binary is available. The fixture is session-scoped and
shuts down at exit. Tests that depend on Postgres are skipped if the
binary is not available (e.g. CI environments without nix/apt).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Allow `import fastrecon` to work regardless of editable install / PYTHONPATH.
# This complements the [tool.pytest.ini_options] pythonpath = ["src"] config
# in pyproject.toml so the suite runs from a clean checkout.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _find_pg_bin() -> str | None:
    for cand in ("pg_ctl", "/usr/lib/postgresql/16/bin/pg_ctl",
                 "/usr/lib/postgresql/15/bin/pg_ctl"):
        path = shutil.which(cand) if "/" not in cand else (cand if Path(cand).exists() else None)
        if path:
            return str(Path(path).parent)
    # Nix store fallback (Replit environment)
    import glob
    for p in sorted(glob.glob("/nix/store/*-postgresql-1*/bin/pg_ctl"), reverse=True):
        return str(Path(p).parent)
    return None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Return a SQLAlchemy URL for a session-scoped local Postgres.

    Skips the test if no postgres binary is found.
    """
    pgbin = _find_pg_bin()
    if not pgbin:
        pytest.skip("postgres binary not available")

    pgdata = Path("/tmp/fr_pg_pytest_data")
    pgsock = Path("/tmp/fr_pg_pytest_sock")
    if pgdata.exists():
        shutil.rmtree(pgdata)
    if pgsock.exists():
        shutil.rmtree(pgsock)
    pgsock.mkdir(parents=True)

    port = _free_port()
    subprocess.run(
        [f"{pgbin}/initdb", "-D", str(pgdata), "-U", "postgres", "--auth=trust"],
        check=True, capture_output=True,
    )
    log = Path("/tmp/fr_pg_pytest.log")
    subprocess.run(
        [f"{pgbin}/pg_ctl", "-D", str(pgdata),
         "-o", f"-k {pgsock} -h 127.0.0.1 -p {port}",
         "-l", str(log), "start"],
        check=True, capture_output=True, timeout=30,
    )

    # Wait for ready
    for _ in range(40):
        ready = subprocess.run(
            [f"{pgbin}/pg_isready", "-h", "127.0.0.1", "-p", str(port)],
            capture_output=True,
        )
        if ready.returncode == 0:
            break
        time.sleep(0.25)
    else:
        subprocess.run([f"{pgbin}/pg_ctl", "-D", str(pgdata), "stop", "-m", "immediate"],
                       capture_output=True)
        pytest.skip("postgres failed to start")

    subprocess.run(
        [f"{pgbin}/psql", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
         "-d", "postgres", "-c", "CREATE DATABASE fastrecon_test;"],
        check=True, capture_output=True,
    )

    url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/fastrecon_test"
    yield url

    subprocess.run([f"{pgbin}/pg_ctl", "-D", str(pgdata), "stop", "-m", "immediate"],
                   capture_output=True)
