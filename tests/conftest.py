"""Shared pytest fixtures for the agentsla test suite.

The fixtures here are intentionally minimal — each Phase 1 plan node adds its
own focused fixtures (test-local) rather than expanding this module. The goal
is to keep `conftest.py` as small as possible so failures point to the test, not
the global plumbing.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Path to the repository root (this conftest's parent's parent)."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture()
def tmp_dir() -> Iterator[Path]:
    """Per-test temp directory; auto-cleaned.

    Replaces the more common `tmp_path` fixture when a test wants a *named*
    directory for ease of debugging failed runs.
    """
    with tempfile.TemporaryDirectory(prefix="agentsla-test-") as d:
        old = os.getcwd()
        os.chdir(d)
        try:
            yield Path(d)
        finally:
            os.chdir(old)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-tag slow tests as `integration` if they touch the filesystem heavily."""
    # Kept explicit (markers = ["integration"]) for now; this hook is a placeholder
    # so future test gradations (e.g. network, GPU) can be tagged by path/name.
    _ = (config, items, sys.modules)
