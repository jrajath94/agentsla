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

Cost guards (paid calls must be impossible by accident):

  * ``--dry-plan`` — print the run plan (model, tasks, prompts, rows,
    cache hits, estimated live calls, output path) and exit. Zero
    network access; no API key required.
  * ``--max-paid-calls`` — default 3. The run refuses to start if the
    number of *uncached* prompts exceeds this cap. Raise it explicitly
    for larger runs (see docs/GPU_API_COST_OPTIMIZATION.md rungs C-E).
  * ``--cache-dir`` — raw model responses are cached keyed by
    ``sha256(model_id, task_id, prompt, seed)``. Live calls always
    populate the cache.
  * ``--resume`` — serve cached responses instead of re-calling the
    API. Cached rows are marked ``cached=True`` in the parquet.
  * ``--overwrite`` — required when the output parquet already exists;
    the harness refuses to clobber a prior artifact otherwise.
  * fail-fast — the run stops after the first API/provider error
    (partial parquet is kept, error rows tagged ``[NOT YET MEASURED]``).
    Pass ``--no-fail-fast`` to record error rows and keep going.

Frugal usage (Rung C smoke — 3 prompts, 6 rows)::

    ANTHROPIC_API_KEY=sk-... \\
        python -m agentsla bench-real \\
            --model claude-haiku-4-5-20251001 \\
            --tasks-per-domain 1 \\
            --seeds 1 \\
            --out bench/results/real_llm_smoke.parquet

Or with the Anthropic-compatible gateway (e.g. MiniMax M3)::

    ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic \\
    ANTHROPIC_AUTH_TOKEN=sk-cp-... \\
    ANTHROPIC_API_KEY=sk-cp-... \\   # the harness reads this name
        python -m agentsla bench-real \\
            --model MiniMax-M3 \\
            --tasks-per-domain 1

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
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.bench.tasks import BenchTask, load_ground_truthable_tasks
from agentsla.core.events import ToolCall
from agentsla.policy import Policy, PolicyGate, default_egress_rules

# The anthropic SDK is optional — keeps the core install hermetic.
try:
    import anthropic  # type: ignore[import-untyped]

    _HAS_ANTHROPIC = True
except ImportError:  # pragma: no cover
    _HAS_ANTHROPIC = False


DEFAULT_CACHE_DIR = Path("bench/cache/real_llm")
DEFAULT_MAX_PAID_CALLS = 3


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
    cached: bool = False  # True when the response was served from --cache-dir


@dataclass
class DryPlan:
    """What a ``bench-real`` invocation would do — computed without network."""

    model: str
    n_tasks: int
    n_prompts: int  # tasks * seeds = live-call upper bound
    n_rows: int  # 2 * n_prompts (naked + wrapped per response)
    n_cached: int  # prompts already answered in cache_dir
    n_live_calls: int  # prompts that would hit the paid API
    out_path: Path
    out_exists: bool

    def render(self) -> str:
        lines = [
            "bench-real dry plan (no network access performed):",
            f"  model:            {self.model}",
            f"  tasks:            {self.n_tasks}",
            f"  prompts:          {self.n_prompts}",
            f"  rows to write:    {self.n_rows}",
            f"  cached responses: {self.n_cached}",
            f"  estimated PAID API calls: {self.n_live_calls}",
            f"  output:           {self.out_path}" + (" [EXISTS — requires --overwrite]" if self.out_exists else " [new file]"),
        ]
        return "\n".join(lines)


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


# ---------------------------------------------------------------------------
# Task selection + response cache
# ---------------------------------------------------------------------------


def _select_tasks(tasks_per_domain: int) -> list[BenchTask]:
    """Stratified selection: first ``tasks_per_domain`` tasks of EACH domain.

    The naive ``corpus[: tasks_per_domain * 3]`` slice is wrong because the
    corpus is grouped by domain — a smoke run (``tasks_per_domain=1``) would
    grab three financial_ops tasks and zero incident/doc rows, violating the
    Rung C acceptance criterion "all three domains represented"
    (docs/GPU_API_COST_OPTIMIZATION.md).
    """
    by_domain: dict[str, list[BenchTask]] = {}
    for t in load_ground_truthable_tasks():
        by_domain.setdefault(t.domain, []).append(t)
    return [t for domain_tasks in by_domain.values() for t in domain_tasks[:tasks_per_domain]]


def _cache_key(*, model: str, task_id: str, prompt: str, seed: int) -> str:
    """Deterministic response-cache key: sha256(model, task_id, prompt, seed).

    Changing model, prompt text, or seed changes the key (invalidation);
    changing policy/verifier/report code does not (cached text is reusable).
    """
    material = "\x00".join([model, task_id, prompt, str(seed)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cache_read(cache_dir: Path, key: str) -> dict | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "text" not in payload:
        return None
    return payload


def _cache_write(cache_dir: Path, key: str, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def plan_real_llm_bench(
    *,
    model: str = "claude-haiku-4-5-20251001",
    tasks_per_domain: int = 1,
    seeds: int = 1,
    out_path: Path = Path("bench/results/real_llm.parquet"),
    cache_dir: Path | None = None,
    resume: bool = False,
) -> DryPlan:
    """Compute the run plan. Pure function of local state — zero network,
    no API key required. Backs ``--dry-plan`` and the ``--max-paid-calls``
    guard so both count paid calls identically.
    """
    tasks = _select_tasks(tasks_per_domain)
    n_prompts = len(tasks) * seeds
    n_cached = 0
    if resume and cache_dir is not None:
        for task in tasks:
            for seed in range(seeds):
                key = _cache_key(model=model, task_id=task.task_id, prompt=task.text, seed=seed)
                if _cache_read(cache_dir, key) is not None:
                    n_cached += 1
    return DryPlan(
        model=model,
        n_tasks=len(tasks),
        n_prompts=n_prompts,
        n_rows=2 * n_prompts,
        n_cached=n_cached,
        n_live_calls=n_prompts - n_cached,
        out_path=out_path,
        out_exists=out_path.exists(),
    )


def run_real_llm_bench(
    *,
    model: str = "claude-haiku-4-5-20251001",
    tasks_per_domain: int = 1,  # Rung C smoke by default — larger runs must be explicit
    seeds: int = 1,
    api_key: str | None = None,
    out_path: Path = Path("bench/results/real_llm.parquet"),
    cache_dir: Path | None = None,
    resume: bool = False,
    overwrite: bool = False,
    max_paid_calls: int | None = None,
    fail_fast: bool = True,
) -> list[RealLlmRow]:
    """Run tasks through real Claude API. Captures traces; runs gate.

    Returns the list of :class:`RealLlmRow` records (also persisted to
    ``out_path`` as parquet). Errors from ``_call_claude`` become rows
    with ``success=False`` and ``note="[NOT YET MEASURED] ..."`` so the
    parquet is honest when the API rate-limits mid-run. With
    ``fail_fast=True`` (default) the run stops after the first API error
    and keeps the partial artifact instead of burning more paid calls.

    Cost guards (all enforced BEFORE any network call):

      * ``out_path`` exists and ``overwrite=False`` → :class:`RuntimeError`.
      * planned uncached prompts > ``max_paid_calls`` → :class:`RuntimeError`.

    Emits BOTH ``naked`` and ``wrapped`` rows per (task, seed) — same
    Claude response, gate runs only on the wrapped path.
    """
    plan = plan_real_llm_bench(
        model=model,
        tasks_per_domain=tasks_per_domain,
        seeds=seeds,
        out_path=out_path,
        cache_dir=cache_dir,
        resume=resume,
    )

    # Guard 1: never clobber a prior benchmark artifact silently.
    if plan.out_exists and not overwrite:
        raise RuntimeError(f"output already exists: {out_path} — pass --overwrite to replace it (prior benchmark artifacts are protected by default)")

    # Guard 2: refuse accidental large paid runs.
    if max_paid_calls is not None and plan.n_live_calls > max_paid_calls:
        raise RuntimeError(
            f"planned paid API calls ({plan.n_live_calls}) exceed --max-paid-calls ({max_paid_calls}). "
            f"Raise the cap explicitly for larger runs; see docs/GPU_API_COST_OPTIMIZATION.md "
            f"(Rung C=3 prompts, Rung D=9, Rung E=15). Use --dry-plan to preview."
        )

    # Guard 3: fail fast with a clear message if no key resolves — but only
    # when the run actually needs the network (a fully cached resume run
    # must work offline).
    if plan.n_live_calls > 0:
        _resolve_api_key(api_key)

    tasks = _select_tasks(tasks_per_domain)

    # Build the policy + gate once; reused for every wrapped row.
    policy = Policy(allowed_tools=["response_text"], egress_rules=default_egress_rules())
    gate = PolicyGate(policy)

    rows: list[RealLlmRow] = []
    aborted = False
    for task in tasks:
        if aborted:
            break
        for seed in range(seeds):
            key = _cache_key(model=model, task_id=task.task_id, prompt=task.text, seed=seed)
            cached_payload = _cache_read(cache_dir, key) if (resume and cache_dir is not None) else None
            if cached_payload is not None:
                text = str(cached_payload["text"])
                latency_ms = float(cached_payload.get("latency_ms", 0.0))
                from_cache = True
            else:
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
                    if fail_fast:
                        # Stop burning paid calls; keep the honest partial artifact.
                        aborted = True
                        break
                    continue
                latency_ms = (time.perf_counter() - t0) * 1000.0
                from_cache = False
                if cache_dir is not None:
                    _cache_write(
                        cache_dir,
                        key,
                        {
                            "model_id": model,
                            "task_id": task.task_id,
                            "seed": seed,
                            "prompt_sha256": hashlib.sha256(task.text.encode("utf-8")).hexdigest(),
                            "text": text,
                            "latency_ms": latency_ms,
                        },
                    )
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
                    cached=from_cache,
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
                    cached=from_cache,
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
            "Run the real-LLM bench against Claude. Requires ANTHROPIC_API_KEY env var or --api-key. "
            "Paid-call guards: --dry-plan previews the run; --max-paid-calls (default 3) blocks "
            "accidental large runs; --cache-dir + --resume reuse prior responses. "
            "See docs/GPU_API_COST_OPTIMIZATION.md for the frugal run ladder."
        ),
    )
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument(
        "--tasks-per-domain",
        type=int,
        default=1,
        help="Tasks per domain (default 1 = Rung C smoke: 3 prompts, fits --max-paid-calls 3). Raise alongside --max-paid-calls for Rung D/E.",
    )
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--out", type=Path, default=Path("bench/results/real_llm.parquet"))
    parser.add_argument(
        "--dry-plan",
        action="store_true",
        help="Print the run plan (tasks, prompts, rows, cache hits, estimated paid calls) and exit. No network, no API key needed.",
    )
    parser.add_argument(
        "--max-paid-calls",
        type=int,
        default=DEFAULT_MAX_PAID_CALLS,
        help=f"Refuse to start if uncached prompts exceed this cap (default {DEFAULT_MAX_PAID_CALLS}). Raise explicitly for Rung D/E runs.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Response cache directory (default {DEFAULT_CACHE_DIR}). Live calls always populate it; --resume reads from it.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Serve cached responses from --cache-dir instead of re-calling the API. Cached rows are marked cached=True.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Required when --out already exists. Prior benchmark artifacts are protected by default.",
    )
    parser.add_argument(
        "--no-fail-fast",
        action="store_true",
        help="Keep calling the API after the first provider error (default: stop after the first error and keep the partial parquet).",
    )
    args = parser.parse_args(argv)

    if args.dry_plan:
        plan = plan_real_llm_bench(
            model=args.model,
            tasks_per_domain=args.tasks_per_domain,
            seeds=args.seeds,
            out_path=args.out,
            cache_dir=args.cache_dir,
            resume=args.resume,
        )
        print(plan.render())
        if plan.n_live_calls > args.max_paid_calls:
            print(
                f"NOTE: {plan.n_live_calls} paid calls exceed --max-paid-calls={args.max_paid_calls}; the live run would refuse to start.",
            )
        return 0

    try:
        rows = run_real_llm_bench(
            model=args.model,
            tasks_per_domain=args.tasks_per_domain,
            seeds=args.seeds,
            api_key=args.api_key,
            out_path=args.out,
            cache_dir=args.cache_dir,
            resume=args.resume,
            overwrite=args.overwrite,
            max_paid_calls=args.max_paid_calls,
            fail_fast=not args.no_fail_fast,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    n_ok = sum(1 for r in rows if r.success)
    n_total = len(rows)
    n_cached = sum(1 for r in rows if r.cached)
    n_unmeasured = sum(1 for r in rows if r.note.startswith("[NOT YET MEASURED]"))
    print(f"Wrote {n_total} rows to {args.out}")
    print(f"  success: {n_ok}/{n_total}")
    if n_cached:
        print(f"  cached rows (no paid call): {n_cached}/{n_total}")
    if n_unmeasured:
        print(f"  [NOT YET MEASURED] rows: {n_unmeasured}/{n_total}")
        if not args.no_fail_fast:
            print("  fail-fast stopped the run after the first API error; partial parquet kept.", file=sys.stderr)
            return 1
    return 0


__all__ = [
    "DEFAULT_CACHE_DIR",
    "DEFAULT_MAX_PAID_CALLS",
    "DryPlan",
    "RealLlmRow",
    "main",
    "plan_real_llm_bench",
    "run_real_llm_bench",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
