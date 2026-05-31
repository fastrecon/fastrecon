"""ReconResult — the structured object returned by ``compare()``."""

from __future__ import annotations

import datetime as _dt
import decimal as _dec
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import orjson

from ..types import ExecutionMetrics, SchemaDiff, Status


@dataclass
class ReconResult:
    status: Status
    row_count_left: int = 0
    row_count_right: int = 0
    schema_match: bool = False
    data_match: bool = False
    missing_in_left: int = 0
    missing_in_right: int = 0
    changed_rows: int = 0
    duplicate_keys_left: int = 0
    duplicate_keys_right: int = 0
    schema_diff: Optional[SchemaDiff] = None
    column_stats: Dict[str, Any] = field(default_factory=dict)
    sample_mismatches: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    execution_metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    error: Optional[str] = None
    compare_mode: str = "keyed"
    keys: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ public
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return _scrub(d)

    def to_json(self, path: Optional[str] = None, indent: bool = False) -> str:
        """Render the result as JSON. If ``path`` is given, also writes it."""
        opts = orjson.OPT_INDENT_2 if indent else 0
        out = orjson.dumps(self.to_dict(), default=_json_default, option=opts).decode()
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(out)
        return out

    def to_html(
        self,
        path: Optional[str] = None,
        title: str = "fastrecon report",
        detail: str = "full",
    ) -> str:
        """Render a self-contained HTML report.

        Parameters
        ----------
        path : str | None
            If given, also writes the HTML to this path.
        title : str
            Page title.
        detail : {"summary", "diff", "full"}
            ``summary`` → headline metrics only.
            ``diff``    → metrics + schema diff (skip mismatch sample tables).
            ``full``    → everything (default).
        """
        from .html_report import render_html
        html = render_html(self, title=title, detail=detail)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        return html

    def to_csv(self, path: Optional[str] = None, layout: str = "diff") -> str:
        """Render the result as CSV.

        Parameters
        ----------
        path : str | None
            If given, also writes the CSV to this path.
        layout : {"diff", "summary"}
            ``diff``    → mismatch sample rows tagged by bucket (default).
            ``summary`` → 2-column key/value of headline metrics.
        """
        from .csv_report import render_csv
        out = render_csv(self, layout=layout)
        if path:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(out)
        return out

    def to_text(self, path: Optional[str] = None, detail: str = "diff") -> str:
        """Render a plain-text report (terminal/log/email-friendly).

        Parameters
        ----------
        path : str | None
            If given, also writes the text to this path.
        detail : {"summary", "diff", "full"}
            ``summary`` → metrics only.
            ``diff``    → metrics + schema diff + sample tables (default).
            ``full``    → everything including partition table.
        """
        from .text_report import render_text
        out = render_text(self, detail=detail)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(out)
        return out

    def to_junit(self, path: Optional[str] = None, suite_name: str = "fastrecon") -> str:
        """Render a JUnit XML report. If ``path`` is given, also writes it."""
        from .junit_report import render_junit
        xml = render_junit(self, suite_name=suite_name)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml)
        return xml

    @property
    def exit_code(self) -> int:
        """0 for MATCH, 1 for MISMATCH, 2 for ERROR — useful for CLI/CI hooks."""
        return {"MATCH": 0, "MISMATCH": 1, "ERROR": 2}.get(self.status, 1)

    def summary(self) -> str:
        lines = [
            f"status               : {self.status}",
            f"compare_mode         : {self.compare_mode}",
            f"row_count_left       : {self.row_count_left:,}",
            f"row_count_right      : {self.row_count_right:,}",
            f"schema_match         : {self.schema_match}",
            f"data_match           : {self.data_match}",
            f"missing_in_left      : {self.missing_in_left:,}",
            f"missing_in_right     : {self.missing_in_right:,}",
            f"changed_rows         : {self.changed_rows:,}",
            f"duplicate_keys_left  : {self.duplicate_keys_left:,}",
            f"duplicate_keys_right : {self.duplicate_keys_right:,}",
            f"elapsed_sec          : {self.execution_metrics.elapsed_sec:.3f}",
            f"engine               : {self.execution_metrics.engine}",
        ]
        if self.error:
            lines.append(f"error                : {self.error}")

        # Schema-diff details. Show only the things that actually differ —
        # silent on a clean match so the summary stays compact.
        sd = self.schema_diff
        if sd is not None and (
            sd.missing_in_left or sd.missing_in_right or sd.logical_type_mismatches
        ):
            lines.append("")
            lines.append("schema_diff:")
            if sd.missing_in_left:
                lines.append(f"  missing_in_left      : {', '.join(sd.missing_in_left)}")
            if sd.missing_in_right:
                lines.append(f"  missing_in_right     : {', '.join(sd.missing_in_right)}")
            if sd.logical_type_mismatches:
                lines.append("  logical_type_mismatches:")
                for col, sides in sd.logical_type_mismatches.items():
                    lines.append(f"    {col}: left={sides['left']} right={sides['right']}")
        return "\n".join(lines)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<ReconResult status={self.status} mode={self.compare_mode} changed={self.changed_rows}>"


# ----------------------------------------------------------------------- utils
def _json_default(o: Any) -> Any:
    if isinstance(o, _dec.Decimal):
        return float(o)
    if isinstance(o, (_dt.date, _dt.datetime, _dt.time)):
        return o.isoformat()
    if isinstance(o, bytes):
        return o.decode("utf-8", errors="replace")
    raise TypeError(f"not JSON-serializable: {type(o)}")


def _scrub(obj: Any) -> Any:
    """Walk a nested structure and convert non-JSON-friendly leaves."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    if isinstance(obj, _dec.Decimal):
        return float(obj)
    if isinstance(obj, (_dt.date, _dt.datetime, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj
