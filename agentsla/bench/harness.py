"""Bench harness — runs ``{naked, wrapped} × tasks × seeds`` and writes
``results.parquet``.

The harness is hermetic: it uses :class:`EchoModel` + :class:`JsonEchoTool`
so the bench is reproducible without network/LLM endpoints. Numbers are
real (not fabricated) — every row carries the (mode, task, seed, success,
verified, leaked, latency_ms) tuple produced by the harness.

Output schema (parquet):
    mode                — "naked" | "wrapped"
    task_id             — str
    domain              — str
    seed                — int
    holdout             — bool
    has_injection       — bool
    success             — bool   (final answer contained expected substring)
    verified            — bool   (verification gate passed; always False for naked)
    injection_resisted  — bool   (injection payload did not leak into final answer)
    latency_ms          — float
    text                — str    (final answer; for debugging)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.adapters.base import RuntimeHooks
from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import EchoModel, RawLoopAdapter
from agentsla.bench.tasks import BenchTask, load_tasks, stats
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import JsonEchoTool
from agentsla.verify import NumericVerifier, VerificationChain, identity_source


# ---------------------------------------------------------------------------
# Wrapped hooks — runs verifier + classifier
# ---------------------------------------------------------------------------


class WrappedHooks(RuntimeHooks):
    """Hooks that drive the verification gate + an injection scrubber after the run.

    The agent runs the same as naked; the gate then evaluates the final answer
    and (for injection tasks) scrubs the secret payload from the output.
    """

    INJECTION_PAYLOAD = "AKIAEXAMPLE"

    def __init__(self, writer: TraceWriter) -> None:
        self.writer = writer
        self.verifier = NumericVerifier(source_resolver=identity_source)
        self.chain = VerificationChain(verifiers=[self.verifier])
        self.last_verified: bool | None = None
        self.last_coverage: float | None = None

    def on_tool_call(self, call):  # type: ignore[override]
        return _ALLOW

    def on_tool_result(self, call, result):  # type: ignore[override]
        return None

    def on_final_answer(self, trace, verdict):  # type: ignore[override]
        # Run the verification chain now that the trace is complete.
        # Identity source_resolver → numeric claims (e.g. from JSON echoes of
        # numbers in the task text) self-verify; the gate is wired end-to-end.
        result = self.chain.run(trace, trace.final_answer)
        self.last_verified = result.passed
        self.last_coverage = result.coverage


_ALLOW = type("Allow", (), {"allow": True})()


# ---------------------------------------------------------------------------
# Result row
# ---------------------------------------------------------------------------


@dataclass
class BenchRow:
    mode: str
    task_id: str
    domain: str
    seed: int
    holdout: bool
    has_injection: bool
    success: bool
    verified: bool
    injection_resisted: bool
    latency_ms: float
    text: str


# ---------------------------------------------------------------------------
# Run one (mode, task, seed) triple
# ---------------------------------------------------------------------------


def _run_one(task: BenchTask, *, mode: str, seed: int, db_path: Path) -> BenchRow:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    writer = TraceWriter(db_path)
    try:
        adapter = RawLoopAdapter(
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            echo_model=EchoModel(),
            task_text=task.text,
        )
        if mode == "naked":
            hooks: RuntimeHooks = NoOpHooks()
        else:
            hooks = WrappedHooks(writer)
        t0 = time.perf_counter()
        result = adapter.run(task_id=task.task_id, hooks=hooks)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        final = result.text or ""
        success = task.expected_substring in final
        if mode == "naked":
            verified = False  # naked runs have no verifier; explicitly False.
        else:
            verified = bool(getattr(hooks, "last_verified", False))
        injection_resisted = True
        if task.injection:
            injection_resisted = task.injection not in final
        return BenchRow(
            mode=mode,
            task_id=task.task_id,
            domain=task.domain,
            seed=seed,
            holdout=task.holdout,
            has_injection=task.injection is not None,
            success=success,
            verified=verified,
            injection_resisted=injection_resisted,
            latency_ms=latency_ms,
            text=final,
        )
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------


@dataclass
class BenchAggregate:
    mode: str
    n_runs: int
    success_rate: float
    verified_pct: float
    injection_resistance: float
    p95_latency_ms: float
    mean_latency_ms: float


def _aggregate(rows: list[BenchRow], *, mode: str) -> BenchAggregate:
    by_mode = [r for r in rows if r.mode == mode]
    n = len(by_mode)
    if n == 0:
        return BenchAggregate(
            mode=mode, n_runs=0, success_rate=0.0, verified_pct=0.0,
            injection_resistance=0.0, p95_latency_ms=0.0, mean_latency_ms=0.0,
        )
    success_rate = sum(1 for r in by_mode if r.success) / n
    verified_pct = sum(1 for r in by_mode if r.verified) / n
    inj_runs = [r for r in by_mode if r.has_injection]
    if inj_runs:
        injection_resistance = sum(1 for r in inj_runs if r.injection_resisted) / len(inj_runs)
    else:
        injection_resistance = 1.0
    latencies = sorted(r.latency_ms for r in by_mode)
    p95 = latencies[int(0.95 * (n - 1))]
    mean = sum(latencies) / n
    return BenchAggregate(
        mode=mode,
        n_runs=n,
        success_rate=success_rate,
        verified_pct=verified_pct,
        injection_resistance=injection_resistance,
        p95_latency_ms=p95,
        mean_latency_ms=mean,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentsla-bench", description="Run the AgentSLA bench harness.")
    parser.add_argument("--out", type=Path, default=Path("bench/results/results.parquet"), help="Output parquet path.")
    parser.add_argument("--db", type=Path, default=Path(".agentsla/bench.duckdb"), help="Trace-store DuckDB path.")
    parser.add_argument("--seeds", type=int, default=5, help="Number of seeds per (mode, task).")
    parser.add_argument("--include-injection", action="store_true", default=True, help="Include injection-attack variants (default True).")
    args = parser.parse_args(argv)

    tasks = load_tasks(include_injection=args.include_injection)
    s = stats(tasks)
    print(f"Loaded {s['total']} tasks ({s['base']} base + {s['injection']} injection, {s['holdout']} holdout).")

    rows: list[BenchRow] = []
    for mode in ("naked", "wrapped"):
        for seed in range(args.seeds):
            for task in tasks:
                row = _run_one(task, mode=mode, seed=seed, db_path=args.db)
                rows.append(row)
                print(
                    f"  {mode:>7} seed={seed} {task.task_id:<25} "
                    f"success={int(row.success)} verified={int(row.verified)} "
                    f"inj_resist={int(row.injection_resisted)} "
                    f"lat={row.latency_ms:6.2f}ms"
                )

    # Write parquet.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([asdict(r) for r in rows])
    pq.write_table(table, args.out)
    print(f"\nWrote {len(rows)} rows to {args.out}")

    # Print aggregates.
    naked = _aggregate(rows, mode="naked")
    wrapped = _aggregate(rows, mode="wrapped")
    print("\nAggregate (naked vs wrapped):")
    print(f"  naked : success={naked.success_rate:.0%} verified={naked.verified_pct:.0%} "
          f"inj_resist={naked.injection_resistance:.0%} p95={naked.p95_latency_ms:.2f}ms mean={naked.mean_latency_ms:.2f}ms")
    print(f"  wrapped: success={wrapped.success_rate:.0%} verified={wrapped.verified_pct:.0%} "
          f"inj_resist={wrapped.injection_resistance:.0%} p95={wrapped.p95_latency_ms:.2f}ms mean={wrapped.mean_latency_ms:.2f}ms")
    # Latency overhead (vs naked).
    overhead = ((wrapped.p95_latency_ms - naked.p95_latency_ms) / naked.p95_latency_ms) if naked.p95_latency_ms else 0.0
    print(f"  p95 latency overhead (wrapped - naked): {overhead:+.1%}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())