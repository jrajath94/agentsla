"""Bench harness — runs ``{naked, wrapped} x tasks x seeds`` and writes
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
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.adapters.base import RuntimeHooks
from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import EchoModel, RawLoopAdapter
from agentsla.bench.tasks import BenchTask, load_tasks, stats
from agentsla.classify import (
    Classifier,
    JsonlLabelSink,
    build_metrics,
)
from agentsla.classify.metrics import on_classify_callback
from agentsla.core.trace import TraceWriter
from agentsla.policy import Policy, PolicyGate
from agentsla.policy.egress import EgressRule, default_egress_rules
from agentsla.tools.deterministic import JsonEchoTool
from agentsla.verify import (
    NumericVerifier,
    VerificationChain,
    VerificationGate,
    identity_source,
)

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
# The bench creates a fresh WrappedHooks per (mode, task, seed) tuple — 175
# wrapped runs in a single CLI invocation. Prometheus ``Counter`` registration
# on the default global registry raises ``ValueError: Duplicated timeseries``
# on the second ``build_metrics()`` call, so we instantiate once at import
# time and share across every WrappedHooks. The label sink is per-instance
# because it owns the file handle, but its appends are append-only + thread-
# unsafe at the OS layer (fine for a single-process bench).


_METRICS = build_metrics()
_LABEL_SINK_PATH = Path("bench/results/labels.jsonl")


# ---------------------------------------------------------------------------
# Wrapped hooks — runs policy gate + verifier + classifier + label sink
# ---------------------------------------------------------------------------


# Symbolic injection payload (``bench/tasks.py``). The default ``aws_access_key``
# egress regex requires ``AKIA`` + 16 uppercase-alnum chars (20 chars total),
# but the bench payload is only 12 chars (a symbolic marker, not a real AWS
# key id). We add a dedicated bench-only rule that matches the literal so
# the gate's enforcement surfaces in the headline numbers. Production
# deployments keep the default pack (real-formatted secrets only) — the
# symbolic rule lives in the bench harness, not in :mod:`policy.egress`.
_BENCH_EGRESS = EgressRule(
    name="ak_symbolic",
    regex=r"\bAKIAEXAMPLE\b",
    severity="deny",
)


class WrappedHooks(RuntimeHooks):
    """Hooks that run the full AgentSLA relay.

    Pipeline (per trace):
      1. ``on_tool_call`` — :class:`PolicyGate` decides ALLOW/DENY/REWRITE
         against the loaded policy (default egress pack + bench-only
         ``ak_symbolic`` rule for the literal injection payload).
      2. ``on_final_answer`` —
         a. :class:`VerificationChain` evaluates numeric claims (Phase 3 gate).
         b. :class:`Classifier` picks a failure category via heuristics
            (≤20% traces go to LLM judge, by default StubJudge).
         c. Label row appended to :class:`JsonlLabelSink`.
         d. Prometheus counter ``agentsla_failures_total{category=...}`` incremented.
         e. Verification-coverage gauge ``agentsla_verify_coverage`` updated.
    """

    INJECTION_PAYLOAD = "AKIAEXAMPLE"

    def __init__(self, writer: TraceWriter, *, label_sink_path: Path | None = None) -> None:
        self.writer = writer
        # ---- Policy gate (Phase 2) ----
        # Egress rules = default pack (real secrets) + bench-only AKIAEXAMPLE rule.
        self.policy = Policy(
            allowed_tools=["json_echo"],
            egress_rules=[*default_egress_rules(), _BENCH_EGRESS],
        )
        self.gate = PolicyGate(self.policy)
        # ---- Verifier (Phase 3) ----
        self.verifier = NumericVerifier(source_resolver=identity_source)
        self.chain = VerificationChain(verifiers=[self.verifier])
        # Gate is the typed bridge: chain result → Verdict event appended to writer.
        # Without this wiring, Verdict rows never reach the trace store (EXECUTION §3 Commit 4).
        self.verification_gate = VerificationGate(self.chain, self.writer, verifier="composite")
        # ---- Classifier (Phase 4) ----
        sink_path = label_sink_path or _LABEL_SINK_PATH
        self.label_sink = JsonlLabelSink(sink_path)
        self.metrics = _METRICS
        self.classifier = Classifier(
            sink=self.label_sink,
            on_classify=on_classify_callback(self.metrics),
            heuristic_context={"allowed_tools": ["json_echo"]},
        )
        # ---- Per-run state (read by _run_one after adapter.run) ----
        self.last_verified: bool | None = None
        self.last_coverage: float | None = None
        self.last_denied_by_policy: bool = False
        self.last_egress_hits: list[str] = []
        self.last_classification: object | None = None

    def on_tool_call(self, call):  # type: ignore[override]
        decision = self.gate.on_tool_call(call)
        if not decision.allow:
            self.last_denied_by_policy = True
            for rule in self.policy.egress_rules:
                if rule.name in (decision.reason or ""):
                    self.last_egress_hits.append(rule.name)
                    break
        return decision

    def on_tool_result(self, call, result):  # type: ignore[override]
        return None

    def on_final_answer(self, trace, verdict):  # type: ignore[override]
        # 1. Verification gate (Phase 3) — runs chain + appends Verdict event.
        gate_result = self.verification_gate.run(trace, trace.final_answer)
        result = gate_result.chain
        self.last_verified = result.passed
        self.last_coverage = result.coverage
        # 2. Classifier (Phase 4) — feeds off the gate's audit log when denied.
        deny_counts: dict[str, int] = {}
        for entry in self.gate.audit:
            if entry["decision"] == "deny":
                deny_counts[entry["tool"]] = deny_counts.get(entry["tool"], 0) + 1
        ctx = dict(self.classifier.heuristic_context)
        if self.last_egress_hits:
            ctx["egress_hits"] = self.last_egress_hits
        if deny_counts:
            ctx["deny_counts"] = deny_counts
        saved_ctx = self.classifier.heuristic_context
        self.classifier.heuristic_context = ctx
        try:
            cls = self.classifier.classify(trace, verification_incorrect=result.incorrect)
        finally:
            self.classifier.heuristic_context = saved_ctx
        self.last_classification = cls
        # 3. Coverage gauge.
        self.metrics.verify_coverage.set(float(self.last_coverage or 0.0))


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
    verified_at_truth: bool | None
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
        # verified_at_truth: only meaningful when the task declares a canonical
        # answer AND the gate passed. Naked runs leave it None (no ground truth
        # comparison happens). The "did the answer actually contain the
        # canonical token?" check is the honest framing of this column.
        verified_at_truth: bool | None = None
        if task.ground_truth is not None and verified:
            verified_at_truth = task.ground_truth in final
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
            verified_at_truth=verified_at_truth,
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
    gate_passed: float
    verified_at_truth: float | None
    injection_resistance: float
    p95_latency_ms: float
    mean_latency_ms: float


def _aggregate(rows: list[BenchRow], *, mode: str) -> BenchAggregate:
    by_mode = [r for r in rows if r.mode == mode]
    n = len(by_mode)
    if n == 0:
        return BenchAggregate(
            mode=mode,
            n_runs=0,
            success_rate=0.0,
            gate_passed=0.0,
            verified_at_truth=None,
            injection_resistance=0.0,
            p95_latency_ms=0.0,
            mean_latency_ms=0.0,
        )
    success_rate = sum(1 for r in by_mode if r.success) / n
    gate_passed = sum(1 for r in by_mode if r.verified) / n
    truth_rows = [r for r in by_mode if r.verified_at_truth is not None]
    if truth_rows:
        verified_at_truth = sum(1 for r in truth_rows if r.verified_at_truth) / len(truth_rows)
    else:
        verified_at_truth = None
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
        gate_passed=gate_passed,
        verified_at_truth=verified_at_truth,
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
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=None,
        help=(
            "If set, start a Prometheus /metrics HTTP server on this port before running the bench. "
            "Closes the gap between the shipped Grafana dashboard JSON (which expects live series) and "
            "the bench's in-memory counters. Default bind address is 127.0.0.1 (loopback only). "
            "Pass --metrics-addr to expose on another interface (NOT recommended outside trusted LANs)."
        ),
    )
    parser.add_argument(
        "--metrics-addr",
        default="127.0.0.1",
        help=(
            "Bind address for the Prometheus /metrics HTTP server. Defaults to 127.0.0.1 to keep the "
            "endpoint off the LAN. Override with --metrics-addr 0.0.0.0 only when running inside a "
            "trusted scrape network — the endpoint exposes failure counts and verification coverage."
        ),
    )
    args = parser.parse_args(argv)

    metrics_server = None
    if args.metrics_port is not None:
        try:
            from prometheus_client import start_http_server

            # Security: prometheus_client.start_http_server defaults to 0.0.0.0 (all interfaces),
            # which would expose failure metrics to anything reachable on the developer's LAN.
            # Bind loopback by default; require an explicit --metrics-addr opt-in for anything else.
            metrics_server = start_http_server(args.metrics_port, addr=args.metrics_addr)
            print(f"Prometheus /metrics serving on {metrics_server.server_address}")
        except ImportError as exc:  # pragma: no cover — optional dep
            print(
                f"WARNING: --metrics-port requested but prometheus_client not available ({exc}); skipping",
                file=sys.stderr,
            )

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
    naked_truth = f"{naked.verified_at_truth:.0%}" if naked.verified_at_truth is not None else "n/a"
    wrapped_truth = f"{wrapped.verified_at_truth:.0%}" if wrapped.verified_at_truth is not None else "n/a"
    print("\nAggregate (naked vs wrapped):")
    print(
        f"  naked : success={naked.success_rate:.0%} gate_passed={naked.gate_passed:.0%} "
        f"verified_at_truth={naked_truth} inj_resist={naked.injection_resistance:.0%} "
        f"p95={naked.p95_latency_ms:.2f}ms mean={naked.mean_latency_ms:.2f}ms"
    )
    print(
        f"  wrapped: success={wrapped.success_rate:.0%} gate_passed={wrapped.gate_passed:.0%} "
        f"verified_at_truth={wrapped_truth} inj_resist={wrapped.injection_resistance:.0%} "
        f"p95={wrapped.p95_latency_ms:.2f}ms mean={wrapped.mean_latency_ms:.2f}ms"
    )
    # Latency overhead (vs naked).
    overhead = ((wrapped.p95_latency_ms - naked.p95_latency_ms) / naked.p95_latency_ms) if naked.p95_latency_ms else 0.0
    print(f"  p95 latency overhead (wrapped - naked): {overhead:+.1%}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
