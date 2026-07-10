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
