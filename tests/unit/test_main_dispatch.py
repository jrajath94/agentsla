"""Coverage for ``agentsla.__main__`` dispatch."""

from __future__ import annotations

import pytest

from agentsla.__main__ import main as unified_main
from agentsla.cli import run as run_mod  # noqa: F401  (dispatcher imports verified)


def test_unified_dispatches_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def fake_run(argv: list[str]) -> int:
        called.append(argv)
        return 0

    monkeypatch.setattr("agentsla.__main__.run_mod.main", fake_run)
    assert unified_main(["run", "--db", "/tmp/y.duckdb"]) == 0
    assert called == [["--db", "/tmp/y.duckdb"]]


def test_unified_dispatches_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def fake_replay(argv: list[str]) -> int:
        called.append(argv)
        return 0

    monkeypatch.setattr("agentsla.__main__.replay_mod.main", fake_replay)
    assert unified_main(["replay", "tid", "--mode", "tolerant"]) == 0
    assert called == [["tid", "--mode", "tolerant"]]


def test_unified_unknown_command(capsys: object) -> None:
    rc = unified_main(["weird"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert rc == 1
    assert "unknown subcommand" in captured.err


def test_unified_no_args(capsys: object) -> None:
    rc = unified_main([])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert rc == 1
    assert "usage:" in captured.err
