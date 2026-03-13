"""Parse a COBOL source file to automatically extract:

1. SELECT clauses → file names and DD-name mappings
2. FD sections → record layouts (multiple 01-levels per FD)
3. Strip mainframe artifacts (sequence numbers, LABEL RECORDS, etc.)
4. Rewrite ASSIGN TO clauses for GnuCOBOL env-var file mapping

This is the "magic" that makes pobol feel like maturin: you point it at
a mainframe .cbl and it Just Works™.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pobol.copybook import Copybook, parse_copybook
from pobol.exceptions import CopybookParseError


# ---------------------------------------------------------------------------
# Data model for parsed source
# ---------------------------------------------------------------------------


@dataclass
class FileSpec:
    """One SELECT/FD pair extracted from a COBOL source."""

    select_name: str  # e.g. "VAR-DATA-FILE"
    assign_name: str  # e.g. "DATAIN" (original DD name)
    direction: str = "unknown"  # "input" | "output" | "input-output" | "unknown"
    record_layouts: dict[str, Copybook] = field(default_factory=dict)
    # Maps 01-level name → Copybook.  One FD can have multiple 01 records
    # (e.g. header record, detail record, trailer record).


@dataclass
class ParsedSource:
    """Result of parsing a full COBOL source file."""

    files: dict[str, FileSpec] = field(default_factory=dict)
    # Keyed by SELECT name
    cleaned_source: str = ""
    # Source with mainframe artifacts stripped, ready for cobc


# ---------------------------------------------------------------------------
# Mainframe source cleaning
# ---------------------------------------------------------------------------


def _is_mainframe_source(source: str) -> bool:
    """Heuristically detect if the source uses mainframe fixed format.

    Mainframe format has:
    - Columns 1-6: sequence numbers (digits with possible spaces)
    - Column 7: indicator area
    - Columns 8-72: source code
    - Columns 73-80: identification/change markers

    We require columns 1-6 to contain at least one digit (not just
    spaces, which would be normal fixed-format COBOL).  We also check
    for content beyond column 72 as a secondary signal.
    """
    lines = source.splitlines()
    sample = [line for line in lines if line.strip()][:50]
    if not sample:
        return False

    # Lines where cols 1-6 contain at least one digit (sequence numbers)
    seq_count = sum(
        1
        for line in sample
        if len(line) >= 7
        and re.match(r"^[\d ]{6}", line)
        and re.search(r"\d", line[:6])
    )

    # Lines that extend beyond column 72 (identification area)
    wide_count = sum(1 for line in sample if len(line) > 72)

    # Need a significant fraction of lines with sequence numbers
    return seq_count / len(sample) > 0.3 and wide_count / len(sample) > 0.2


def strip_mainframe_format(source: str) -> str:
    """Strip mainframe fixed-format artifacts from COBOL source.

    - Remove sequence numbers (columns 1-6)
    - Remove identification field (columns 73-80)
    - Preserve the indicator area (column 7)
    - Remove LABEL RECORDS / RECORDING MODE / BLOCK CONTAINS clauses
    - Convert IBM-370 → X86-64 in SOURCE/OBJECT-COMPUTER
    """
    lines = source.splitlines()
    cleaned: list[str] = []

    is_mf = _is_mainframe_source(source)

    for line in lines:
        if not line:
            cleaned.append("")
            continue

        if is_mf:
            # Strip columns 1-6 and 73-80
            if len(line) >= 7:
                content = line[6:72] if len(line) >= 72 else line[6:]
                cleaned.append(content)
            else:
                cleaned.append(line)
        else:
            cleaned.append(line)

    result = "\n".join(cleaned)

    # Remove obsolete FD clauses that GnuCOBOL doesn't need
    result = re.sub(
        r"^\s+LABEL\s+RECORDS?\s+(?:ARE\s+)?STANDARD\.?\s*$",
        "",
        result,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    result = re.sub(
        r"^\s+RECORDING\s+MODE\s+IS\s+\w+\.?\s*$",
        "",
        result,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    result = re.sub(
        r"^\s+BLOCK\s+CONTAINS\s+\d+\s+RECORDS?\.?\s*$",
        "",
        result,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    result = re.sub(
        r"^\s+RECORD\s+CONTAINS\s+\d+\s+CHARACTERS?\.?\s*$",
        "",
        result,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # Fix SOURCE-COMPUTER / OBJECT-COMPUTER
    result = re.sub(
        r"SOURCE-COMPUTER\.\s*IBM-370\.",
        "SOURCE-COMPUTER. X86-64.",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"OBJECT-COMPUTER\.\s*IBM-370\.",
        "OBJECT-COMPUTER. X86-64.",
        result,
        flags=re.IGNORECASE,
    )

    return result


def _needs_assign_rewrite(source: str, select_name: str, target: str) -> bool:
    """Determine if a SELECT...ASSIGN TO clause needs rewriting.

    We skip rewriting when:
    - Target starts with "WS-" (working-storage variable already wired up)
    - There's already an ACCEPT ... FROM ENVIRONMENT for this file

    Both bare DD-names (``DATAIN``) and quoted literals (``"datain.dat"``)
    are rewritten to env-var mapping so pobol can control file paths at
    runtime.  This means pobol works identically whether you point it at
    the mainframe original or a hand-ported GnuCOBOL version.
    """
    # Working-storage variable — already adapted
    if target.upper().startswith("WS-"):
        return False

    # Check if there's already an ACCEPT from environment for this file
    env_name = f"DD_{select_name.replace('-', '_')}"
    if re.search(
        rf'ACCEPT\s+\S+\s+FROM\s+ENVIRONMENT\s+"{re.escape(env_name)}"',
        source,
        re.IGNORECASE,
    ):
        return False

    return True


def _rewrite_assigns_for_env(source: str) -> tuple[str, dict[str, str]]:
    """Rewrite SELECT...ASSIGN TO clauses to use GnuCOBOL env-var resolution.

    Rewrites both bare DD-names (``DATAIN``) and quoted literals
    (``"datain.dat"``) so pobol can control file paths at runtime.
    Skips working-storage variables (``WS-*``) and files that already
    have ``ACCEPT … FROM ENVIRONMENT`` statements.

    Returns (rewritten_source, mapping of select_name → env_var_name).
    """
    select_re = re.compile(
        r"(SELECT\s+(?P<sel>[A-Za-z0-9-]+)\s+ASSIGN\s+TO\s+)"
        r'(?:EXTERNAL\s+)?(?P<target>[A-Za-z0-9-]+|"[^"]+")',
        re.IGNORECASE,
    )

    env_map: dict[str, str] = {}
    ws_fields: list[tuple[str, str]] = []  # (ws_name, env_name)

    def _replace_assign(m: re.Match) -> str:
        sel_name = m.group("sel").upper()
        target = m.group("target")
        env_name = f"DD_{sel_name.replace('-', '_')}"
        env_map[sel_name] = env_name

        if not _needs_assign_rewrite(source, sel_name, target):
            return m.group(0)  # Leave unchanged

        ws_name = f"WS-PATH-{sel_name}"
        ws_fields.append((ws_name, env_name))
        return f"{m.group(1)}{ws_name}"

    rewritten = select_re.sub(_replace_assign, source)

    # Insert WS field declarations and ACCEPT statements only if we rewrote anything
    if ws_fields:
        # Determine if source is free-format or fixed-format
        is_fixed = any(
            len(line) >= 7 and line[6] in (" ", "*", "-", "/")
            for line in rewritten.splitlines()[:20]
            if line.strip()
        )
        if is_fixed:
            comment_prefix = "      *"
            ws_indent = "       "
            stmt_indent = "           "
        else:
            comment_prefix = "      *>"
            ws_indent = "       "
            stmt_indent = "           "

        # Add WS path fields before PROCEDURE DIVISION
        ws_block = f"\n{comment_prefix} POBOL: auto-generated file path fields\n"
        for ws_name, _ in ws_fields:
            ws_block += f"{ws_indent}01  {ws_name:<30} PIC X(256).\n"

        proc_re = re.compile(
            r"(^\s*PROCEDURE\s+DIVISION\.)", re.MULTILINE | re.IGNORECASE
        )
        if proc_re.search(rewritten):
            rewritten = proc_re.sub(ws_block + r"\n\1", rewritten)

        # Add ACCEPT statements after first paragraph label in PROCEDURE DIVISION
        accept_block = f"\n{comment_prefix} POBOL: load file paths from environment\n"
        for ws_name, env_name in ws_fields:
            accept_block += (
                f'{stmt_indent}ACCEPT {ws_name} FROM ENVIRONMENT "{env_name}"\n'
            )

        proc_match = proc_re.search(rewritten)
        if proc_match:
            after_proc = rewritten[proc_match.end() :]
            para_re = re.compile(r"^(\s*[A-Z][A-Z0-9-]*\.\s*$)", re.MULTILINE)
            para_match = para_re.search(after_proc)
            if para_match:
                insert_pos = proc_match.end() + para_match.end()
                rewritten = (
                    rewritten[:insert_pos] + accept_block + rewritten[insert_pos:]
                )

    return rewritten, env_map


# ---------------------------------------------------------------------------
# SELECT / FD extraction
# ---------------------------------------------------------------------------


def _extract_selects(source: str) -> dict[str, str]:
    """Extract SELECT name → ASSIGN TO name mappings."""
    select_re = re.compile(
        r"SELECT\s+([A-Za-z0-9-]+)\s+ASSIGN\s+TO\s+"
        r'(?:EXTERNAL\s+)?([A-Za-z0-9-]+|"[^"]+")',
        re.IGNORECASE,
    )
    result = {}
    for m in select_re.finditer(source):
        sel_name = m.group(1).upper()
        assign_name = m.group(2).strip('"').upper()
        result[sel_name] = assign_name
    return result


def _detect_direction(source: str, select_name: str) -> str:
    """Heuristically detect if a file is input, output, or both by looking
    at OPEN statements in the PROCEDURE DIVISION."""
    # Handle multi-file OPEN: OPEN INPUT file1 file2 OUTPUT file3 file4
    # We need to check if our name falls between an INPUT/OUTPUT keyword and the next one
    open_re = re.compile(
        r"OPEN\s+((?:(?:INPUT|OUTPUT|I-O|EXTEND)\s+[A-Za-z0-9-]+(?:\s+[A-Za-z0-9-]+)*\s*)+)",
        re.IGNORECASE,
    )
    for m in open_re.finditer(source):
        open_clause = m.group(1)
        # Parse the clause: keyword followed by file names
        current_mode = None
        for token in open_clause.split():
            upper = token.upper().strip(".")
            if upper in ("INPUT", "OUTPUT", "I-O", "EXTEND"):
                current_mode = (
                    "input"
                    if upper == "INPUT"
                    else ("output" if upper in ("OUTPUT", "EXTEND") else "input-output")
                )
            elif upper == select_name.upper():
                return current_mode or "unknown"

    return "unknown"


def _extract_fd_records(
    source: str, select_names: set[str]
) -> dict[str, list[tuple[str, str]]]:
    """Extract FD sections and their 01-level record definitions.

    Returns dict mapping select_name → [(record_name, record_source), ...].
    """
    result: dict[str, list[tuple[str, str]]] = {name: [] for name in select_names}

    # Build a mapping from FD name to select name
    # FD names match SELECT names
    lines = source.splitlines()
    current_fd: str | None = None
    current_record_name: str | None = None
    current_record_lines: list[str] = []
    in_file_section = False

    fd_re = re.compile(r"^\s*FD\s+([A-Za-z0-9-]+)", re.IGNORECASE)
    level_01_re = re.compile(r"^\s*01\s+([A-Za-z0-9-]+)", re.IGNORECASE)
    section_re = re.compile(
        r"^\s*(WORKING-STORAGE\s+SECTION|PROCEDURE\s+DIVISION|LINKAGE\s+SECTION|"
        r"LOCAL-STORAGE\s+SECTION|SCREEN\s+SECTION|REPORT\s+SECTION)",
        re.IGNORECASE,
    )

    def _flush_record():
        nonlocal current_record_name, current_record_lines, current_fd
        if current_fd and current_record_name and current_record_lines:
            record_src = "\n".join(current_record_lines)
            if current_fd.upper() in result:
                result[current_fd.upper()].append((current_record_name, record_src))
        current_record_name = None
        current_record_lines = []

    for line in lines:
        # Detect we've left the FILE SECTION
        if section_re.search(line):
            _flush_record()
            current_fd = None
            in_file_section = False
            continue

        if re.search(r"FILE\s+SECTION", line, re.IGNORECASE):
            in_file_section = True
            continue

        if not in_file_section:
            continue

        # Skip comments
        stripped = line.lstrip()
        if stripped.startswith("*"):
            continue

        # New FD
        fd_match = fd_re.match(line)
        if fd_match:
            _flush_record()
            fd_name = fd_match.group(1).upper()
            if fd_name in result:
                current_fd = fd_name
            else:
                current_fd = None
            continue

        if current_fd is None:
            continue

        # New 01-level record
        rec_match = level_01_re.match(line)
        if rec_match:
            _flush_record()
            current_record_name = rec_match.group(1).upper()
            current_record_lines = [line]
            continue

        # Continuation of current record
        if current_record_name:
            current_record_lines.append(line)

    _flush_record()
    return result


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------


def parse_cobol_source(
    source: str | Path,
    *,
    strip_mainframe: bool = True,
    rewrite_assigns: bool = True,
) -> ParsedSource:
    """Parse a COBOL source file and extract all file I/O metadata.

    Parameters
    ----------
    source : str or Path
        Path to a .cob/.cbl file, or the raw source text.
    strip_mainframe : bool
        Strip sequence numbers, LABEL RECORDS, RECORDING MODE, etc.
    rewrite_assigns : bool
        Rewrite SELECT...ASSIGN TO clauses for env-var file mapping.

    Returns
    -------
    ParsedSource with file specs and cleaned source.
    """
    if isinstance(source, Path):
        source = source.resolve()
        raw = source.read_text(encoding="ascii", errors="replace")
    elif isinstance(source, str) and "\n" not in source and len(source) < 300:
        path = Path(source).resolve()
        if path.exists():
            raw = path.read_text(encoding="ascii", errors="replace")
        else:
            raw = source
    else:
        raw = source

    # Step 1: Clean mainframe artifacts
    if strip_mainframe:
        cleaned = strip_mainframe_format(raw)
    else:
        cleaned = raw

    # Step 2: Extract SELECT clauses (before rewriting)
    selects = _extract_selects(cleaned)

    # Step 3: Detect file directions
    directions: dict[str, str] = {}
    for sel_name in selects:
        directions[sel_name] = _detect_direction(cleaned, sel_name)

    # Step 4: Extract FD record layouts
    fd_records = _extract_fd_records(cleaned, set(selects.keys()))

    # Step 5: Build FileSpec objects
    files: dict[str, FileSpec] = {}
    for sel_name, assign_name in selects.items():
        fs = FileSpec(
            select_name=sel_name,
            assign_name=assign_name,
            direction=directions.get(sel_name, "unknown"),
        )
        for rec_name, rec_source in fd_records.get(sel_name, []):
            try:
                cb = parse_copybook(rec_source, name=rec_name)
                if cb.fields:  # Only add if we found parseable fields
                    fs.record_layouts[rec_name] = cb
            except CopybookParseError:
                pass  # Skip unparseable records
        files[sel_name] = fs

    # Step 6: Rewrite assigns for env-var mapping
    if rewrite_assigns:
        final_source, env_map = _rewrite_assigns_for_env(cleaned)
    else:
        final_source = cleaned

    return ParsedSource(files=files, cleaned_source=final_source)
