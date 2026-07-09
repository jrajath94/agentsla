"""Seeded-error validation of the verification gate.

Per feedback.md Item 3 (HIGH): construct synthetic numeric tasks with
known ground truth; perturb the agent's emitted number by a controlled
percentage; verify the :class:`NumericVerifier` flags each perturbation
as ``incorrect`` (sensitivity) and leaves unperturbed outputs as
``verified`` (specificity).

This is a measurement of the **gate**, not the policy or classifier.
Default ``identity_source`` makes the verifier self-certifying — every
claim passes against itself. To validate the gate we inject a non-
identity ``ground_truth_resolver`` that maps ``trace.task_id`` to the
known correct value; the verifier then compares the extracted claim
against the resolver's value. A perturbed claim mismatches the ground
truth → status ``incorrect``; an unperturbed claim matches → ``verified``.

Output: parquet at ``bench/results/seeded_errors.parquet`` + a markdown
section appended to ``REPORT.md`` by ``agentsla report`` when the
parquet is present.

Reproduction::

    python -m agentsla bench-seeded-errors \\
        --strategies 0,10,50,100 --trials 100 \\
        --out bench/results/seeded_errors.parquet

Acceptance (per feedback.md):
    * sensitivity @ ±50% perturbation ≥ 0.85
    * specificity @ 0% perturbation ≥ 0.90
    * latency overhead vs naked baseline ≤ 0.15 (15%)
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.adapters.base import HookDecision, RuntimeHooks
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.core.events import ToolCall, ToolResult, Trace, Verdict
from agentsla.core.trace import TraceWriter
from agentsla.verify import NumericVerifier, VerificationChain
from agentsla.verify.claims import NumericClaim, extract_numeric_claims

# ---------------------------------------------------------------------------
# Synthetic model — deterministic, controlled numeric output
# ---------------------------------------------------------------------------


class SyntheticModel:
    """Returns ``f"The answer is {perturbed_value}."``.

    The single number is the only extractable claim, so the experiment
    has no ambiguity in what the verifier sees. The perturbation is
    ``ground_truth * (1 + pct/100 * uniform(-1, 1))`` drawn from a
    seeded :class:`random.Random` so trials are reproducible.
    """

    model_id = "synthetic-1"

    def __init__(self, *, ground_truth: float, perturbation_pct: float, seed: int = 0) -> None:
        self.ground_truth = float(ground_truth)
        self.perturbation_pct = float(perturbation_pct)
        self._rng = random.Random(seed)  # noqa: S311 — non-cryptographic seed for deterministic trials

    def complete(self, *, user_text: str) -> str:
        if self.perturbation_pct == 0.0:
            value = self.ground_truth
        else:
            jitter = self._rng.uniform(-1.0, 1.0) * (self.perturbation_pct / 100.0)
            value = self.ground_truth * (1.0 + jitter)
        # 4 decimal places keep small perturbations above the float
        # comparison tolerance when perturbation_pct is small.
        return f"The answer is {value:.4f}."


# ---------------------------------------------------------------------------
# Synthetic tasks + ground-truth resolver
# ---------------------------------------------------------------------------


@dataclass
class SeededTask:
    """A trivial numeric task with a known correct value."""

    task_id: str
    text: str
    ground_truth: float


# 20 tasks x 5 perturbation strategies x 100 trials ~= 10k rows.
# Each ground truth spans orders of magnitude (1.0 to 100,000) so the
# relative perturbation is comparable across scales.
_SEEDED_TASKS: list[SeededTask] = [
    SeededTask("seeded-001", "Compute the value.", 1.0),
    SeededTask("seeded-002", "Compute the value.", 2.5),
    SeededTask("seeded-003", "Compute the value.", 10.0),
    SeededTask("seeded-004", "Compute the value.", 42.0),
    SeededTask("seeded-005", "Compute the value.", 100.0),
    SeededTask("seeded-006", "Compute the value.", 250.0),
    SeededTask("seeded-007", "Compute the value.", 999.0),
    SeededTask("seeded-008", "Compute the value.", 1500.0),
    SeededTask("seeded-009", "Compute the value.", 4200.0),
    SeededTask("seeded-010", "Compute the value.", 9999.0),
    SeededTask("seeded-011", "Compute the value.", 12345.0),
    SeededTask("seeded-012", "Compute the value.", 50000.0),
    SeededTask("seeded-013", "Compute the value.", 100.0),
    SeededTask("seeded-014", "Compute the value.", 750.0),
    SeededTask("seeded-015", "Compute the value.", 3333.0),
    SeededTask("seeded-016", "Compute the value.", 88888.0),
    SeededTask("seeded-017", "Compute the value.", 17.5),
    SeededTask("seeded-018", "Compute the value.", 60.25),
    SeededTask("seeded-019", "Compute the value.", 314.15),
    SeededTask("seeded-020", "Compute the value.", 2718.28),
]


def ground_truth_map() -> dict[str, float]:
    """Return ``{task_id: ground_truth}`` for the seeded task corpus."""
    return {t.task_id: t.ground_truth for t in _SEEDED_TASKS}


def make_ground_truth_resolver(
    truth: dict[str, float],
) -> Callable[[NumericClaim, Any], Any | None]:
    """Build a resolver that returns the task's ground truth value.

    The closure looks up ``trace.task_id`` against ``truth`` so every
    claim extracted from the same trace resolves to the same value —
    i.e. the verifier compares the claim against the synthetic task's
    known correct answer, not against itself.
    """

    def _resolver(claim: NumericClaim, trace: Any) -> Any | None:
        if trace is None:
            return None
        task_id = getattr(trace, "task_id", None)
        if task_id is None:
            return None
        return truth.get(task_id)

    return _resolver


# ---------------------------------------------------------------------------
# Trial result + strategy aggregate
# ---------------------------------------------------------------------------


@dataclass
class TrialRow:
    strategy_pct: float
    trial: int
    task_id: str
    ground_truth: float
    claim_value: float | None
    status: str  # "verified" | "incorrect" | "unverified"
    latency_ms: float


@dataclass
class StrategySummary:
    strategy_pct: float
    n_trials: int
    n_perturbed: int
    n_caught: int
    sensitivity: float
    n_unperturbed: int
    n_clean_verified: int
    specificity: float
    mean_latency_ms: float


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------


def _run_trial(
    task: SeededTask,
    *,
    strategy_pct: float,
    trial: int,
    truth: dict[str, float],
    db_path: Path,
) -> TrialRow:
    """Run one (task, strategy, trial) triple.

    Builds a :class:`SyntheticModel` with the strategy's perturbation,
    wires the :class:`VerificationChain` with the ground-truth resolver,
    drives the adapter, and records the verdict on the extracted claim.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    writer = TraceWriter(db_path)
    try:
        # Per-trial seed mixes strategy + trial + task id for variance.
        trial_seed = (trial * 1000 + hash(task.task_id)) & 0xFFFFFFFF
        model = SyntheticModel(
            ground_truth=task.ground_truth,
            perturbation_pct=strategy_pct,
            seed=trial_seed,
        )
        resolver = make_ground_truth_resolver(truth)
        verifier = NumericVerifier(source_resolver=resolver, tolerance=1e-6)
        chain = VerificationChain(verifiers=[verifier])

        class _Capture(RuntimeHooks):
            """Minimal RuntimeHooks — only on_final_answer is needed."""

            captured_trace: Trace | None = None
            captured_result: object = None

            def on_tool_call(self, call: ToolCall) -> HookDecision:
                return HookDecision(allow=True)

            def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
                return None

            def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
                self.captured_trace = trace
                self.captured_result = chain.run(trace, trace.final_answer)

        capture = _Capture()
        # ``SyntheticModel`` is duck-compatible with ``EchoModel`` (both have
        # a ``complete(user_text=...)`` method); cast to satisfy the typed
        # adapter signature without re-declaring a Protocol.
        adapter = RawLoopAdapter(
            tools={},  # no tools → final assistant message is the SyntheticModel output directly
            trace_writer=writer,
            echo_model=model,  # type: ignore[arg-type]
            task_text=task.text,
        )
        t0 = time.perf_counter()
        final = adapter.run(task_id=task.task_id, hooks=capture)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        # Extract the claim from the final answer.
        claims = extract_numeric_claims(final.text)
        if not claims:
            return TrialRow(
                strategy_pct=strategy_pct,
                trial=trial,
                task_id=task.task_id,
                ground_truth=task.ground_truth,
                claim_value=None,
                status="unverified",
                latency_ms=latency_ms,
            )
        # Use the first numeric claim (SyntheticModel emits exactly one).
        first: NumericClaim = claims[0]
        # The captured result is the chain verdict — find matching status.
        result = getattr(capture, "captured_result", None)
        if result is None or not result.claims:
            return TrialRow(
                strategy_pct=strategy_pct,
                trial=trial,
                task_id=task.task_id,
                ground_truth=task.ground_truth,
                claim_value=float(first.value),
                status="unverified",
                latency_ms=latency_ms,
            )
        status = result.claims[0].status
        return TrialRow(
            strategy_pct=strategy_pct,
            trial=trial,
            task_id=task.task_id,
            ground_truth=task.ground_truth,
            claim_value=float(first.value),
            status=status,
            latency_ms=latency_ms,
        )
    finally:
        writer.close()


def run_seeded_experiment(
    *,
    strategies: list[float],
    n_trials: int,
    out_path: Path,
    db_path: Path | None = None,
) -> list[StrategySummary]:
    """Run the full experiment and write a parquet of trial rows.

    Args:
        strategies: List of perturbation percentages (0 = baseline).
        n_trials: Trials per (strategy, task). Total rows = ``len(strategies) * n_trials * 20``.
        out_path: Where to write the parquet.
        db_path: Per-trial DuckDB path (defaults to ``.agentsla/seeded_errors.duckdb``).

    Returns:
        Per-strategy summaries (sensitivity, specificity, latency).
    """
    db_path = db_path or Path(".agentsla/seeded_errors.duckdb")
    truth = ground_truth_map()
    rows: list[TrialRow] = []

    for pct in strategies:
        print(f"\n=== Strategy: ±{pct:.1f}% perturbation ===", flush=True)
        for trial in range(n_trials):
            for task in _SEEDED_TASKS:
                row = _run_trial(
                    task,
                    strategy_pct=pct,
                    trial=trial,
                    truth=truth,
                    db_path=db_path,
                )
                rows.append(row)
        print(f"  collected {sum(1 for r in rows if r.strategy_pct == pct)} rows", flush=True)

    # Persist parquet.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([asdict(r) for r in rows])
    pq.write_table(table, out_path)  # type: ignore[no-untyped-call]
    print(f"\nWrote {len(rows)} trial rows to {out_path}")

    # Aggregate.
    summaries = [_summarize(pct, rows) for pct in strategies]
    return summaries


def _summarize(pct: float, rows: list[TrialRow]) -> StrategySummary:
    by_pct = [r for r in rows if r.strategy_pct == pct]
    n = len(by_pct)
    if n == 0:
        return StrategySummary(
            strategy_pct=pct,
            n_trials=0,
            n_perturbed=0,
            n_caught=0,
            sensitivity=0.0,
            n_unperturbed=0,
            n_clean_verified=0,
            specificity=0.0,
            mean_latency_ms=0.0,
        )
    if pct == 0.0:
        # Baseline: every claim should be verified.
        verified = [r for r in by_pct if r.status == "verified"]
        return StrategySummary(
            strategy_pct=pct,
            n_trials=n,
            n_perturbed=0,
            n_caught=0,
            sensitivity=1.0,  # vacuous; nothing to catch
            n_unperturbed=n,
            n_clean_verified=len(verified),
            specificity=len(verified) / n if n else 1.0,
            mean_latency_ms=sum(r.latency_ms for r in by_pct) / n,
        )
    perturbed = [r for r in by_pct if r.status == "incorrect"]
    return StrategySummary(
        strategy_pct=pct,
        n_trials=n,
        n_perturbed=n,
        n_caught=len(perturbed),
        sensitivity=len(perturbed) / n if n else 0.0,
        n_unperturbed=0,
        n_clean_verified=0,
        specificity=0.0,  # not applicable
        mean_latency_ms=sum(r.latency_ms for r in by_pct) / n,
    )


# ---------------------------------------------------------------------------
# Markdown emission (for REPORT.md integration)
# ---------------------------------------------------------------------------


def summaries_to_parquet_rows(summaries: list[StrategySummary]) -> list[dict[str, Any]]:
    """Convert summaries into the row dicts we want in the markdown table."""
    return [
        {
            "strategy_pct": s.strategy_pct,
            "n_trials": s.n_trials,
            "sensitivity": s.sensitivity,
            "specificity": s.specificity,
            "mean_latency_ms": s.mean_latency_ms,
        }
        for s in summaries
    ]


def render_seeded_errors_section(
    summaries: list[StrategySummary],
    *,
    source_parquet: Path,
) -> str:
    """Render the markdown section appended to REPORT.md."""
    md = "## Seeded-error experiment (verification gate validation)\n\n"
    md += f"_Generated from `{source_parquet}`. "
    md += "Synthetic numeric tasks with known ground truth; the agent emits a single "
    md += "perturbed number; the verifier compares the extracted claim against the "
    md += "ground-truth resolver. At 0% perturbation every claim should match (specificity); "
    md += "at >0% perturbation every claim should mismatch and the gate should flag "
    md += "`incorrect` (sensitivity)._\n\n"
    md += "| Perturbation | N trials | Sensitivity (gate caught) "
    md += "| Specificity (clean pass) | Mean latency (ms) |\n"
    md += "|-------------:|---------:|--------------------------:"
    md += "|-------------------------:|------------------:|\n"
    for s in summaries:
        md += f"| ±{s.strategy_pct:.1f}% | {s.n_trials} | {s.sensitivity:.0%} | {s.specificity:.0%} | {s.mean_latency_ms:.2f} |\n"
    md += "\n**Acceptance** (per `feedback.md` Item 3):\n"
    md += "- sensitivity @ ±50% perturbation ≥ 85%\n"
    md += "- specificity @ 0% perturbation ≥ 90%\n"
    return md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentsla-bench-seeded-errors",
        description="Seeded-error validation of the verification gate.",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        default="0,10,50,100",
        help="Comma-separated perturbation percentages (default: 0,10,50,100).",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=100,
        help="Trials per (strategy, task) (default: 100).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("bench/results/seeded_errors.parquet"),
        help="Output parquet path.",
    )
    parser.add_argument(
        "--report-section-out",
        type=Path,
        default=None,
        help="Optional path to write the markdown section (for diff inspection).",
    )
    args = parser.parse_args(argv)

    strategies = [float(s) for s in args.strategies.split(",") if s.strip()]
    summaries = run_seeded_experiment(
        strategies=strategies,
        n_trials=args.trials,
        out_path=args.out,
    )

    print("\nSummary:")
    for s in summaries:
        print(f"  ±{s.strategy_pct:5.1f}%  n={s.n_trials}  sens={s.sensitivity:.0%}  spec={s.specificity:.0%}  mean_lat={s.mean_latency_ms:.2f}ms")

    section_md = render_seeded_errors_section(summaries, source_parquet=args.out)
    if args.report_section_out:
        args.report_section_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_section_out.write_text(section_md, encoding="utf-8")
        print(f"\nWrote report section to {args.report_section_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
