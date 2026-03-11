"""pobol — call COBOL programs as Python functions."""

from pobol.program import CobolProgram, load
from pobol.copybook import Copybook, parse_copybook
from pobol.source_parser import parse_cobol_source, strip_mainframe_format, ParsedSource
from pobol.exceptions import (
    CompileError,
    CobolRuntimeError,
    CopybookParseError,
    PyCobolError,
)

__all__ = [
    "CobolProgram",
    "load",
    "Copybook",
    "parse_copybook",
    "parse_cobol_source",
    "strip_mainframe_format",
    "ParsedSource",
    "CompileError",
    "CobolRuntimeError",
    "CopybookParseError",
    "PyCobolError",
]
