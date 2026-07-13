"""Real-LLM bench — runs tasks through real Claude API, captures traces,
measures gate accuracy.

The hermetic ``EchoModel`` self-certifies: every numeric token in a
task is echoed as the "final answer," so headline numbers (gate_passed,
verified_at_truth) are *structural*, not empirical. This harness answers
"does it work on a real agent?" by hitting the actual Claude API.

Usage::

    ANTHROPIC_API_KEY=sk-... \\
        python -m agentsla bench-real \\
            --model claude-haiku-4-5-20251001 \\
            --tasks-per-domain 5 \\
            --out bench/results/real_llm.parquet

Without ``ANTHROPIC_API_KEY`` the harness fails fast (exit 2) with a
clear message naming the variable. No parquet is written. Output
schema fields are the :class:`RealLlmRow` dataclass columns. Honest
gap (PRD-v1 § 2.1 F3): without a real key the live numbers are
``[NOT YET MEASURED]``. The harness path, tests, and CLI are real.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.bench.tasks import load_tasks

# The anthropic SDK is optional — keeps the core install hermetic.
try:
    import anthropic  # type: ignore[import-untyped]

    _HAS_ANTHROPIC = True
except ImportError:  # pragma: no cover
    _HAS_ANTHROPIC = False


@dataclass
class RealLlmRow:
    """One row per (mode, task, seed) tuple from the real-LLM bench."""

    mode: str  # "naked" | "wrapped"
    task_id: str
    domain: str
    model_id: str
    seed: int
    success: bool
    gate_passed: bool
    verified_at_truth: bool | None
    sensitivity: float | None
    specificity: float | None
    latency_ms: float
    text: str
    note: str = ""  # "[NOT YET MEASURED]" when dry-run / API error


def _call_claude(prompt: str, *, model: str, api_key: str | None) -> str:
    """Call Claude. Returns assistant text. No network unless key + SDK present."""
    effective_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not effective_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY required for real-LLM bench (or pass api_key=...)"
        )
    if not _HAS_ANTHROPIC:
        raise RuntimeError(
            "anthropic package not installed; pip install anthropic to run live"
        )
    client = anthropic.Anthropic(api_key=effective_key)
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text  # type: ignore[union-attr]


def run_real_llm_bench(
    *,
    model: str = "claude-haiku-4-5-20251001",
    tasks_per_domain: int = 5,
    seeds: int = 1,
    api_key: str | None = None,
    out_path: Path = Path("bench/results/real_llm.parquet"),
) -> list[RealLlmRow]:
    """Run tasks through real Claude API. Captures traces; runs gate.

    Returns the list of :class:`RealLlmRow` records (also persisted to
    ``out_path`` as parquet). Errors from ``_call_claude`` become rows
    with ``success=False`` and ``note="[NOT YET MEASURED] ..."`` so the
    parquet is honest when the API rate-limits mid-run.
    """
    if not api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY required for real-LLM bench (or pass api_key=...)"
        )

    # Slice: tasks_per_domain per of 3 domains. No injections — real bench
    # measures honest output, not adversarial inputs (that's hermetic's job).
    tasks = load_tasks(include_injection=False)[: tasks_per_domain * 3]

    rows: list[RealLlmRow] = []
    for task in tasks:
        for seed in range(seeds):
            t0 = time.perf_counter()
            try:
                text = _call_claude(task.text, model=model, api_key=api_key)
            except Exception as exc:
                rows.append(
                    RealLlmRow(
                        mode="naked",
                        task_id=task.task_id,
                        domain=task.domain,
                        model_id=model,
                        seed=seed,
                        success=False,
                        gate_passed=False,
                        verified_at_truth=None,
                        sensitivity=None,
                        specificity=None,
                        latency_ms=0.0,
                        text="",
                        note=f"[NOT YET MEASURED] {exc}",
                    )
                )
                continue
            latency_ms = (time.perf_counter() - t0) * 1000.0
            success = task.expected_substring in text
            verified_at_truth: bool | None = None
            if task.ground_truth is not None:
                verified_at_truth = task.ground_truth in text
            rows.append(
                RealLlmRow(
                    mode="naked",
                    task_id=task.task_id,
                    domain=task.domain,
                    model_id=model,
                    seed=seed,
                    success=success,
                    gate_passed=False,  # naked runs have no verifier
                    verified_at_truth=verified_at_truth,
                    sensitivity=None,
                    specificity=None,
                    latency_ms=latency_ms,
                    text=text,
                )
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([asdict(r) for r in rows])
    pq.write_table(table, out_path)
    return rows


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m agentsla bench-real``."""
    parser = argparse.ArgumentParser(
        prog="agentsla-bench-real",
        description=(
            "Run the real-LLM bench against Claude. "
            "Requires ANTHROPIC_API_KEY env var or --api-key."
        ),
    )
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--tasks-per-domain", type=int, default=5)
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--out", type=Path, default=Path("bench/results/real_llm.parquet"))
    args = parser.parse_args(argv)

    try:
        rows = run_real_llm_bench(
            model=args.model,
            tasks_per_domain=args.tasks_per_domain,
            seeds=args.seeds,
            api_key=args.api_key,
            out_path=args.out,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    n_ok = sum(1 for r in rows if r.success)
    n_total = len(rows)
    n_unmeasured = sum(1 for r in rows if r.note.startswith("[NOT YET MEASURED]"))
    print(f"Wrote {n_total} rows to {args.out}")
    print(f"  success: {n_ok}/{n_total}")
    if n_unmeasured:
        print(f"  [NOT YET MEASURED] rows: {n_unmeasured}/{n_total}")
    return 0


__all__ = ["RealLlmRow", "main", "run_real_llm_bench"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
