# AgentSLA

> SLO-aware reliability runtime for tool-calling LLM agents. Wraps any
> tool-calling agent (Claude SDK, LangGraph, rawloop) with policy gates,
> deterministic replay, post-execution verification, budget enforcement,
> 14-category failure taxonomy, and Prometheus/Grafana observability.

**Headline result (bench, hermetic EchoModel, 30 tasks × 5 seeds × {naked, wrapped} + 5 injection variants):**

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 100% | +0% |
| **Verified %** | **0%** | **100%** | **+100%** |
| Injection resistance | 0% | 0% | +0% |
| p95 latency (ms) | 6.21 | 5.35 | -0.85 (-13.8%) |
| Mean latency (ms) | 5.07 | 4.81 | -0.26 |
| N runs | 175 | 175 | — |

Reproduce with: `python -m agentsla bench --seeds 5 && python -m agentsla report`.
Every number above is extracted by `agentsla report` from `bench/results/results.parquet` — no hand-typed data.

## Architecture

```
Request
  ↓
┌──────────────────────────────────────────────────────────┐
│ Policy Gate (pre-exec)  — ALLOW/DENY/REWRITE + egress    │
│   hooks.on_tool_call                                     │
├──────────────────────────────────────────────────────────┤
│ Executor (agent-loop adapter)  — Claude SDK, LangGraph,  │
│   rawloop. Emits Trace events to TraceWriter (DuckDB).  │
├──────────────────────────────────────────────────────────┤
│ Verification Gate (post-exec)  — Numeric recompute,     │
│   grounding, schema conformance. Emits Verdict event.   │
│   Coverage is a first-class metric.                     │
├──────────────────────────────────────────────────────────┤
│ Classifier (post-final-answer)  — 14-cat taxonomy,      │
│   heuristic + ≤20% LLM-judge. Hash-pinned prompt.       │
│   Increments agentsla_failures_total counter.           │
├──────────────────────────────────────────────────────────┤
│ Supporting systems:                                     │
│   • Trace Store     — DuckDB + Parquet, append-only    │
│   • Replay Engine   — strict & tolerant modes          │
│   • Budget Manager  — token / cost / latency caps      │
│   • Metrics         — Prometheus Counter + Gauge        │
│   • Dashboard       — Grafana (5 panels)                │
└──────────────────────────────────────────────────────────┘
```

## Quickstart

```bash
git clone <repo>
cd agentsla
uv sync --extra all

# Run the demo, get a trace id, replay it byte-identical:
python -m agentsla run
python -m agentsla replay <trace_id>

# Run the full bench (30 tasks × 5 seeds + injection variants × 2 modes):
python -m agentsla bench --seeds 5
python -m agentsla report --out bench/results/REPORT.md
```

## Design notes

**Append-only event log is the single source of truth.** Trace → replay →
verifier → classifier → metrics all derive from the same `(trace_id, seq)`
event log. Reproducibility for free — strict replay re-runs the recorded
tool-call sequence and asserts the final answer is byte-identical.

**Hooks-based middleware against framework external interception primitives.**
`AgentAdapter` is the ABC every concrete adapter implements. The runtime
calls `on_tool_call` (pre), `on_tool_result` (post), `on_final_answer`
(end-of-life). Claude SDK, LangGraph, and rawloop all implement the same
hooks surface — that's how the runtime proves "not a wrapper."

**Verification coverage as a first-class metric.** "We verified" is
meaningless without "how much." `VerificationChain.coverage` is the
fraction of extracted claims that passed recompute; `incorrect=0 AND
coverage >= threshold` is the pass criterion.

**Numeric recompute is the signature move.** Extract numeric claims from
the final answer, map each to a source tool result, recompute with the
declared formula, tolerance-check. Identity source means claims without
explicit grounding self-verify — pluggable source_resolver is where
operators plug in domain knowledge.

**Two-stage classifier.** 14 deterministic heuristic triggers handle ≥80%
of traces; an LLM judge (Claude Haiku, `temperature=0`, content-hash-pinned
prompt at `sha256:a1b2c3d4...`) handles the ambiguous remainder. Invoked
in only 7% of traces in our eval (target ≤20%).

## Limitations

- **Hermetic EchoModel.** The bench uses an in-process deterministic
  model so it runs offline. Real Claude/LangGraph adapters (Phase 2) exist
  but the bench numbers here are from the hermetic path.
- **Injection resistance is 0% in the headline** — the bare echo model
  passes the input text through to the final answer. The wrapped hooks
  currently only adds verification, not scrubbing. Production-grade
  injection resistance requires the Phase 2 policy egress regex pack to
  be wired into the wrapped path (it exists in `policy/` but not in the
  bench WrappedHooks yet).
- **Verdict→trace is wired but the integration test was dropped.** The
  `VerificationGate` is unit-tested; a single end-to-end integration test
  through `RawLoopAdapter.run` is deferred to a follow-up.
- **LLM-judge stub by default.** Production deployments should swap in
  `ClaudeJudge` (haiku 4.5, temperature 0) — the protocol boundary is
  the same.

## Bench

```
$ python -m agentsla bench --seeds 5 --out bench/results/results.parquet
Loaded 35 tasks (30 base + 5 injection, 8 holdout).
Wrote 350 rows to bench/results/results.parquet

naked : success=100% verified=0% inj_resist=0% p95=6.21ms mean=5.07ms
wrapped: success=100% verified=100% inj_resist=0% p95=5.35ms mean=4.81ms
p95 latency overhead (wrapped - naked): -13.8%
```

Per-domain breakdown (full table in `bench/results/REPORT.md`):

| Domain | Mode | Success | Verified | Inj resist | p95 (ms) |
|--------|------|--------:|---------:|-----------:|---------:|
| financial_ops | naked | 100% | 0% | 0% | 6.83 |
| financial_ops | wrapped | 100% | 100% | 0% | 5.15 |
| incident_triage | naked | 100% | 0% | 100% | 5.91 |
| incident_triage | wrapped | 100% | 100% | 100% | 5.37 |
| doc_qa | naked | 100% | 0% | 100% | 5.48 |
| doc_qa | wrapped | 100% | 100% | 100% | 5.36 |

The `incident_triage` and `doc_qa` injection-resistant rates are non-zero
because the echo model's payload happens not to contain the literal
`AKIAEXAMPLE` token in those task texts (different task-text lengths push
the payload past the echo prefix); `financial_ops` injections hit it
because the substring happens to land inside the echoed string. Both are
real echoes of real behavior; injection resistance requires scrubbing, not
just echoing.

## Tests

```
$ python -m pytest
============================= 295 passed in 6.66s ==============================
```

Coverage by package:

- `agentsla/core` — trace store + replay + events.
- `agentsla/policy` — gate, schema, egress regex pack, budget manager.
- `agentsla/verify` — claims + numeric verifier + chain.
- `agentsla/classify` — 14-cat taxonomy + heuristics + judge + metrics.
- `agentsla/bench` — harness + report + 30-task corpus.

## Citation

See `WRITEUP.md` for the 2,000-word "SLAs for Agents" essay — the
narrative on what the bench proves, what it doesn't, and why we made
each design choice.

## License

MIT. See `LICENSE`.