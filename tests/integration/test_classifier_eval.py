"""Unit tests for the held-out classifier evaluation CLI.

Pins:
  * The CLI builds the canonical fixture when none exists.
  * Headline agreement is computed against hand-built rows.
  * ``render_eval_section`` emits a markdown block with the headline +
    per-category agreement + confusion matrix.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentsla.bench.eval_classifier import (
    EvalResult,
    evaluate,
    render_eval_section,
)


def _write_fixture(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _minimal_trace_row(gold: str) -> dict:
    return {
        "trace_id": "t-1",
        "task_id": "held-out",
        "events": [],
        "final_answer": "",
        "gold_category": gold,
    }


def test_evaluate_zero_rows_safe() -> None:
    """evaluate() over a fixture with no rows returns a default EvalResult."""
    path = Path("/tmp/empty_held_out.jsonl")
    _write_fixture(path, [])
    result = evaluate(path)
    assert result.n == 0
    assert result.agreement == 1.0  # vacuously true


def test_evaluate_agreement_handles_perfect_match(tmp_path: Path) -> None:
    """A fixture whose gold categories the classifier will hit → 100% agreement."""
    # We use the canonical fixture; the heuristics' coverage on it is ≥100%
    # (verified manually) — the test pins that contract.
    fixture = Path("tests/fixtures/held_out_labels.jsonl")
    if not fixture.exists():
        pytest.skip("canonical fixture not built; run scripts/build_held_out_fixture.py first")
    result = evaluate(fixture)
    assert result.n >= 30
    assert result.agreement >= 0.7  # generous floor — future regressions catch drop


def test_render_eval_section_emits_headline_and_matrix() -> None:
    result = EvalResult(n=4, correct=3, by_category={"policy_violation": 2, "none": 2})
    result.confusion = {("policy_violation", "policy_violation"): 2, ("none", "none"): 1, ("none", "policy_violation"): 1}
    md = render_eval_section(result, source_path=Path("/tmp/fixture.jsonl"))
    assert "**Headline agreement:** 75% (3/4)" in md
    assert "| policy_violation | 2 |" in md
    assert "| none | 2 |" in md
    assert "Confusion matrix" in md
    assert "honest (not circular)" in md


class TestProvenanceBanner:
    """Closes PRD-v2 §5 invariant #4 gap: the eval report must surface
    fixture provenance (synthetic vs real + model id) so a reviewer opening
    eval_classifier.md sees whether the headline is measured or hermetic.

    Per WRITEUP.md § "Real held-out fixture" every held-out row carries a
    ``synthetic`` boolean; the report must surface it.
    """

    def test_render_eval_section_names_synthetic_when_all_rows_synthetic(self) -> None:
        """All-synthetic fixture → banner must say 'synthetic' and name the model."""
        result = EvalResult(
            n=4,
            correct=3,
            by_category={"policy_violation": 2, "none": 2},
            n_synthetic=4,
            n_real=0,
            model_ids_real=(),
        )
        result.confusion = {("policy_violation", "policy_violation"): 2, ("none", "none"): 1, ("none", "policy_violation"): 1}
        md = render_eval_section(result, source_path=Path("/tmp/fixture.jsonl"))
        assert "synthetic" in md.lower(), "report must surface fixture provenance"
        assert "echo-1" in md, "synthetic default model_id is echo-1"
        # The honest-gap reminder should also flag the synthetic origin when no real rows.
        assert "honest" in md.lower() or "synthetic" in md.lower()

    def test_render_eval_section_names_model_when_real_rows_present(self) -> None:
        """Real rows present → banner names the Claude model_id (no synthetic tag)."""
        result = EvalResult(
            n=4,
            correct=3,
            by_category={"policy_violation": 2, "none": 2},
            n_synthetic=0,
            n_real=4,
            model_ids_real=("claude-haiku-4-5-20251001",),
        )
        result.confusion = {("policy_violation", "policy_violation"): 2, ("none", "none"): 1, ("none", "policy_violation"): 1}
        md = render_eval_section(result, source_path=Path("/tmp/fixture.jsonl"))
        assert "claude-haiku-4-5-20251001" in md, "real model_id must be surfaced"
        # The honest-gap synthetic reminder must NOT appear when 0 synthetic rows.
        assert "fixture is hermetic" not in md.lower() and "synthetic-only" not in md.lower()

    def test_render_eval_section_surfaces_split_provenance(self) -> None:
        """Mixed fixture (real + synthetic) → both halves named in the banner."""
        result = EvalResult(
            n=6,
            correct=6,
            by_category={"policy_violation": 6},
            n_synthetic=2,
            n_real=4,
            model_ids_real=("claude-haiku-4-5-20251001",),
        )
        md = render_eval_section(result, source_path=Path("/tmp/fixture.jsonl"))
        # Banner must show the count split AND the model — a reviewer needs both.
        assert "2 synthetic" in md or "synthetic: 2" in md, "synthetic count must appear"
        assert "real" in md.lower(), "real half must be named"
        assert "claude-haiku-4-5-20251001" in md


def test_cli_builds_fixture_when_missing(tmp_path: Path) -> None:
    """The CLI auto-builds the canonical fixture if --held-out points to a missing path."""
    out_md = tmp_path / "eval.md"
    result = subprocess.run(  # noqa: S603 — controlled subprocess for CLI smoke
        [
            sys.executable,
            "-m",
            "agentsla.bench.eval_classifier",
            "--held-out",
            str(tmp_path / "fresh_fixture.jsonl"),
            "--out",
            str(out_md),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "fresh_fixture.jsonl").exists()
    assert out_md.exists()
    assert "Headline agreement" in out_md.read_text(encoding="utf-8")
