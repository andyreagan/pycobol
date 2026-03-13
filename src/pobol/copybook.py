"""Parse COBOL copybook / DATA DIVISION record layouts into Python-friendly
field descriptors so we can serialize dicts → fixed-width records and back.

Supports the most common PIC clauses:
  - PIC X(n)        — alphanumeric, left-justified, space-padded
  - PIC 9(n)        — unsigned integer, right-justified, zero-padded
  - PIC S9(n)       — signed integer (trailing sign overpunch by default)
  - PIC 9(n)V9(m)   — implied decimal
  - PIC S9(n)V9(m)  — signed implied decimal

Group (level < 49, no PIC) items are recognized but not directly serialized;
their children carry the actual data. COMP/COMP-3 packed fields are NOT
supported yet — those are uncommon in flat-file I/O on GnuCOBOL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pobol.exceptions import CopybookParseError

# ---------------------------------------------------------------------------
# PIC parsing helpers
# ---------------------------------------------------------------------------


def _parse_9_part(s: str) -> int:
    """Parse a 9-digit specifier: '9(5)' → 5, '999' → 3, '9' → 1."""
    m = re.match(r"9\((\d+)\)", s)
    if m:
        return int(m.group(1))
    m = re.match(r"(9+)", s)
    if m:
        return len(m.group(1))
    return 0


def _expand_pic(pic: str) -> tuple[str, int, int]:
    """Return (kind, total_display_length, decimal_places).

    kind is one of 'alpha', 'unsigned', 'signed'.

    Supports all common forms:
    - X(n), XX, X            → alpha
    - 9(n), 999, 9           → unsigned
    - S9(n), S999            → signed
    - 9(n)V9(m), 9(n)V99, 999V9(2), 999V99  → unsigned decimal
    - S9(n)V9(m), etc.       → signed decimal
    """
    pic = pic.upper().replace(" ", "")

    # Alpha: X(n) or XXX
    m = re.fullmatch(r"X\((\d+)\)", pic)
    if m:
        return ("alpha", int(m.group(1)), 0)
    m = re.fullmatch(r"(X+)", pic)
    if m:
        return ("alpha", len(m.group(1)), 0)

    # Numeric: optional S, then 9-part, optional V + 9-part
    m = re.fullmatch(r"(S)?(9(?:\(\d+\)|9*))(V(9(?:\(\d+\)|9*)))?", pic)
    if not m:
        raise CopybookParseError(f"Cannot parse PIC clause: {pic!r}")

    signed = bool(m.group(1))
    int_len = _parse_9_part(m.group(2))
    if m.group(4):
        dec_len = _parse_9_part(m.group(4))
    else:
        dec_len = 0

    display_len = int_len + dec_len
    kind = "signed" if signed else "unsigned"
    return (kind, display_len, dec_len)


# ---------------------------------------------------------------------------
# Field / Copybook model
# ---------------------------------------------------------------------------


@dataclass
class Field:
    """One elementary data item in a record layout."""

    level: int
    name: str
    pic: str  # raw PIC string
    kind: str  # alpha | unsigned | signed
    offset: int  # byte offset within the record
    length: int  # display length in bytes
    decimals: int  # implied decimal places

    def encode(self, value: Any) -> bytes:
        """Encode a Python value into fixed-width COBOL display bytes."""
        if self.kind == "alpha":
            s = str(value) if value is not None else ""
            return s.ljust(self.length)[: self.length].encode("ascii")

        # numeric
        if value is None:
            value = 0
        num = round(float(value) * (10**self.decimals))
        negative = num < 0
        num = abs(int(num))
        digits = str(num).zfill(self.length)[-self.length :]

        if self.kind == "signed":
            # GnuCOBOL default: trailing sign overpunch.
            # Positive: last digit stays 0-9 → {ABCDEFGHI (0-9)
            # Negative: last digit → }JKLMNOPQR (0-9)
            # For simplicity we use ASCII sign convention that GnuCOBOL
            # understands when reading DISPLAY fields: just prefix with
            # '-' / '+' — but that changes length.  Instead, use the
            # standard overpunch encoding that cobc expects:
            pos_over = "{ABCDEFGHI"
            neg_over = "}JKLMNOPQR"
            last = int(digits[-1])
            if negative:
                digits = digits[:-1] + neg_over[last]
            else:
                digits = digits[:-1] + pos_over[last]

        return digits.encode("ascii")

    def decode(self, raw: bytes) -> Any:
        """Decode fixed-width COBOL display bytes into a Python value."""
        text = raw.decode("ascii", errors="replace")

        if self.kind == "alpha":
            return text.rstrip()

        # Numeric — handle possible overpunch on last char
        pos_over = "{ABCDEFGHI"
        neg_over = "}JKLMNOPQR"

        negative = False
        last = text[-1]
        if last in pos_over:
            digit = pos_over.index(last)
            text = text[:-1] + str(digit)
        elif last in neg_over:
            digit = neg_over.index(last)
            text = text[:-1] + str(digit)
            negative = True
        elif last == "-":
            text = text[:-1]
            negative = True
        elif last == "+":
            text = text[:-1]

        # strip non-digit chars that may be left
        cleaned = re.sub(r"[^0-9]", "0", text)
        num = int(cleaned)
        if negative:
            num = -num
        if self.decimals:
            return num / (10**self.decimals)
        return num


@dataclass
class Copybook:
    """A parsed record layout — ordered list of elementary fields."""

    name: str
    fields: list[Field] = field(default_factory=list)
    record_length: int = 0

    def encode(self, record: dict[str, Any]) -> bytes:
        """Encode a dict → one fixed-width record (bytes)."""
        buf = bytearray(b" " * self.record_length)
        for f in self.fields:
            val = record.get(f.name, record.get(f.name.replace("-", "_")))
            encoded = f.encode(val)
            buf[f.offset : f.offset + f.length] = encoded
        return bytes(buf)

    def decode(self, raw: bytes) -> dict[str, Any]:
        """Decode one fixed-width record → dict.

        LINE SEQUENTIAL files strip trailing spaces, so we right-pad
        short records back to ``record_length`` before slicing fields.
        """
        if len(raw) < self.record_length:
            raw = raw + b" " * (self.record_length - len(raw))
        out: dict[str, Any] = {}
        for f in self.fields:
            chunk = raw[f.offset : f.offset + f.length]
            key = f.name.lower().replace("-", "_")
            out[key] = f.decode(chunk)
        return out

    def encode_many(self, records: list[dict[str, Any]], newline: bool = True) -> bytes:
        """Encode a list of dicts into file contents."""
        parts = [self.encode(r) for r in records]
        sep = b"\n" if newline else b""
        result = sep.join(parts)
        if newline and parts:
            result += b"\n"
        return result

    def decode_many(self, data: bytes) -> list[dict[str, Any]]:
        """Decode file contents into list of dicts."""
        if not data:
            return []
        # split by newlines if present; otherwise by record_length
        if b"\n" in data:
            lines = data.split(b"\n")
            # drop trailing empty line
            if lines and lines[-1] == b"":
                lines = lines[:-1]
        else:
            lines = [
                data[i : i + self.record_length]
                for i in range(0, len(data), self.record_length)
            ]
        return [self.decode(line) for line in lines if line]


# ---------------------------------------------------------------------------
# Parser: COBOL source → Copybook
# ---------------------------------------------------------------------------

# Matches lines like:  05  CUSTOMER-NAME     PIC X(30).
_FIELD_RE = re.compile(
    r"""
    ^\s*
    (?P<level>\d{1,2}) \s+
    (?P<name>[A-Za-z0-9-]+) \s+
    PIC(?:TURE)? \s+
    (?P<pic>[SsXx9Vv()0-9]+)
    \s*\.
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Group item (no PIC)
_GROUP_RE = re.compile(
    r"^\s*(?P<level>\d{1,2})\s+(?P<name>[A-Za-z0-9-]+)\s*\.", re.IGNORECASE
)


def parse_copybook(source: str, name: str = "RECORD") -> Copybook:
    """Parse COBOL DATA DIVISION field definitions into a Copybook.

    *source* can be the full COBOL program or just the FD/record lines.
    We extract every elementary PIC item and compute offsets.
    """
    fields: list[Field] = []
    offset = 0

    for line in source.splitlines():
        # skip comments (col 7 = *)
        stripped = line
        if len(line) > 6 and line[6] == "*":
            continue

        m = _FIELD_RE.search(stripped)
        if not m:
            continue

        level = int(m.group("level"))
        fname = m.group("name").upper()
        pic = m.group("pic")

        kind, length, decimals = _expand_pic(pic)
        fields.append(
            Field(
                level=level,
                name=fname,
                pic=pic,
                kind=kind,
                offset=offset,
                length=length,
                decimals=decimals,
            )
        )
        offset += length

    return Copybook(name=name, fields=fields, record_length=offset)
