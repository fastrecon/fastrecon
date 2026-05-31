"""``fastrecon`` command-line interface.

Built with `typer`. Drop into any CI pipeline:

    fastrecon compare \\
        --left  csv:./left.csv \\
        --right csv:./right.csv \\
        --keys order_id \\
        --tolerance amount=0.01 \\
        --partition region:value \\
        --report html:./report.html \\
        --report junit:./report.xml \\
        --fail-on mismatch

Source URI grammar (passed to ``--left`` / ``--right``):

* ``csv:<path>``
* ``parquet:<path>``
* ``sqltable:<sqlalchemy_url>#<table>``
* ``sqlquery:<sqlalchemy_url>#<SELECT ...>``
* ``postgres:<sqlalchemy_url>#<table>``  (native scanner, no Python rows)
* ``postgres-query:<sqlalchemy_url>#<SELECT ...>``

Each ``--report`` flag is ``<format>:<path>`` where format is one of
``html``, ``junit``, or ``json``. The flag is repeatable.

The legacy verbose flags (``--left-type csv --left-path ...``) remain
supported for backwards compatibility with 0.3.x scripts.
"""

from __future__ import annotations

import enum
import logging
import sys
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.logging import RichHandler


class FailOn(str, enum.Enum):
    NEVER = "never"
    MISMATCH = "mismatch"
    ERROR = "error"


class CompareMode(str, enum.Enum):
    SCHEMA = "schema"
    ROWCOUNT = "rowcount"
    KEYED = "keyed"
    PROFILE = "profile"
    HASH = "hash"

from . import (
    CsvFile, ExcelFile, FixedWidthFile, JsonFile, ParquetFile,
    PartitionSpec, ReconConfig, SqlQuery, SqlTable, compare,
)

app = typer.Typer(
    name="fastrecon",
    help="High-performance reconciliation engine. Run `fastrecon compare --help`.",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """fastrecon — drop reconciliation into your CI pipeline."""
    # Forcing a callback turns this into a multi-command app, so the
    # `compare` subcommand keeps its name even when it's the only one.
    return None

log = logging.getLogger("fastrecon.cli")


# --------------------------------------------------------------- URI parsing
def _parse_source_uri(uri: str):
    """Parse ``kind:rest[#extra]`` into a Source instance.

    The fragment (``#``) carries the table name or SQL query for SQL sources;
    splitting on ``#`` after ``rsplit`` lets users embed ``?sslmode=require``
    inside the SQL URL without ambiguity.
    """
    if ":" not in uri:
        raise typer.BadParameter(
            f"source URI must be 'kind:value', got {uri!r}; "
            "see `fastrecon compare --help` for the grammar"
        )
    kind, rest = uri.split(":", 1)
    kind = kind.strip().lower()

    if kind == "csv":
        return CsvFile(rest)
    if kind == "tsv":
        return CsvFile(rest, options={"delim": "\t"})
    if kind == "parquet":
        return ParquetFile(rest)
    if kind == "json":
        return JsonFile(rest)
    if kind == "excel":
        # excel:<path>[#<sheet>]; sheet is optional
        if "#" in rest:
            path, sheet = rest.rsplit("#", 1)
            return ExcelFile(path, sheet=sheet)
        return ExcelFile(rest)
    if kind in ("fixedwidth", "fixed"):
        # fixedwidth:<path>#<col1:start:len,col2:start:len,...>
        if "#" not in rest:
            raise typer.BadParameter(
                f"{kind!r} source needs '#col1:start:len,col2:start:len,...' suffix"
            )
        path, spec = rest.rsplit("#", 1)
        cols = []
        for part in spec.split(","):
            try:
                name, start, length = part.split(":")
                cols.append((name.strip(), int(start), int(length)))
            except ValueError as e:
                raise typer.BadParameter(
                    f"fixedwidth column spec must be name:start:len, got {part!r}"
                ) from e
        return FixedWidthFile(path, columns=cols)

    # SQL-flavored sources: split on the LAST '#' to separate URL and tail
    if "#" not in rest:
        raise typer.BadParameter(
            f"{kind!r} source needs '#<table-or-query>' suffix; got {uri!r}"
        )
    url, tail = rest.rsplit("#", 1)

    if kind == "sqltable":
        return SqlTable(conn=url, table=tail)
    if kind == "sqlquery":
        return SqlQuery(conn=url, query=tail)
    if kind == "postgres":
        from .sources.postgres_scanner import PostgresSource
        return PostgresSource(conn=url, table=tail)
    if kind in ("postgres-query", "postgresquery"):
        from .sources.postgres_scanner import PostgresSource
        return PostgresSource(conn=url, query=tail)

    raise typer.BadParameter(f"unknown source kind: {kind!r}")


def _parse_tolerance(items: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for it in items or []:
        if "=" not in it:
            raise typer.BadParameter(f"--tolerance must be col=value, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError as e:
            raise typer.BadParameter(f"--tolerance value not numeric: {it!r}") from e
    return out


def _parse_partition(spec: Optional[str]) -> Optional[PartitionSpec]:
    if not spec:
        return None
    parts = spec.split(":", 2)
    col = parts[0]
    strat = parts[1] if len(parts) > 1 else "value"
    if strat == "value":
        return PartitionSpec(column=col, strategy="value")
    if strat == "hash":
        buckets = int(parts[2]) if len(parts) > 2 else 16
        return PartitionSpec(column=col, strategy="hash", buckets=buckets)
    if strat == "range":
        if len(parts) < 3:
            raise typer.BadParameter("--partition range requires 'col:range:lo,hi;lo,hi'")
        boundaries: List[Tuple[Any, Any]] = []
        for chunk in parts[2].split(";"):
            lo, hi = chunk.split(",")
            boundaries.append((_coerce(lo), _coerce(hi)))
        return PartitionSpec(column=col, strategy="range", boundaries=boundaries)
    raise typer.BadParameter(f"unknown partition strategy: {strat}")


def _coerce(s: str):
    s = s.strip()
    try:    return int(s)
    except ValueError: pass
    try:    return float(s)
    except ValueError: pass
    return s


def _parse_report(items: List[str]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for it in items or []:
        if ":" not in it:
            raise typer.BadParameter(
                f"--report must be 'format:path', got {it!r}; "
                "valid formats: html, junit, json"
            )
        fmt, path = it.split(":", 1)
        fmt = fmt.strip().lower()
        if fmt not in ("html", "junit", "json"):
            raise typer.BadParameter(f"unknown report format: {fmt!r}")
        out.append((fmt, path))
    return out


# ------------------------------------------------------- Legacy source builder
def _require(opts: Dict[str, Any], prefix: str, *names: str) -> None:
    """Raise BadParameter if any required legacy companion flag is missing.

    This matches the explicit-error behavior of the pre-typer argparse CLI.
    """
    missing = [n for n in names if not opts.get(f"{prefix}_{n}")]
    if missing:
        flags = ", ".join(f"--{prefix}-{n}" for n in missing)
        raise typer.BadParameter(
            f"{flags} required when --{prefix}-type={opts.get(f'{prefix}_type')!r}"
        )


def _legacy_build_source(prefix: str, opts: Dict[str, Optional[str]]):
    t = opts[f"{prefix}_type"]
    if t == "csv":
        _require(opts, prefix, "path")
        return CsvFile(opts[f"{prefix}_path"])
    if t == "parquet":
        _require(opts, prefix, "path")
        return ParquetFile(opts[f"{prefix}_path"])
    if t == "sqltable":
        _require(opts, prefix, "conn", "table")
        return SqlTable(conn=opts[f"{prefix}_conn"], table=opts[f"{prefix}_table"])
    if t == "sqlquery":
        _require(opts, prefix, "conn", "query")
        return SqlQuery(conn=opts[f"{prefix}_conn"], query=opts[f"{prefix}_query"])
    if t == "postgres":
        _require(opts, prefix, "conn")
        if not (opts.get(f"{prefix}_table") or opts.get(f"{prefix}_query")):
            raise typer.BadParameter(
                f"--{prefix}-table or --{prefix}-query required for type=postgres"
            )
        from .sources.postgres_scanner import PostgresSource
        return PostgresSource(
            conn=opts[f"{prefix}_conn"],
            table=opts[f"{prefix}_table"],
            query=opts[f"{prefix}_query"],
        )
    raise typer.BadParameter(f"unknown {prefix} source type: {t!r}")


# ------------------------------------------------------------- compare command
@app.command(name="compare")
def compare_cmd(
    left: Optional[str] = typer.Option(
        None, "--left", help="Source URI (e.g. csv:./a.csv, postgres:postgresql://u:p@h/db#orders)"
    ),
    right: Optional[str] = typer.Option(None, "--right", help="See --left"),
    keys: List[str] = typer.Option(
        [], "--keys", "-k",
        help="Key column (repeatable). Comma-separated values also supported.",
    ),
    columns: Optional[str] = typer.Option(None, "--columns", help="Comma-separated columns to include"),
    exclude: Optional[str] = typer.Option(None, "--exclude", help="Comma-separated columns to exclude"),
    tolerance: List[str] = typer.Option(
        [], "--tolerance", help="Repeatable: col=abs_value (e.g. amount=0.01)"
    ),
    partition: Optional[str] = typer.Option(
        None, "--partition", help="col[:strategy[:args]] — e.g. region:value, id:hash:32"
    ),
    mode: CompareMode = typer.Option(
        CompareMode.KEYED, "--mode",
        case_sensitive=False,
        help="Compare mode.",
    ),
    sample_limit: int = typer.Option(10, "--sample-limit"),
    hash_only: bool = typer.Option(
        False, "--hash-only",
        help="Shortcut for --mode hash (whole-dataset checksum compare).",
    ),
    row_hash: bool = typer.Option(
        False, "--row-hash",
        help="In keyed mode, compare a per-row 64-bit hash instead of "
             "per-column equality. Faster on wide tables; sample only "
             "carries keys.",
    ),
    report: List[str] = typer.Option(
        [], "--report",
        help="format:path (repeatable). format ∈ {html, junit, json}",
    ),
    fail_on: FailOn = typer.Option(
        FailOn.MISMATCH, "--fail-on",
        case_sensitive=False,
        help="Exit non-zero policy: never | mismatch | error.",
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress text summary"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable structured logging"),

    # ----- Legacy 0.3.x flags (kept for backwards compatibility) -------------
    left_type: Optional[str] = typer.Option(None, "--left-type", hidden=True),
    left_path: Optional[str] = typer.Option(None, "--left-path", hidden=True),
    left_conn: Optional[str] = typer.Option(None, "--left-conn", hidden=True),
    left_table: Optional[str] = typer.Option(None, "--left-table", hidden=True),
    left_query: Optional[str] = typer.Option(None, "--left-query", hidden=True),
    right_type: Optional[str] = typer.Option(None, "--right-type", hidden=True),
    right_path: Optional[str] = typer.Option(None, "--right-path", hidden=True),
    right_conn: Optional[str] = typer.Option(None, "--right-conn", hidden=True),
    right_table: Optional[str] = typer.Option(None, "--right-table", hidden=True),
    right_query: Optional[str] = typer.Option(None, "--right-query", hidden=True),
    html_path: Optional[str] = typer.Option(None, "--html", hidden=True),
    junit_path: Optional[str] = typer.Option(None, "--junit", hidden=True),
    json_path: Optional[str] = typer.Option(None, "--json", hidden=True),
):
    """Run a reconciliation between two sources and emit reports.

    Either pass URI-style ``--left`` / ``--right`` (preferred) or the
    legacy ``--left-type/--left-path/...`` flag set from 0.3.x.
    """
    if verbose:
        # Attach a RichHandler to *our* logger only, so we don't clobber
        # pytest's caplog handler on root and so library users keep control
        # of root logging configuration.
        root = logging.getLogger("fastrecon")
        root.setLevel(logging.INFO)
        if not any(isinstance(h, RichHandler) for h in root.handlers):
            root.addHandler(
                RichHandler(rich_tracebacks=True, show_time=True, show_path=False)
            )

    # Resolve sources: URI form takes precedence; otherwise legacy flags.
    if left:
        left_src = _parse_source_uri(left)
    elif left_type:
        left_src = _legacy_build_source("left", locals())
    else:
        raise typer.BadParameter("--left is required (or legacy --left-type/--left-path/...)")

    if right:
        right_src = _parse_source_uri(right)
    elif right_type:
        right_src = _legacy_build_source("right", locals())
    else:
        raise typer.BadParameter("--right is required (or legacy --right-type/--right-path/...)")

    # `keys` accepts repeated --keys A --keys B and also --keys A,B
    flat_keys: List[str] = []
    for k in keys:
        flat_keys.extend(p.strip() for p in k.split(",") if p.strip())
    cols = [c.strip() for c in columns.split(",")] if columns else None
    excl = [c.strip() for c in exclude.split(",")] if exclude else None

    log.info("Loading sources: left=%s right=%s", type(left_src).__name__,
             type(right_src).__name__)
    res = compare(
        left=left_src, right=right_src,
        keys=flat_keys or None,
        compare_mode=(CompareMode.HASH.value if hash_only else mode.value),
        columns=cols, exclude_columns=excl,
        tolerances=_parse_tolerance(tolerance),
        partition=_parse_partition(partition),
        config=ReconConfig(sample_limit=sample_limit, row_hash=row_hash),
    )
    log.info("compare done in %.3fs status=%s changed=%d",
             res.execution_metrics.elapsed_sec, res.status, res.changed_rows)
    # Granular per-partition metrics (rows scanned, mismatch counts) so users
    # running with --verbose in CI can pinpoint which slice was slow / wrong.
    if verbose:
        em = res.execution_metrics
        bytes_scanned = getattr(em, "bytes_scanned_left", None)
        if bytes_scanned is not None:
            log.info(
                "rows scanned: left=%s right=%s · bytes scanned: left=%s right=%s",
                f"{res.row_count_left:,}", f"{res.row_count_right:,}",
                f"{getattr(em, 'bytes_scanned_left', 0):,}",
                f"{getattr(em, 'bytes_scanned_right', 0):,}",
            )
        else:
            log.info("rows scanned: left=%s right=%s",
                     f"{res.row_count_left:,}", f"{res.row_count_right:,}")
        parts = (res.column_stats or {}).get("partitions") or []
        for p in parts:
            log.info(
                "partition %r: left=%s right=%s missingL=%s missingR=%s changed=%s status=%s",
                p.get("partition"),
                f"{p.get('row_count_left', 0):,}",
                f"{p.get('row_count_right', 0):,}",
                f"{p.get('missing_in_left', 0):,}",
                f"{p.get('missing_in_right', 0):,}",
                f"{p.get('changed_rows', 0):,}",
                "OK" if p.get("match") else "FAIL",
            )

    if not quiet:
        typer.echo(res.summary())

    # Emit reports — both new --report and legacy single-flag forms.
    for fmt, path in _parse_report(report):
        _emit_report(res, fmt, path)
    if html_path:  _emit_report(res, "html", html_path)
    if junit_path: _emit_report(res, "junit", junit_path)
    if json_path:  _emit_report(res, "json", json_path)

    code = _exit_code_for(res, fail_on.value)
    raise typer.Exit(code=code)


def _emit_report(res, fmt: str, path: str) -> None:
    log.info("Writing %s report -> %s", fmt, path)
    if fmt == "html":
        from .output.html_report import render_html
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(res))
    elif fmt == "junit":
        from .output.junit_report import render_junit
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_junit(res))
    elif fmt == "json":
        with open(path, "w", encoding="utf-8") as f:
            f.write(res.to_json(indent=True))


def _exit_code_for(res, fail_on: str) -> int:
    if fail_on == "never":
        return 0
    if fail_on == "error":
        return 2 if res.status == "ERROR" else 0
    # mismatch (default): 0 match, 1 mismatch, 2 error
    if res.status == "MATCH":    return 0
    if res.status == "MISMATCH": return 1
    return 2


# ------------------------------------------------------------- entrypoints
def main(argv: Optional[List[str]] = None) -> int:
    """Programmatic entrypoint returning an int exit code (used in tests)."""
    # Default: compare subcommand if no subcommand given.
    args = list(argv) if argv is not None else sys.argv[1:]
    # Convenience: allow `fastrecon --left ...` (no subcommand) to mean
    # `fastrecon compare --left ...`. Anything else is passed through.
    if args and args[0] not in {"compare", "--help", "-h"} and args[0].startswith("-"):
        args = ["compare"] + args
    # standalone_mode=True lets typer/click translate `typer.Exit(code=N)`
    # into `SystemExit(N)`; we trap that here so callers (tests, library
    # users) get an int instead of an interpreter exit.
    try:
        app(args=args)
        return 0
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        try:
            return int(code)
        except (TypeError, ValueError):
            return 1


if __name__ == "__main__":
    sys.exit(main())
