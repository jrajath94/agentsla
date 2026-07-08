"""Smoke coverage for deterministic-fixture tool set (PITFALL #8)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentsla.tools.deterministic import ClockTool, FetchTool, JsonEchoTool


def test_fetch_reads_fixture(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
    assert FetchTool(root=tmp_path)(path="hello.txt") == "hi"


def test_fetch_rejects_escape_attempts(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FetchTool(root=tmp_path)(path="../escape.txt")


def test_clock_is_pure() -> None:
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    tool = ClockTool(fixed=fixed)
    assert tool() == tool()
    assert tool() == "2026-01-01T00:00:00+00:00"


def test_json_echo_is_stable() -> None:
    tool = JsonEchoTool()
    out = tool(x=1, y=2)
    # Sorted by key (sort_keys=True); round-trip via json.loads to assert
    # structural equality independent of any ordering quirks.
    import json as _json
    assert _json.loads(out) == {"x": 1, "y": 2}
