"""XML file source.

XML is hierarchical; reconciliation needs flat rows. The user picks the
"record" node via an XPath (``record_path``) and a mapping of output
column → relative XPath (``columns``). Each matched record becomes one
row; missing nodes/attributes become NULL.

Streaming parse via ``lxml.iterparse`` so multi-GB files don't blow
memory. The resulting Arrow table is registered as a DuckDB view, same
pattern as :class:`JsonFile`.

Install with the optional extra::

    pip install 'fastrecon[xml]'
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import duckdb

from ..exceptions import SourceError
from .base import Source


@dataclass
class XmlFile(Source):
    path: str
    record_path: str = "."
    """XPath that selects each record node, e.g. ``./orders/order`` or
    ``//order``. Default ``.`` treats the root as a single record."""
    columns: Dict[str, str] = field(default_factory=dict)
    """Map of output column name → XPath relative to the record node.
    Use ``./@id`` for an attribute, ``./amount`` for a child element's
    text, ``./addr/zip`` for a nested child. If empty, every direct
    child element of the record becomes a column (named after its tag),
    and every attribute becomes a column prefixed with ``@``."""
    namespaces: Optional[Dict[str, str]] = None
    """Prefix → URI map for XPaths that use namespaces."""
    encoding: Optional[str] = None
    """Override the XML's declared encoding."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        try:
            from lxml import etree  # type: ignore
        except ImportError as e:
            raise SourceError(
                "XmlFile requires the 'lxml' package. Install with: "
                "pip install 'fastrecon[xml]'"
            ) from e
        try:
            import pyarrow as pa
        except ImportError as e:  # pragma: no cover - pyarrow is a hard dep
            raise SourceError("pyarrow is required to register XmlFile") from e

        try:
            rows = self._extract_rows(etree)
        except Exception as e:
            raise SourceError(
                f"Failed to parse XML {self.path!r} (record_path={self.record_path!r}): {e}"
            ) from e

        # Determine column order: explicit mapping first, otherwise the
        # union of keys observed across rows (preserve first-seen order).
        if self.columns:
            col_order: List[str] = list(self.columns.keys())
        else:
            seen: Dict[str, None] = {}
            for r in rows:
                for k in r.keys():
                    if k not in seen:
                        seen[k] = None
            col_order = list(seen.keys())

        # Build column-major dict for Arrow; everything as string. Users
        # can cast downstream — fastrecon's compare engine already
        # tolerates dtype skew across sides (see 0.5.3).
        col_data: Dict[str, List[Optional[str]]] = {c: [] for c in col_order}
        for r in rows:
            for c in col_order:
                v = r.get(c)
                col_data[c].append(None if v is None else str(v))

        if not col_order:
            # Empty result: register an empty single-column view so
            # downstream queries don't choke on a zero-column table.
            table = pa.table({"_empty": pa.array([], type=pa.string())})
        else:
            table = pa.table(col_data)

        try:
            con.register(f"_arrow_{view_name}", table)
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f'SELECT * FROM "_arrow_{view_name}"'
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(f"Failed to register XML {self.path!r}: {e}") from e
        return f'SELECT * FROM "{view_name}"'

    def _extract_rows(self, etree) -> List[Dict[str, Optional[str]]]:
        # Streaming parse — only hold one record subtree in memory at a
        # time, then discard it.
        parser_kwargs = {"events": ("end",)}
        if self.encoding:
            parser_kwargs["encoding"] = self.encoding  # type: ignore[assignment]

        # Resolve the record tag from the XPath so iterparse can filter
        # cheaply at the C level. iterparse's ``tag=`` only accepts a
        # single QName, so for arbitrary XPaths we fall back to an
        # in-memory parse (still fine for typical config/source files).
        record_tag = _xpath_leaf_tag(self.record_path, self.namespaces or {})
        rows: List[Dict[str, Optional[str]]] = []

        if record_tag and "/" not in self.record_path.lstrip("./"):
            # Streaming fast-path.
            context = etree.iterparse(self.path, tag=record_tag, **parser_kwargs)
            for _, elem in context:
                rows.append(self._record_to_dict(elem))
                elem.clear()
                # Free preceding siblings to bound memory.
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
        else:
            # General path: parse fully, then XPath-select.
            tree = etree.parse(self.path)
            for elem in tree.xpath(self.record_path, namespaces=self.namespaces or None):
                rows.append(self._record_to_dict(elem))
        return rows

    def _record_to_dict(self, elem) -> Dict[str, Optional[str]]:
        if self.columns:
            out: Dict[str, Optional[str]] = {}
            for col, xp in self.columns.items():
                vals = elem.xpath(xp, namespaces=self.namespaces or None)
                out[col] = _first_text(vals)
            return out
        # Auto mode: attributes (prefixed @) + direct child text.
        out = {f"@{k}": v for k, v in elem.attrib.items()}
        for child in elem:
            tag = _localname(child.tag)
            # Last-write-wins for repeated tags; users with repeated
            # children should pass an explicit ``columns`` map.
            text = (child.text or "").strip()
            out[tag] = text if text else None
        return out


def _localname(tag: str) -> str:
    # Strip ``{namespace}`` prefix lxml adds.
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _first_text(vals) -> Optional[str]:
    if not vals:
        return None
    v = vals[0]
    if isinstance(v, str):
        return v
    # Element node — take its text.
    text = getattr(v, "text", None)
    if text is None:
        return None
    s = text.strip()
    return s if s else None


def _xpath_leaf_tag(xpath: str, namespaces: Dict[str, str]) -> Optional[str]:
    """Return a QName usable with ``iterparse(tag=...)`` if the XPath is
    a simple steps-only expression like ``./a/b/c``; otherwise None."""
    cleaned = xpath.strip().lstrip("./")
    if not cleaned or cleaned in (".", "/"):
        return None
    if any(ch in cleaned for ch in ("[", "(", "@", "*")):
        return None
    leaf = cleaned.rsplit("/", 1)[-1]
    if ":" in leaf:
        prefix, local = leaf.split(":", 1)
        ns = namespaces.get(prefix)
        if not ns:
            return None
        return f"{{{ns}}}{local}"
    return leaf
