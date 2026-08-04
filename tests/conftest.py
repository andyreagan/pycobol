"""Shared fixtures for pobol tests."""

from pathlib import Path

import pytest


@pytest.fixture
def examples_dir() -> Path:
    """Return the path to the examples/cobol directory."""
    return Path(__file__).parent.parent / "examples" / "cobol"
