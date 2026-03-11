"""Core module: load a COBOL source file and call it like a Python function.

Usage
-----

    from pobol import load

    # Auto mode: pobol parses the COBOL source, discovers all files,
    # extracts record layouts, handles compilation and file I/O.
    prog = load("check_disbursement.cbl")
    print(prog.file_info)  # shows discovered inputs/outputs

    result = prog(
        txn_data_file=[header_record, detail_record, trailer_record],
        print_setup=[setup_line_1, setup_line_2],
    )
    print(result.check_out_file)   # output records as list[dict]
    print(result.recon_file)       # another output

    # Manual mode still works for simple programs:
    add = load("add_numbers.cob")
    result = add(stdin="0010000200")

    # Or explicit copybook overrides:
    report = load(
        "report.cob",
        inputs={"INPUT-FILE": my_copybook},
        outputs={"OUTPUT-FILE": my_copybook},
    )
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pobol.compiler import compile_program
from pobol.copybook import Copybook
from pobol.exceptions import CobolRuntimeError
from pobol.source_parser import ParsedSource, parse_cobol_source


@dataclass
class CobolResult:
    """Result of running a COBOL program."""

    return_code: int
    stdout: str
    stderr: str
    outputs: dict[str, list[dict[str, Any]]]

    def __getattr__(self, name: str) -> Any:
        """Allow ``result.output_file`` as shorthand for
        ``result.outputs["OUTPUT-FILE"]``."""
        cobol_name = name.upper().replace("_", "-")
        if cobol_name in self.outputs:
            return self.outputs[cobol_name]
        raise AttributeError(f"No output named {name!r} (tried {cobol_name!r})")


def _normalize_dd(name: str) -> str:
    """Normalize a DD / SELECT name for use as dict key."""
    return name.upper().replace("_", "-")


def _fd_env_var(select_name: str) -> str:
    """Derive the environment variable name for a SELECT name.

    Convention: ``DD_<SELECT-NAME>`` with hyphens → underscores.
    """
    return "DD_" + select_name.upper().replace("-", "_")


class CobolProgram:
    """A compiled COBOL program you can call from Python.

    Can operate in two modes:

    **Auto mode** (default): The COBOL source is parsed to discover all
    SELECT/FD file descriptors and their record layouts. Inputs and outputs
    are determined from OPEN statements.

    **Manual mode**: You explicitly provide ``inputs`` and ``outputs`` dicts
    mapping SELECT names to Copybooks.

    Parameters
    ----------
    source : str | Path
        Path to the .cob/.cbl source file.
    inputs : dict mapping SELECT-name → Copybook, optional
        Override auto-discovered input file layouts.
    outputs : dict mapping SELECT-name → Copybook, optional
        Override auto-discovered output file layouts.
    dialect : str | None
        GnuCOBOL -std dialect.
    extra_flags : list[str] | None
        Extra cobc flags.
    strip_mainframe : bool
        Auto-strip mainframe format (sequence numbers, LABEL RECORDS, etc.)
    rewrite_assigns : bool
        Auto-rewrite ASSIGN TO clauses for env-var file mapping.
    """

    def __init__(
        self,
        source: str | Path,
        *,
        inputs: dict[str, Copybook] | None = None,
        outputs: dict[str, Copybook] | None = None,
        dialect: str | None = None,
        extra_flags: list[str] | None = None,
        strip_mainframe: bool = True,
        rewrite_assigns: bool = True,
    ):
        self.source = Path(source).resolve()
        self.dialect = dialect
        self.extra_flags = extra_flags

        # Parse the source to discover file I/O
        self._parsed = parse_cobol_source(
            self.source,
            strip_mainframe=strip_mainframe,
            rewrite_assigns=rewrite_assigns,
        )

        # Build input/output maps
        # Start with auto-discovered, then overlay manual overrides
        self.inputs: dict[str, Copybook] = {}
        self.outputs: dict[str, Copybook] = {}
        self._all_files = self._parsed.files

        for sel_name, fspec in self._parsed.files.items():
            # Pick the record layout to use.
            # If there are multiple 01-levels under one FD, we keep all of them
            # but the "primary" one (largest) is used for default encode/decode.
            if fspec.record_layouts:
                # Use the largest layout as default
                primary_cb = max(
                    fspec.record_layouts.values(), key=lambda cb: cb.record_length
                )
            else:
                primary_cb = None

            if fspec.direction == "input" and primary_cb:
                self.inputs[sel_name] = primary_cb
            elif fspec.direction == "output" and primary_cb:
                self.outputs[sel_name] = primary_cb

        # Apply manual overrides
        if inputs:
            for k, v in inputs.items():
                self.inputs[_normalize_dd(k)] = v
        if outputs:
            for k, v in outputs.items():
                self.outputs[_normalize_dd(k)] = v

        # Compile from the cleaned/rewritten source
        if self._parsed.cleaned_source:
            # Write the cleaned source to a temp file for compilation.
            # Use a short name — cobc has a max filename length.
            stem = self.source.stem[:16]
            self._compile_source = Path(tempfile.mktemp(
                suffix=".cob", prefix=f"{stem}_"
            ))
            self._compile_source.write_text(self._parsed.cleaned_source)
        else:
            self._compile_source = self.source

        self._exe = compile_program(
            self._compile_source, dialect=dialect, extra_flags=extra_flags
        )

    @property
    def file_info(self) -> str:
        """Human-readable summary of discovered file I/O."""
        lines = [f"COBOL Program: {self.source.name}", ""]
        if self.inputs:
            lines.append("INPUTS:")
            for name, cb in self.inputs.items():
                fields = ", ".join(f.name for f in cb.fields)
                lines.append(f"  {name} ({cb.record_length} bytes): {fields}")
        if self.outputs:
            lines.append("OUTPUTS:")
            for name, cb in self.outputs.items():
                fields = ", ".join(f.name for f in cb.fields)
                lines.append(f"  {name} ({cb.record_length} bytes): {fields}")

        unknown = [
            name for name, fs in self._all_files.items()
            if name not in self.inputs and name not in self.outputs
        ]
        if unknown:
            lines.append("UNCLASSIFIED (use inputs=/outputs= to override):")
            for name in unknown:
                fs = self._all_files[name]
                layouts = list(fs.record_layouts.keys())
                lines.append(f"  {name} (direction={fs.direction}): {layouts}")

        return "\n".join(lines)

    @property
    def record_layouts(self) -> dict[str, dict[str, Copybook]]:
        """All discovered record layouts organized by file.

        Returns dict[select_name → dict[record_name → Copybook]].
        Useful for inspecting multi-record FDs (header/detail/trailer).
        """
        return {
            name: fspec.record_layouts
            for name, fspec in self._all_files.items()
        }

    def __call__(
        self,
        *,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = 30.0,
        raw_files: dict[str, bytes] | None = None,
        **file_inputs: list[dict[str, Any]] | list[bytes],
    ) -> CobolResult:
        """Run the program.

        Keyword arguments whose names match input file descriptors (with
        underscores replacing hyphens) are serialized to temp files.

        For complex multi-record files, pass ``raw_files={"SELECT-NAME": b"..."}``
        with pre-encoded bytes.

        Returns a :class:`CobolResult` with stdout/stderr and decoded output
        file data.
        """
        run_env = {**os.environ}
        if env:
            run_env.update(env)

        tmpdir = tempfile.mkdtemp(prefix="pobol_run_")

        # --- write input files ---
        for kwarg_name, records in file_inputs.items():
            dd_name = _normalize_dd(kwarg_name)
            if dd_name not in self.inputs:
                raise ValueError(
                    f"Unknown input file {kwarg_name!r} (known: {list(self.inputs)})"
                )
            cb = self.inputs[dd_name]
            path = os.path.join(tmpdir, dd_name.replace("-", "_") + ".dat")
            with open(path, "wb") as f:
                if records and isinstance(records[0], (bytes, bytearray)):
                    # Raw pre-encoded records
                    f.write(b"\n".join(records) + b"\n")
                else:
                    f.write(cb.encode_many(records))
            run_env[_fd_env_var(dd_name)] = path

        # --- write raw files ---
        if raw_files:
            for dd_name_raw, data in raw_files.items():
                dd_name = _normalize_dd(dd_name_raw)
                path = os.path.join(tmpdir, dd_name.replace("-", "_") + ".dat")
                with open(path, "wb") as f:
                    f.write(data)
                run_env[_fd_env_var(dd_name)] = path

        # --- prepare output files ---
        output_paths: dict[str, str] = {}
        for dd_name in self.outputs:
            path = os.path.join(tmpdir, dd_name.replace("-", "_") + "_out.dat")
            open(path, "w").close()
            output_paths[dd_name] = path
            run_env[_fd_env_var(dd_name)] = path

        # --- also set env vars for any files we haven't covered ---
        # (files that are both input+output, or unclassified)
        for dd_name, fspec in self._all_files.items():
            env_var = _fd_env_var(dd_name)
            if env_var not in run_env:
                path = os.path.join(tmpdir, dd_name.replace("-", "_") + ".dat")
                if not os.path.exists(path):
                    open(path, "w").close()
                run_env[env_var] = path

        # --- run ---
        proc = subprocess.run(
            [str(self._exe)],
            input=stdin,
            capture_output=True,
            text=True,
            env=run_env,
            timeout=timeout,
        )

        # --- read output files ---
        decoded_outputs: dict[str, list[dict[str, Any]]] = {}
        for dd_name, path in output_paths.items():
            cb = self.outputs[dd_name]
            with open(path, "rb") as f:
                data = f.read()
            decoded_outputs[dd_name] = cb.decode_many(data)

        if proc.returncode != 0:
            raise CobolRuntimeError(
                str(self._exe), proc.returncode, proc.stderr, proc.stdout
            )

        return CobolResult(
            return_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            outputs=decoded_outputs,
        )

    def recompile(self) -> None:
        """Force recompilation of the source."""
        self._exe = compile_program(
            self._compile_source,
            dialect=self.dialect,
            extra_flags=self.extra_flags,
            force=True,
        )

    def __repr__(self) -> str:
        return (
            f"CobolProgram({self.source.name!r}, "
            f"inputs={list(self.inputs)}, outputs={list(self.outputs)})"
        )


def load(
    source: str | Path,
    *,
    inputs: dict[str, Copybook] | None = None,
    outputs: dict[str, Copybook] | None = None,
    dialect: str | None = None,
    extra_flags: list[str] | None = None,
    strip_mainframe: bool = True,
    rewrite_assigns: bool = True,
) -> CobolProgram:
    """Convenience function: compile a COBOL source and return a callable.

    In the simplest case, just::

        prog = load("my_program.cbl")
        result = prog(input_file=[...])

    pobol will:
    - Parse the source to discover all SELECT/FD file descriptors
    - Extract record layouts (PIC clauses) automatically
    - Strip mainframe artifacts (sequence numbers, LABEL RECORDS, etc.)
    - Rewrite ASSIGN TO clauses for env-var file mapping
    - Compile with cobc
    - Handle all temp file I/O when you call it
    """
    return CobolProgram(
        source,
        inputs=inputs,
        outputs=outputs,
        dialect=dialect,
        extra_flags=extra_flags,
        strip_mainframe=strip_mainframe,
        rewrite_assigns=rewrite_assigns,
    )
