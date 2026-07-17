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
    """No ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN → clear error, no parquet written."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
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
    """No key (either env var) → CLI exits 2 with a clear ANTHROPIC_API_KEY message; no parquet produced."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
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


class TestResolveApiKey:
    def test_explicit_arg_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit api_key argument takes precedence over every env var."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-api")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-env-auth")
        from agentsla.bench.real_llm import _resolve_api_key

        assert _resolve_api_key("sk-explicit") == "sk-explicit"

    def test_anthropic_api_key_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ANTHROPIC_API_KEY is the primary env var (the SDK-native name)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-api")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        from agentsla.bench.real_llm import _resolve_api_key

        assert _resolve_api_key(None) == "sk-env-api"

    def test_anthropic_auth_token_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ANTHROPIC_AUTH_TOKEN is the secondary fallback (gateway-style auth)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-env-auth")
        from agentsla.bench.real_llm import _resolve_api_key

        assert _resolve_api_key(None) == "sk-env-auth"

    def test_missing_key_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No key anywhere → RuntimeError naming both env vars."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        from agentsla.bench.real_llm import _resolve_api_key

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            _resolve_api_key(None)


def _raise_if_called(prompt: str, *, model: str, api_key: str | None) -> str:
    raise AssertionError("_call_claude must NOT be invoked in this test — a paid API call would have happened")


class TestDryPlan:
    def test_dry_plan_performs_zero_api_calls_and_needs_no_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """--dry-plan must work with no key and never touch _call_claude."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        from agentsla.bench.real_llm import main

        out_path = tmp_path / "plan.parquet"
        with patch("agentsla.bench.real_llm._call_claude", side_effect=_raise_if_called):
            rc = main(
                [
                    "--dry-plan",
                    "--tasks-per-domain",
                    "1",
                    "--seeds",
                    "1",
                    "--out",
                    str(out_path),
                    "--cache-dir",
                    str(tmp_path / "cache"),
                ]
            )
        assert rc == 0, "dry plan must exit 0 without a key"
        assert not out_path.exists(), "dry plan must not write a parquet"

    def test_plan_counts_prompts_rows_and_domains(self, tmp_path: Path) -> None:
        from agentsla.bench.real_llm import plan_real_llm_bench

        plan = plan_real_llm_bench(tasks_per_domain=2, seeds=2, out_path=tmp_path / "o.parquet")
        assert plan.n_tasks == 6, "2 per domain * 3 domains"
        assert plan.n_prompts == 12, "tasks * seeds"
        assert plan.n_rows == 24, "2 modes per prompt"
        assert plan.n_live_calls == 12, "no cache => every prompt is a paid call"
        assert plan.out_exists is False
        rendered = plan.render()
        assert "PAID API calls: 12" in rendered

    def test_smoke_plan_covers_all_three_domains(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Rung C acceptance: tasks-per-domain=1 must select one task from EACH domain."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

        def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
            return "42"

        from agentsla.bench.real_llm import run_real_llm_bench

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_fake_call):
            out_path = tmp_path / "smoke.parquet"
            run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=out_path)
        domains = set(pq.read_table(out_path).column("domain").to_pylist())
        assert domains == {"financial_ops", "incident_triage", "doc_qa"}, f"smoke run must be stratified across domains; got {domains}"


class TestMaxPaidCalls:
    def test_blocks_accidental_large_run_before_any_call(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        out_path = tmp_path / "big.parquet"
        with (
            patch("agentsla.bench.real_llm._call_claude", side_effect=_raise_if_called),
            pytest.raises(RuntimeError, match="max-paid-calls"),
        ):
            run_real_llm_bench(tasks_per_domain=2, seeds=1, out_path=out_path, max_paid_calls=3)
        assert not out_path.exists(), "refused run must not write a parquet"

    def test_cli_default_cap_is_3(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Default CLI invocation with tasks-per-domain=2 (6 prompts) must refuse."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import main

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_raise_if_called):
            rc = main(
                [
                    "--tasks-per-domain",
                    "2",
                    "--seeds",
                    "1",
                    "--out",
                    str(tmp_path / "big.parquet"),
                    "--cache-dir",
                    str(tmp_path / "cache"),
                ]
            )
        assert rc == 2, "6 planned paid calls > default cap of 3 must exit 2"

    def test_raised_cap_allows_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

        def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
            return "42"

        from agentsla.bench.real_llm import run_real_llm_bench

        out_path = tmp_path / "ok.parquet"
        with patch("agentsla.bench.real_llm._call_claude", side_effect=_fake_call):
            rows = run_real_llm_bench(tasks_per_domain=2, seeds=1, out_path=out_path, max_paid_calls=6)
        assert len(rows) == 12
        assert out_path.exists()


class TestResponseCache:
    def test_cache_hit_does_not_invoke_call_claude(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Second run with --resume must serve every response from cache."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        cache_dir = tmp_path / "cache"

        def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
            return f"first-run-answer:{prompt[:20]}"

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_fake_call):
            first = run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=tmp_path / "a.parquet", cache_dir=cache_dir)
        assert all(not r.cached for r in first), "live rows must not be marked cached"

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_raise_if_called):
            second = run_real_llm_bench(
                tasks_per_domain=1,
                seeds=1,
                out_path=tmp_path / "b.parquet",
                cache_dir=cache_dir,
                resume=True,
            )
        assert len(second) == len(first)
        assert all(r.cached for r in second), "resume rows must be marked cached"
        assert {r.text for r in second} == {r.text for r in first}, "cached text must match the original responses"

    def test_resume_run_needs_no_api_key_when_fully_cached(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        cache_dir = tmp_path / "cache"
        with patch("agentsla.bench.real_llm._call_claude", side_effect=lambda prompt, *, model, api_key: "42"):
            run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=tmp_path / "a.parquet", cache_dir=cache_dir)

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        with patch("agentsla.bench.real_llm._call_claude", side_effect=_raise_if_called):
            rows = run_real_llm_bench(
                tasks_per_domain=1,
                seeds=1,
                out_path=tmp_path / "b.parquet",
                cache_dir=cache_dir,
                resume=True,
            )
        assert rows, "fully cached resume run must work offline without a key"

    def test_without_resume_cache_is_not_read(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Cache reads are opt-in: no --resume => live call happens even with a warm cache."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        cache_dir = tmp_path / "cache"
        with patch("agentsla.bench.real_llm._call_claude", side_effect=lambda prompt, *, model, api_key: "old"):
            run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=tmp_path / "a.parquet", cache_dir=cache_dir)
        with patch("agentsla.bench.real_llm._call_claude", side_effect=lambda prompt, *, model, api_key: "new") as mocked:
            rows = run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=tmp_path / "b.parquet", cache_dir=cache_dir)
        assert mocked.called, "without resume, the live path must be exercised"
        assert all(r.text == "new" for r in rows)


class TestOverwriteProtection:
    def test_existing_output_refused_without_overwrite(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        out_path = tmp_path / "existing.parquet"
        out_path.write_bytes(b"precious prior artifact")
        with (
            patch("agentsla.bench.real_llm._call_claude", side_effect=_raise_if_called),
            pytest.raises(RuntimeError, match="overwrite"),
        ):
            run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=out_path)
        assert out_path.read_bytes() == b"precious prior artifact", "refused run must leave the artifact untouched"

    def test_overwrite_flag_allows_replacement(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        out_path = tmp_path / "existing.parquet"
        out_path.write_bytes(b"old")
        with patch("agentsla.bench.real_llm._call_claude", side_effect=lambda prompt, *, model, api_key: "42"):
            rows = run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=out_path, overwrite=True)
        assert rows
        assert pq.read_table(out_path).num_rows == len(rows)


class TestFailFast:
    def test_fail_fast_stops_after_first_api_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        def _always_fail(prompt: str, *, model: str, api_key: str | None) -> str:
            raise ConnectionError("simulated provider outage")

        out_path = tmp_path / "partial.parquet"
        with patch("agentsla.bench.real_llm._call_claude", side_effect=_always_fail):
            rows = run_real_llm_bench(tasks_per_domain=2, seeds=1, out_path=out_path, max_paid_calls=6)
        # 6 tasks planned, but fail-fast stops after the FIRST error: 2 rows (naked+wrapped).
        assert len(rows) == 2, f"fail-fast must stop after the first error; got {len(rows)} rows"
        assert all(r.note.startswith("[NOT YET MEASURED]") for r in rows)
        assert out_path.exists(), "partial parquet must be kept (honest artifact)"

    def test_no_fail_fast_records_every_error_row(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import run_real_llm_bench

        def _always_fail(prompt: str, *, model: str, api_key: str | None) -> str:
            raise ConnectionError("simulated provider outage")

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_always_fail):
            rows = run_real_llm_bench(
                tasks_per_domain=2,
                seeds=1,
                out_path=tmp_path / "all_errors.parquet",
                max_paid_calls=6,
                fail_fast=False,
            )
        assert len(rows) == 12, "without fail-fast, every (task, seed) must emit its error rows"

    def test_cli_exits_1_when_fail_fast_triggers(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
        from agentsla.bench.real_llm import main

        def _always_fail(prompt: str, *, model: str, api_key: str | None) -> str:
            raise ConnectionError("simulated provider outage")

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_always_fail):
            rc = main(
                [
                    "--tasks-per-domain",
                    "1",
                    "--seeds",
                    "1",
                    "--out",
                    str(tmp_path / "partial.parquet"),
                    "--cache-dir",
                    str(tmp_path / "cache"),
                ]
            )
        assert rc == 1, "fail-fast abort must exit 1 so scripts notice the partial run"


class TestBothModesEmitted:
    def test_parquet_contains_both_naked_and_wrapped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """PRD F3 requires naked-vs-wrapped comparison. Run with mocked Claude
        that echoes the prompt; assert both modes appear in the parquet.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

        def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
            return f"<echo:{prompt[:30]}>"

        from agentsla.bench.real_llm import run_real_llm_bench

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_fake_call):
            out_path = tmp_path / "both_modes.parquet"
            run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=out_path)

        table = pq.read_table(out_path)
        modes = set(table.column("mode").to_pylist())
        assert modes == {"naked", "wrapped"}, f"parquet must carry both modes for honest comparison; got {modes}"
        # Row count must be 2 * (tasks_per_domain * 3 domains * seeds).
        assert table.num_rows == 2 * 1 * 3 * 1, f"expected 6 rows (2 modes * 1 task * 3 domains * 1 seed); got {table.num_rows}"

    def test_gate_denies_response_containing_aws_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Wrapped row with text containing an AWS key pattern → gate_passed=False,
        success=False (policy denial is the verdict).
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

        def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
            # Echo with an embedded AWS key -- egress regex AKIA[0-9A-Z]{16} should fire.
            return "The result is AKIAIOSFODNN7EXAMPLE which is the example key."

        from agentsla.bench.real_llm import run_real_llm_bench

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_fake_call):
            out_path = tmp_path / "deny.parquet"
            run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=out_path)

        rows = pq.read_table(out_path).to_pylist()
        wrapped = [r for r in rows if r["mode"] == "wrapped"]
        assert wrapped, "wrapped rows must exist"
        for r in wrapped:
            assert r["gate_passed"] is False, f"gate must deny AWS-key response; got row: {r}"
            assert r["success"] is False, "denied response cannot be a success"
        # Naked rows for the same tasks must still report gate_passed=False (no gate ran).
        naked = [r for r in rows if r["mode"] == "naked"]
        for r in naked:
            assert r["gate_passed"] is False, "naked row has no gate; gate_passed must be False"

    def test_gate_allows_clean_response(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Wrapped row with no egress-triggering pattern → gate_passed=True."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

        def _fake_call(prompt: str, *, model: str, api_key: str | None) -> str:
            return "The sum is 42."

        from agentsla.bench.real_llm import run_real_llm_bench

        with patch("agentsla.bench.real_llm._call_claude", side_effect=_fake_call):
            out_path = tmp_path / "allow.parquet"
            run_real_llm_bench(tasks_per_domain=1, seeds=1, out_path=out_path)

        rows = pq.read_table(out_path).to_pylist()
        wrapped = [r for r in rows if r["mode"] == "wrapped"]
        assert wrapped, "wrapped rows must exist"
        for r in wrapped:
            assert r["gate_passed"] is True, f"clean text must pass the gate; got row: {r}"
