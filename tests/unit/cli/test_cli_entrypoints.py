"""CLI module coverage: parse argv, dispatch, write to trace store, replay."""

from __future__ import annotations

import json
from pathlib import Path

from agentsla.cli.replay import main as replay_main
from agentsla.cli.run import main as run_main


def test_run_main_writes_trace(tmp_path: Path, capsys: object) -> None:
    db = tmp_path / "traces.duckdb"
    rc = run_main(["--db", str(db), "--text", "abc"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["db"] == str(db)
    tid = payload["trace_id"]
    # Now replay it via the same CLI entry point.
    rc = replay_main([tid, "--db", str(db), "--mode", "strict"])
    assert rc == 0


def test_run_main_default_db_in_cwd(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    rc = run_main(["--text", "cwd"])
    assert rc == 0


def test_replay_main_unknown_trace(tmp_path: Path, capsys: object) -> None:
    db = tmp_path / "traces.duckdb"
    rc = replay_main(["00000000-0000-4000-8000-000000000000", "--db", str(db)])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert rc == 2
    payload = json.loads(captured.out)
    assert payload["found"] is False
