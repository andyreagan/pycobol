"""Shared fixtures for pobol tests."""

import pytest
from pathlib import Path


@pytest.fixture
def examples_dir() -> Path:
    """Return the path to the examples/cobol directory."""
    return Path(__file__).parent.parent / "examples" / "cobol"
