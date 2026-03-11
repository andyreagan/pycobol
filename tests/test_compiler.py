"""Tests for the COBOL compiler wrapper."""

import pytest
from pathlib import Path

from pobol.compiler import compile_program
from pobol.exceptions import CompileError


class TestCompileProgram:
    def test_compiles_hello(self, examples_dir):
        exe = compile_program(examples_dir / "hello.cob")
        assert exe.exists()
        assert exe.stat().st_mode & 0o111  # executable

    def test_cached_compilation(self, examples_dir):
        """Second compile should return same cached path."""
        exe1 = compile_program(examples_dir / "hello.cob")
        exe2 = compile_program(examples_dir / "hello.cob")
        assert exe1 == exe2

    def test_force_recompile(self, examples_dir):
        exe1 = compile_program(examples_dir / "hello.cob")
        exe2 = compile_program(examples_dir / "hello.cob", force=True)
        assert exe1 == exe2  # same path, but re-built

    def test_compile_error_on_bad_source(self, tmp_path):
        bad_file = tmp_path / "bad.cob"
        bad_file.write_text("THIS IS NOT VALID COBOL")
        with pytest.raises(CompileError) as exc_info:
            compile_program(bad_file)
        assert exc_info.value.returncode != 0

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            compile_program("/nonexistent/path.cob")
