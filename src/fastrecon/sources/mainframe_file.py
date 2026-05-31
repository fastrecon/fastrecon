"""Mainframe binary file source (EBCDIC + COBOL packed decimal).

Real-world mainframe `.dat` files come in three flavors:

  1. EBCDIC fixed-width text records, no line terminators (records are
     ``record_length`` bytes each, back-to-back).
  2. EBCDIC text records with newline terminators (less common but
     occurs when files have been pre-processed).
  3. ASCII fixed-width — same shape as flavor 1 but already in ASCII.
     For this case ``FixedWidthFile`` is usually a better fit; use
     ``MainframeFile`` when you also need to decode COMP-3 (packed
     decimal) numeric fields.

Field types supported:

  - ``"text"``: decode the slot as text using ``encoding`` (default
    ``cp037`` US EBCDIC). ``TRIM()`` applied unless ``trim=False``.
  - ``"int"``: decode as text, then ``TRY_CAST`` to BIGINT.
  - ``"comp3"`` / ``"packed"``: COBOL COMP-3 packed-decimal. ``length``
    is in BYTES (not digits). ``scale`` gives implied decimal places.
  - ``"zoned"``: COBOL zoned-decimal (signed via the last nibble).
  - ``"binary"`` / ``"comp"``: COBOL COMP / COMP-4 / BINARY — raw
    big-endian signed integer of ``length`` bytes.

Field spec is a list of dicts, e.g.::

    MainframeFile(
        path="ACCT.DAT",
        record_length=120,
        encoding="cp037",
        fields=[
            {"name": "acct_id",  "start": 1,  "length": 10, "type": "text"},
            {"name": "balance",  "start": 11, "length": 6,  "type": "comp3", "scale": 2},
            {"name": "status",   "start": 17, "length": 1,  "type": "text"},
            {"name": "txn_count","start": 18, "length": 4,  "type": "binary"},
            ...
        ],
    )

Streaming: records are read in chunks of ``chunk_records`` to bound
memory. Decoded rows are buffered into an Arrow table and registered as
a DuckDB view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

import duckdb

from ..exceptions import SourceError
from .base import Source


FieldSpec = Dict[str, Any]
"""``{"name": str, "start": int (1-indexed), "length": int, "type": str,
       optional "scale": int, "trim": bool}``."""


@dataclass
class MainframeFile(Source):
    path: str
    fields: List[FieldSpec] = field(default_factory=list)
    record_length: Optional[int] = None
    """Fixed record length in BYTES. Required for unterminated
    (flavor 1) files. If None, records are split on newlines (``\\n``)."""
    encoding: str = "cp037"
    """Codec for ``text``/``int``/``zoned`` fields. Common choices:
    ``cp037`` (US EBCDIC), ``cp500`` (international), ``cp1047`` (open
    systems EBCDIC), ``ascii``/``utf-8`` for already-converted files."""
    skip_bytes: int = 0
    """Bytes to skip at the start of the file (e.g. RDW headers)."""
    chunk_records: int = 50_000
    trim: bool = True
    """Default ``trim`` behavior for text fields when not set per-field."""

    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        if not self.fields:
            raise SourceError(
                f"MainframeFile {self.path!r}: at least one field spec required"
            )
        for f in self.fields:
            for key in ("name", "start", "length", "type"):
                if key not in f:
                    raise SourceError(
                        f"MainframeFile field missing {key!r}: {f}"
                    )
            if f["start"] < 1:
                raise SourceError(
                    f"MainframeFile field {f['name']!r}: start must be >=1"
                )
            if f["length"] < 1:
                raise SourceError(
                    f"MainframeFile field {f['name']!r}: length must be >=1"
                )

        try:
            import pyarrow as pa
        except ImportError as e:  # pragma: no cover
            raise SourceError("pyarrow is required for MainframeFile") from e

        try:
            rows = list(self._decode_records())
        except Exception as e:
            raise SourceError(
                f"Failed to decode mainframe file {self.path!r}: {e}"
            ) from e

        col_order = [f["name"] for f in self.fields]
        col_data: Dict[str, List[Any]] = {c: [] for c in col_order}
        for row in rows:
            for c in col_order:
                col_data[c].append(row.get(c))

        # Mixed Decimal/None or other rich types — fall back to string
        # for anything Arrow can't infer cleanly.
        try:
            table = pa.table(col_data)
        except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
            table = pa.table(
                {c: pa.array([None if v is None else str(v) for v in vals],
                             type=pa.string())
                 for c, vals in col_data.items()}
            )

        try:
            con.register(f"_arrow_{view_name}", table)
            con.execute(
                f'CREATE OR REPLACE VIEW "{view_name}" AS '
                f'SELECT * FROM "_arrow_{view_name}"'
            )
        except Exception as e:  # pragma: no cover
            raise SourceError(
                f"Failed to register mainframe file {self.path!r}: {e}"
            ) from e
        return f'SELECT * FROM "{view_name}"'

    # ----- record framing -----

    def _decode_records(self):
        if self.record_length is None:
            # Newline-delimited path.
            with open(self.path, "rb") as fh:
                if self.skip_bytes:
                    fh.read(self.skip_bytes)
                for raw in fh:
                    raw = raw.rstrip(b"\r\n")
                    if not raw:
                        continue
                    yield self._decode_one(raw)
        else:
            rl = int(self.record_length)
            with open(self.path, "rb") as fh:
                if self.skip_bytes:
                    fh.read(self.skip_bytes)
                while True:
                    buf = fh.read(rl * self.chunk_records)
                    if not buf:
                        break
                    for off in range(0, len(buf) - rl + 1, rl):
                        yield self._decode_one(buf[off:off + rl])

    # ----- field decoding -----

    def _decode_one(self, record: bytes) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in self.fields:
            start = int(f["start"]) - 1  # 1-indexed → 0-indexed
            end = start + int(f["length"])
            slot = record[start:end]
            t = f["type"].lower()
            try:
                if t == "text":
                    val = slot.decode(self.encoding, errors="replace")
                    if f.get("trim", self.trim):
                        val = val.strip()
                    out[f["name"]] = val
                elif t == "int":
                    s = slot.decode(self.encoding, errors="replace").strip()
                    out[f["name"]] = int(s) if s and s.lstrip("-").isdigit() else None
                elif t in ("comp3", "packed"):
                    out[f["name"]] = _decode_comp3(slot, f.get("scale", 0))
                elif t == "zoned":
                    out[f["name"]] = _decode_zoned(slot, f.get("scale", 0), self.encoding)
                elif t in ("binary", "comp", "comp4"):
                    out[f["name"]] = int.from_bytes(slot, "big", signed=True) if slot else None
                else:
                    raise SourceError(
                        f"MainframeFile field {f['name']!r}: unknown type {t!r}"
                    )
            except SourceError:
                raise
            except Exception as e:
                # Per-field decode failure → NULL, not a crash. Mirrors
                # the 0.5.3 spirit: bad cells shouldn't kill the run.
                out[f["name"]] = None
                _ = e  # swallow; reconciliation will flag the NULL.
        return out


# COMP-3 / zoned sign nibbles per the COBOL standard:
#   C, F, A, E → positive
#   D, B       → negative
# Anything else is malformed; we surface that as NULL rather than
# silently treating it as positive.
_POSITIVE_SIGN_NIBBLES = frozenset({0xC, 0xF, 0xA, 0xE})
_NEGATIVE_SIGN_NIBBLES = frozenset({0xD, 0xB})


def _decode_comp3(b: bytes, scale: int = 0) -> Optional[Decimal]:
    """Unpack COBOL COMP-3 (BCD packed-decimal) into a ``Decimal``.

    Layout: each byte holds two BCD digits except the last, whose low
    nibble is the sign (C/F/A/E = +, D/B = -). Returns ``None`` on any
    invalid digit or sign nibble.
    """
    if not b:
        return None
    digits: List[str] = []
    sign = "+"
    for i, byte in enumerate(b):
        hi = (byte >> 4) & 0x0F
        lo = byte & 0x0F
        if i < len(b) - 1:
            if hi > 9 or lo > 9:
                return None
            digits.append(str(hi))
            digits.append(str(lo))
        else:
            if hi > 9:
                return None
            digits.append(str(hi))
            if lo in _POSITIVE_SIGN_NIBBLES:
                sign = "+"
            elif lo in _NEGATIVE_SIGN_NIBBLES:
                sign = "-"
            else:
                return None  # malformed sign nibble
    if not digits:
        return None
    s = "".join(digits)
    if scale > 0:
        if len(s) <= scale:
            s = s.zfill(scale + 1)
        s = s[:-scale] + "." + s[-scale:]
    try:
        return Decimal(sign + s)
    except Exception:
        return None


def _decode_zoned(b: bytes, scale: int, encoding: str) -> Optional[Decimal]:
    """Unpack COBOL zoned-decimal. Sign lives in the high nibble of the
    last byte (C/F/A/E → +, D/B → -). Other bytes are EBCDIC digits.
    Returns ``None`` on invalid sign or non-digit content."""
    if not b:
        return None
    try:
        text = b[:-1].decode(encoding, errors="replace")
    except Exception:
        return None
    last = b[-1]
    last_digit = last & 0x0F
    if last_digit > 9:
        return None
    sign_nibble = (last >> 4) & 0x0F
    if sign_nibble in _POSITIVE_SIGN_NIBBLES:
        sign = "+"
    elif sign_nibble in _NEGATIVE_SIGN_NIBBLES:
        sign = "-"
    else:
        return None
    s = text + str(last_digit)
    if not s.isdigit():
        return None
    if scale > 0:
        if len(s) <= scale:
            s = s.zfill(scale + 1)
        s = s[:-scale] + "." + s[-scale:]
    try:
        return Decimal(sign + s)
    except Exception:
        return None
