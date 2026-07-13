"""Unit tests for bench/real_llm.py — mocked Claude responses, no live API.

The harness path is real (parquet schema, CLI exit codes, env validation) but
the underlying ``_call_claude`` function is patched so tests never hit the
network. Without an ``ANTHROPIC_API_KEY`` the harness must fail fast with a
clear, parseable error so CI / fresh-clone reproducers get the right signal.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def test_real_llm_requires_api_key_or_fails_clean(monkeypatch, tmp_path: Path) -> None:
    """No ANTHROPIC_API_KEY → clear error, no parquet written."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from agentsla.bench.real_llm import run_real_llm_bench

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        run_real_llm_bench(out_path=tmp_path / "out.parquet")
    assert not (tmp_path / "out.parquet").exists(), (
        "Parquet must not be written when the API key is missing — the harness should fail before touching disk."
    )


def test_real_llm_schema_matches_parquet(monkeypatch, tmp_path: Path) -> None:
    """Mocked Claude response → row schema valid; parquet written with expected fields."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
        return f"<echo:{prompt}>"

    from agentsla.bench.real_llm import RealLlmRow, run_real_llm_bench

    with patch("agentsla.bench.real_llm._call_claude", side_effect=_fake_call):
        out_path = tmp_path / "real_llm.parquet"
        run_real_llm_bench(tasks_per_domain=2, seeds=1, out_path=out_path)

    assert out_path.exists(), "parquet must be written when the key is present"
    table = pq.read_table(out_path)
    fields = set(table.schema.names)
    # Every RealLlmRow field must be present in the parquet schema.
    expected = set(RealLlmRow.__dataclass_fields__.keys())
    assert expected <= fields, f"missing parquet columns: {expected - fields}"
    # Schema must carry at least one row per (task, seed).
    assert table.num_rows >= 1, "parquet should contain at least one run"
    pylist = table.to_pylist()
    for r in pylist:
        assert r["task_id"], f"empty task_id in row: {r}"
        assert r["latency_ms"] >= 0.0


def test_real_llm_emits_not_yet_measured_marker_when_no_key(tmp_path: Path) -> None:
    """No key → CLI exits 2 with a clear ANTHROPIC_API_KEY message; no parquet produced."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    out_path = tmp_path / "should_not_exist.parquet"
    result = subprocess.run(  # noqa: S603 — controlled subprocess for CLI smoke
        [
            sys.executable,
            "-m",
            "agentsla",
            "bench-real",
            "--tasks-per-domain",
            "1",
            "--seeds",
            "1",
            "--out",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 2, f"CLI must exit 2 on missing key; got {result.returncode}. stderr={result.stderr!r}"
    assert "ANTHROPIC_API_KEY" in result.stderr, f"stderr must name ANTHROPIC_API_KEY so the user knows what to set. stderr={result.stderr!r}"
    assert not out_path.exists(), "parquet must not be written when key is missing"


def test_real_llm_cli_help_renders() -> None:
    """CLI help must be parseable so it shows up in README / docs."""
    result = subprocess.run(
        [sys.executable, "-m", "agentsla", "bench-real", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ANTHROPIC_API_KEY" in result.stdout, "help must document the key requirement"
    assert "--tasks-per-domain" in result.stdout
    assert "--out" in result.stdout


def test_real_llm_row_dataclass_serializes_to_arrow(tmp_path: Path) -> None:
    """The row dataclass shape must be a valid PyArrow struct (no exotic types)."""
    from agentsla.bench.real_llm import RealLlmRow

    row = RealLlmRow(
        mode="naked",
        task_id="finops-001",
        domain="financial_ops",
        model_id="claude-haiku-4-5-20251001",
        seed=0,
        success=True,
        gate_passed=False,
        verified_at_truth=None,
        sensitivity=None,
        specificity=None,
        latency_ms=12.34,
        text="<echo:Compute the value.>",
        note="",
    )
    out_path = tmp_path / "single_row.parquet"
    table = pa.Table.from_pylist([row.__dict__])
    pq.write_table(table, out_path)
    roundtrip = pq.read_table(out_path).to_pylist()
    assert roundtrip[0]["task_id"] == "finops-001"
    assert roundtrip[0]["model_id"] == "claude-haiku-4-5-20251001"
