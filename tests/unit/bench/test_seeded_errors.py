"""Seeded-error experiment tests.

Validates the verification gate's catch-rate against perturbed numeric
outputs (sensitivity) and clean outputs (specificity).

These tests do NOT run the full ``run_seeded_experiment`` loop (that
would take minutes); they cover the components and a small end-to-end
smoke run with minimal trials.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from agentsla.bench.seeded_errors import (
    SeededTask,
    SyntheticModel,
    _run_trial,
    _summarize,
    ground_truth_map,
    make_ground_truth_resolver,
    render_seeded_errors_section,
    run_seeded_experiment,
)
from agentsla.verify.claims import extract_numeric_claims

# ---------------------------------------------------------------------------
# SyntheticModel
# ---------------------------------------------------------------------------


def test_synthetic_model_zero_perturbation_returns_truth() -> None:
    model = SyntheticModel(ground_truth=42.0, perturbation_pct=0.0)
    assert model.complete(user_text="anything") == "The answer is 42.0000."


def test_synthetic_model_perturbed_stays_in_band() -> None:
    model = SyntheticModel(ground_truth=100.0, perturbation_pct=10.0, seed=1)
    # 10% jitter on 100 → claim should be in [90, 110].
    for _ in range(50):
        text = model.complete(user_text="x")
        claim = extract_numeric_claims(text)[0]
        assert 90.0 <= float(claim.value) <= 110.0, text


def test_synthetic_model_seeded_is_deterministic() -> None:
    a = SyntheticModel(ground_truth=100.0, perturbation_pct=25.0, seed=7).complete(user_text="x")
    b = SyntheticModel(ground_truth=100.0, perturbation_pct=25.0, seed=7).complete(user_text="x")
    assert a == b


# ---------------------------------------------------------------------------
# Ground-truth resolver
# ---------------------------------------------------------------------------


def test_ground_truth_resolver_returns_mapped_value() -> None:
    truth = {"t-1": 12.5, "t-2": 99.0}
    resolver = make_ground_truth_resolver(truth)

    class _FakeClaim:
        text = "12.5"
        value = 12.5

    class _FakeTrace:
        task_id = "t-1"

    assert resolver(_FakeClaim(), _FakeTrace()) == 12.5  # type: ignore[arg-type]


def test_ground_truth_resolver_returns_none_for_unknown_task() -> None:
    resolver = make_ground_truth_resolver({"known": 1.0})

    class _FakeTrace:
        task_id = "unknown"

    assert resolver(object(), _FakeTrace()) is None  # type: ignore[arg-type]


def test_ground_truth_map_has_twenty_tasks() -> None:
    truth = ground_truth_map()
    assert len(truth) == 20
    assert all(isinstance(v, float) for v in truth.values())


# ---------------------------------------------------------------------------
# Single-trial smoke (verifier integration)
# ---------------------------------------------------------------------------


def test_clean_trial_flags_verified(tmp_path: Path) -> None:
    """At 0% perturbation the gate must flag the claim as verified."""
    task = SeededTask("seeded-001", "Compute the value.", 1.0)
    row = _run_trial(
        task,
        strategy_pct=0.0,
        trial=0,
        truth=ground_truth_map(),
        db_path=tmp_path / "trial.duckdb",
    )
    assert row.status == "verified", row
    assert row.claim_value == pytest.approx(1.0, abs=1e-3)


def test_perturbed_trial_flags_incorrect(tmp_path: Path) -> None:
    """At 50% perturbation the gate must flag the claim as incorrect.

    Uses a deterministic seed that lands off-truth; jitter on 100 ±50%
    can occasionally land near 100 (uniform in [-1, 1]), so we sweep
    trials until we find an incorrect-flagged row (deterministic with
    a fixed seed in the inner RNG is also possible, but cheaper to
    sweep).
    """
    truth = ground_truth_map()
    for trial in range(20):
        task = SeededTask("seeded-005", "Compute the value.", 100.0)
        row = _run_trial(
            task,
            strategy_pct=50.0,
            trial=trial,
            truth=truth,
            db_path=tmp_path / "trial.duckdb",
        )
        if row.status == "incorrect":
            assert row.claim_value is not None
            assert abs(row.claim_value - 100.0) > 1e-3
            return
    pytest.fail("no perturbed trial flagged incorrect across 20 seeds")


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------


def test_summarize_zero_perturbation_reports_specificity() -> None:
    from agentsla.bench.seeded_errors import TrialRow

    rows = [
        TrialRow(0.0, 0, "t", 1.0, 1.0, "verified", 1.0),
        TrialRow(0.0, 1, "t", 2.0, 2.0, "verified", 1.0),
        TrialRow(0.0, 2, "t", 3.0, 3.0, "incorrect", 1.0),  # anomalous
    ]
    summary = _summarize(0.0, rows)
    assert summary.n_trials == 3
    assert summary.n_unperturbed == 3
    assert summary.n_clean_verified == 2
    assert summary.specificity == pytest.approx(2 / 3)


def test_summarize_nonzero_reports_sensitivity() -> None:
    from agentsla.bench.seeded_errors import TrialRow

    rows = [
        TrialRow(50.0, 0, "t", 1.0, 1.5, "incorrect", 1.0),
        TrialRow(50.0, 1, "t", 2.0, 2.4, "incorrect", 1.0),
        TrialRow(50.0, 2, "t", 3.0, 3.3, "verified", 1.0),  # gate missed
    ]
    summary = _summarize(50.0, rows)
    assert summary.n_trials == 3
    assert summary.n_caught == 2
    assert summary.sensitivity == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# End-to-end (small N)
# ---------------------------------------------------------------------------


def test_run_seeded_experiment_small_writes_parquet(tmp_path: Path) -> None:
    """End-to-end smoke: 2 strategies x 1 trial x 20 tasks = 40 rows."""
    out = tmp_path / "seeded.parquet"
    summaries = run_seeded_experiment(
        strategies=[0.0, 50.0],
        n_trials=1,
        out_path=out,
        db_path=tmp_path / "trial.duckdb",
    )
    assert out.exists()
    table = pq.read_table(out)
    assert table.num_rows == 40  # 2 strategies x 1 trial x 20 tasks
    # Per-strategy summaries.
    assert len(summaries) == 2
    baseline, perturbed = summaries
    assert baseline.strategy_pct == 0.0
    assert baseline.specificity >= 0.9  # clean → must verify
    assert perturbed.strategy_pct == 50.0
    assert perturbed.sensitivity >= 0.8  # perturbed → must catch most


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_seeded_errors_section_mentions_acceptance() -> None:
    summaries = run_seeded_experiment(
        strategies=[0.0, 50.0],
        n_trials=1,
        out_path=Path("/tmp/_unused_seeded.parquet"),
        db_path=Path("/tmp/_unused_seeded.duckdb"),
    )
    md = render_seeded_errors_section(summaries, source_parquet=Path("bench/results/seeded_errors.parquet"))
    assert "Seeded-error experiment" in md
    assert "Sensitivity" in md
    assert "Acceptance" in md
    assert "85%" in md


def test_report_appends_seeded_section_when_parquet_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``seeded_errors.parquet`` sits next to ``results.parquet``,
    ``agentsla report`` must append the seeded-error section.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from agentsla.bench import report as report_mod

    # Write a minimal main results parquet with one row.
    main_rows = [
        {
            "mode": "wrapped",
            "task_id": "t-1",
            "domain": "financial_ops",
            "seed": 0,
            "holdout": False,
            "has_injection": False,
            "success": True,
            "verified": True,
            "injection_resisted": True,
            "latency_ms": 5.0,
            "text": "<echo:t-1>",
        }
    ]
    pq.write_table(pa.Table.from_pylist(main_rows), tmp_path / "results.parquet")

    # Write a tiny seeded-errors parquet (2 strategies x 2 trials).
    seeded_rows = []
    for pct in (0.0, 50.0):
        for trial in range(2):
            seeded_rows.append(
                {
                    "strategy_pct": pct,
                    "trial": trial,
                    "task_id": "x",
                    "ground_truth": 1.0,
                    "claim_value": 1.0 if pct == 0.0 else 2.0,
                    "status": "verified" if pct == 0.0 else "incorrect",
                    "latency_ms": 1.0,
                }
            )
    pq.write_table(pa.Table.from_pylist(seeded_rows), tmp_path / "seeded_errors.parquet")

    out_md = tmp_path / "REPORT.md"
    rc = report_mod.main(["--in", str(tmp_path / "results.parquet"), "--out", str(out_md)])
    assert rc == 0
    text = out_md.read_text(encoding="utf-8")
    assert "Seeded-error experiment" in text
    assert "±0.0%" in text
    assert "±50.0%" in text
