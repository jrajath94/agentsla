"""Real-LLM bench — runs tasks through real Claude API, captures traces,
measures gate accuracy.

The hermetic ``EchoModel`` self-certifies: every numeric token in a
task is echoed as the "final answer," so headline numbers (gate_passed,
verified_at_truth) are *structural*, not empirical. This harness answers
"does it work on a real agent?" by hitting the actual Claude API.

Two modes are emitted per (task, seed):

  * **naked** — raw Claude response. No gate. ``gate_passed=False`` by
    convention (no gate ran).
  * **wrapped** — same Claude response, routed through ``PolicyGate``
    via a synthetic ``ToolCall(tool="response_text", args={"text": resp})``.
    Egress regex scan (SSN, AWS key, JWT, PAN) decides ``gate_passed``.
    If the gate denies, the response is recorded but ``success`` is
    forced to ``False`` (denial IS the policy verdict).

Usage::

    ANTHROPIC_API_KEY=sk-... \\
        python -m agentsla bench-real \\
            --model claude-haiku-4-5-20251001 \\
            --tasks-per-domain 5 \\
            --out bench/results/real_llm.parquet

Or with the Anthropic-compatible gateway (e.g. MiniMax M3)::

    ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic \\
    ANTHROPIC_AUTH_TOKEN=sk-cp-... \\
    ANTHROPIC_API_KEY=sk-cp-... \\   # the harness reads this name
        python -m agentsla bench-real \\
            --model MiniMax-M3 \\
            --tasks-per-domain 5

The harness reads ``ANTHROPIC_API_KEY`` (then falls back to
``ANTHROPIC_AUTH_TOKEN``); the SDK reads ``ANTHROPIC_BASE_URL``
automatically. Without a key the harness fails fast (exit 2) with
a clear message naming the variable. No parquet is written.
Honest gap (PRD-v1 § 2.1 F3): the substring ``verified_at_truth``
column requires task fixtures to declare ``ground_truth``; rows
where the fixture doesn't carry ground truth remain ``None``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.bench.tasks import load_ground_truthable_tasks
from agentsla.core.events import ToolCall
from agentsla.policy import Policy, PolicyGate, default_egress_rules

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


def _resolve_api_key(explicit: str | None) -> str:
    """Resolve the Claude API key: explicit arg > ANTHROPIC_API_KEY > ANTHROPIC_AUTH_TOKEN."""
    if explicit:
        return explicit
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        val = os.environ.get(var)
        if val:
            return val
    raise RuntimeError("ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) required for real-LLM bench (or pass api_key=...)")


def _call_claude(prompt: str, *, model: str, api_key: str | None) -> str:
    """Call Claude. Returns assistant text. No network unless key + SDK present."""
    effective_key = _resolve_api_key(api_key)
    if not _HAS_ANTHROPIC:
        raise RuntimeError("anthropic package not installed; pip install anthropic to run live")
    client = anthropic.Anthropic(api_key=effective_key)
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text  # type: ignore[union-attr]


def _gate_response(text: str, *, gate: PolicyGate, trace_id: uuid.UUID, seq: int) -> bool:
    """Route a free-text response through PolicyGate via a synthetic ToolCall.

    The gate has no free-text entry point; we wrap ``text`` in
    ``ToolCall(tool="response_text", args={"text": text})`` so the egress
    regex scan (``_scan_egress``) runs against the response. Returns True
    iff the gate allows (no egress match + allowed_tools membership).
    """
    call = ToolCall(
        call_id=uuid.uuid4(),
        tool="response_text",
        args={"text": text},
        trace_id=trace_id,
        seq=seq,
    )
    decision = gate.on_tool_call(call)
    return decision.allow


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

    Emits BOTH ``naked`` and ``wrapped`` rows per (task, seed) — same
    Claude response, gate runs only on the wrapped path.
    """
    # Fail fast with a clear message if no key resolves.
    _resolve_api_key(api_key)

    # Slice: tasks_per_domain per of 3 domains. We use the
    # ground-truthable corpus (arithmetic / capitals / short facts) so the
    # live model's free-form answers can be substring-matched against
    # ``ground_truth`` and the report's ``verified_at_truth`` column carries
    # measured numbers instead of `n/a`. The hermetic corpus's ``<echo:``
    # marker would zero-out success_rate against any real model.
    tasks = load_ground_truthable_tasks()[: tasks_per_domain * 3]

    # Build the policy + gate once; reused for every wrapped row.
    policy = Policy(allowed_tools=["response_text"], egress_rules=default_egress_rules())
    gate = PolicyGate(policy)

    rows: list[RealLlmRow] = []
    for task in tasks:
        for seed in range(seeds):
            t0 = time.perf_counter()
            try:
                text = _call_claude(task.text, model=model, api_key=api_key)
            except Exception as exc:
                # Both modes carry the error note — naked and wrapped fail equally.
                err_note = f"[NOT YET MEASURED] {exc}"
                for mode in ("naked", "wrapped"):
                    rows.append(
                        RealLlmRow(
                            mode=mode,
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
                            note=err_note,
                        )
                    )
                continue
            latency_ms = (time.perf_counter() - t0) * 1000.0
            success = task.expected_substring in text
            verified_at_truth: bool | None = None
            if task.ground_truth is not None:
                verified_at_truth = task.ground_truth in text

            # Naked row: no gate.
            rows.append(
                RealLlmRow(
                    mode="naked",
                    task_id=task.task_id,
                    domain=task.domain,
                    model_id=model,
                    seed=seed,
                    success=success,
                    gate_passed=False,  # naked = no gate ran
                    verified_at_truth=verified_at_truth,
                    sensitivity=None,
                    specificity=None,
                    latency_ms=latency_ms,
                    text=text,
                )
            )

            # Wrapped row: route text through PolicyGate via synthetic ToolCall.
            trace_id = uuid.uuid4()
            gate_passed = _gate_response(text, gate=gate, trace_id=trace_id, seq=0)
            rows.append(
                RealLlmRow(
                    mode="wrapped",
                    task_id=task.task_id,
                    domain=task.domain,
                    model_id=model,
                    seed=seed,
                    success=success and gate_passed,  # denial = policy verdict = not success
                    gate_passed=gate_passed,
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
        description=("Run the real-LLM bench against Claude. Requires ANTHROPIC_API_KEY env var or --api-key."),
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
