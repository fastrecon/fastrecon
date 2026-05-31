"""``source(path, **kwargs)`` — pick a Source subclass from the file
extension.

Convenience helper so users can write::

    from fastrecon import source, compare
    compare(source("a.parquet"), source("b.csv"), keys=["id"])

without hand-importing each source class. Unknown extensions raise
``SourceError`` with the list of recognized formats.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..exceptions import SourceError
from .avro_file import AvroFile
from .base import Source
from .csv_file import CsvFile
from .excel_file import ExcelFile
from .fixed_width_file import FixedWidthFile
from .json_file import JsonFile
from .mainframe_file import MainframeFile
from .orc_file import OrcFile
from .parquet_file import ParquetFile
from .xml_file import XmlFile


# Extension → (factory, default-options-overrides). For CsvFile the
# delimiter goes inside ``options`` (forwarded to DuckDB read_csv_auto)
# rather than as a top-level kwarg — CsvFile has no ``delimiter`` arg.
_REGISTRY = {
    ".csv":     (CsvFile,     {}),
    ".tsv":     (CsvFile,     {"options": {"delim": "\t"}}),
    ".psv":     (CsvFile,     {"options": {"delim": "|"}}),
    ".txt":     (CsvFile,     {}),  # caller can pass options / use fixed_width=True
    ".dat":     (CsvFile,     {}),
    ".parquet": (ParquetFile, {}),
    ".pq":      (ParquetFile, {}),
    ".json":    (JsonFile,    {}),
    ".ndjson":  (JsonFile,    {"options": {"format": "newline_delimited"}}),
    ".jsonl":   (JsonFile,    {"options": {"format": "newline_delimited"}}),
    ".xml":     (XmlFile,     {}),
    ".xlsx":    (ExcelFile,   {}),
    ".xls":     (ExcelFile,   {}),
    ".avro":    (AvroFile,    {}),
    ".orc":     (OrcFile,     {}),
}


def source(path: str, *, fixed_width: bool = False, mainframe: bool = False,
           **kwargs: Any) -> Source:
    """Build a :class:`Source` for ``path`` based on its extension.

    Parameters
    ----------
    path:
        Local path, glob, or remote URL (``s3://``, ``https://``).
    fixed_width:
        Force a :class:`FixedWidthFile` regardless of extension. Pass
        ``columns=[(name, start, length), ...]`` alongside.
    mainframe:
        Force a :class:`MainframeFile`. Pass ``fields=[...]`` and
        ``record_length=`` alongside.
    **kwargs:
        Forwarded to the chosen source class.
    """
    if mainframe:
        return MainframeFile(path=path, **kwargs)
    if fixed_width:
        return FixedWidthFile(path=path, **kwargs)

    # Strip URL query/fragment before looking at the extension.
    if "://" in path:
        parsed = urlparse(path)
        leaf = parsed.path
    else:
        leaf = path
    ext = "." + leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""
    if ext not in _REGISTRY:
        raise SourceError(
            f"Unrecognized extension {ext!r} for {path!r}. "
            f"Known: {sorted(_REGISTRY.keys())}. For fixed-width text or "
            f"mainframe binary files, pass fixed_width=True or mainframe=True."
        )
    cls, defaults = _REGISTRY[ext]
    # Deep-merge ``options`` so caller-provided options extend (not
    # replace) the per-extension defaults like {"delim": "\t"}.
    merged = dict(defaults)
    if "options" in defaults and "options" in kwargs:
        merged["options"] = {**defaults["options"], **kwargs.pop("options")}
    merged.update(kwargs)
    return cls(path=path, **merged)
