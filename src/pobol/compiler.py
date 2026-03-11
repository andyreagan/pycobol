"""Thin wrapper around the ``cobc`` compiler shipped with GnuCOBOL."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from pobol.exceptions import CompileError

# Default directory for caching compiled binaries
_CACHE_DIR = Path(tempfile.gettempdir()) / "pobol_cache"


def _source_hash(source_path: Path) -> str:
    """Return a short content-hash of the source file for cache-busting."""
    h = hashlib.sha256(source_path.read_bytes()).hexdigest()[:16]
    return h


def compile_program(
    source_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    extra_flags: list[str] | None = None,
    dialect: str | None = None,
    force: bool = False,
) -> Path:
    """Compile a COBOL source file to an executable using ``cobc -x``.

    Parameters
    ----------
    source_path:
        Path to the ``.cob`` / ``.cbl`` file.
    output_dir:
        Where to put the binary. Defaults to a temp cache dir.
    extra_flags:
        Additional flags passed to ``cobc``.
    dialect:
        E.g. ``"ibm"``, ``"mf"``, ``"cobol85"`` — passed as ``-std=<dialect>``.
    force:
        Re-compile even if a cached binary exists.

    Returns
    -------
    Path to the compiled executable.
    """
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"COBOL source not found: {source_path}")

    if output_dir is None:
        output_dir = _CACHE_DIR
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    h = _source_hash(source_path)
    exe_name = f"{source_path.stem}_{h}"
    exe_path = output_dir / exe_name

    if exe_path.exists() and not force:
        return exe_path

    cmd: list[str] = ["cobc", "-x", "-o", str(exe_path)]
    if dialect:
        cmd.append(f"-std={dialect}")
    if extra_flags:
        cmd.extend(extra_flags)
    cmd.append(str(source_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CompileError(str(source_path), result.returncode, result.stderr)

    # Ensure executable bit
    exe_path.chmod(exe_path.stat().st_mode | 0o111)
    return exe_path
