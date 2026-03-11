"""Exceptions for pobol."""


class PyCobolError(Exception):
    """Base exception for all pobol errors."""


class CompileError(PyCobolError):
    """Raised when cobc fails to compile a COBOL source file."""

    def __init__(self, source_path: str, returncode: int, stderr: str):
        self.source_path = source_path
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"cobc compilation failed (rc={returncode}) for {source_path}:\n{stderr}"
        )


class CobolRuntimeError(PyCobolError):
    """Raised when a compiled COBOL program exits with a non-zero return code."""

    def __init__(self, program_path: str, returncode: int, stderr: str, stdout: str):
        self.program_path = program_path
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        super().__init__(
            f"COBOL program {program_path} failed (rc={returncode}):\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )


class CopybookParseError(PyCobolError):
    """Raised when a copybook / record layout cannot be parsed."""
