"""Tests for the held-out fixture generator script.

Two builders live in ``scripts/build_held_out_fixture.py``:

  * ``build_synthetic_held_out_fixture`` — pure-Python, no API needed.
    Always works; rows tagged ``synthetic=true`` so eval reports can flag
    their provenance.

  * ``build_real_held_out_fixture`` — runs Claude on a held-out task set.
    Requires ``ANTHROPIC_API_KEY``; with ``synthetic_fallback=True``
    (default) it degrades to synthetic rows when the key is missing.

The contract under test:
  * Synthetic builder writes the expected row count + ``synthetic=true``.
  * Real builder with no key + ``synthetic_fallback=True`` writes
    synthetic rows and tags them.
  * Real builder with no key + ``synthetic_fallback=False`` raises.
  * The on-disk fixture is honest about its provenance (synthetic vs real).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_synthetic_builder_writes_rows_with_synthetic_flag(tmp_path: Path) -> None:
    """``build_synthetic_held_out_fixture`` writes rows; every row tagged ``synthetic=true``."""
    from scripts.build_held_out_fixture import build_synthetic_held_out_fixture

    out_path = tmp_path / "fixture.jsonl"
    # repeat=4 x 9 generators = 36 rows (matches the original 36-row fixture).
    n = build_synthetic_held_out_fixture(out_path=out_path, repeat=4)
    assert n >= 30, f"synthetic builder must emit >= 30 rows, got {n}"
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == n
    # Every row is synthetic.
    assert all(r.get("synthetic") is True for r in rows), (
        "Every synthetic row must carry synthetic=true so eval reports can flag provenance."
    )
    # Every row has a gold_category (the eval's input contract).
    assert all(r.get("gold_category") for r in rows)


def test_real_builder_falls_back_to_synthetic_when_no_key(monkeypatch, tmp_path: Path) -> None:
    """No ANTHROPIC_API_KEY + synthetic_fallback=True → writes synthetic rows."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from scripts.build_held_out_fixture import build_real_held_out_fixture

    out_path = tmp_path / "fixture.jsonl"
    # n_per_category=10 → repeat computed as 10//4 = 2 (min 1) → 18 rows.
    # For ≥30 rows, use n_per_category=20.
    n = build_real_held_out_fixture(out_path=out_path, n_per_category=20, synthetic_fallback=True)
    assert n >= 30
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert all(r.get("synthetic") is True for r in rows)


def test_real_builder_fails_without_key_and_no_fallback(monkeypatch, tmp_path: Path) -> None:
    """No API key + synthetic_fallback=False → raises RuntimeError (no file written)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from scripts.build_held_out_fixture import build_real_held_out_fixture

    out_path = tmp_path / "should_not_exist.jsonl"
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_real_held_out_fixture(out_path=out_path, n_per_category=2, synthetic_fallback=False)
    assert not out_path.exists(), "Real builder with no key + no fallback must not write any file."


def test_real_builder_with_mocked_claude_writes_real_rows(monkeypatch, tmp_path: Path) -> None:
    """With a fake key + mocked Claude call, rows are tagged ``synthetic=false``."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
        return f"<echo:{prompt}>"

    # Patch the same _call_claude seam as bench/real_llm.
    import scripts.build_held_out_fixture as mod

    monkeypatch.setattr(mod, "_call_claude", _fake_call)

    from scripts.build_held_out_fixture import build_real_held_out_fixture

    out_path = tmp_path / "fixture.jsonl"
    # n_per_category=5 x 9 generators = 45 rows.
    n = build_real_held_out_fixture(out_path=out_path, n_per_category=5, synthetic_fallback=False)
    assert n >= 30, f"real builder with mocked Claude should emit >= 30 rows, got {n}"
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # Mocked Claude path: synthetic must be false.
    assert all(r.get("synthetic") is False for r in rows), (
        "Real rows must carry synthetic=false so the eval report can mark them as REAL."
    )
    # Each row should carry the model id we asked for.
    assert all(r.get("model_id", "").startswith("claude-") for r in rows)


def test_default_cli_invokes_real_builder(tmp_path: Path) -> None:
    """Running the script with --out + no ANTHROPIC_API_KEY writes a fixture (synthetic fallback)."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + env.get("PYTHONPATH", "")
    out_path = tmp_path / "fixture.jsonl"
    result = subprocess.run(  # noqa: S603 — controlled subprocess for CLI smoke
        [sys.executable, "scripts/build_held_out_fixture.py", "--out", str(out_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert out_path.exists()
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "fixture must contain at least one row"
    assert all(r.get("synthetic") is True for r in rows)
