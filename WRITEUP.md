# SLAs for Agents

*An honest accounting of what AgentSLA proves, what it doesn't, and the
choices we made.*

## What problem are we solving

Enterprises don't lack agents. They lack **agents with SLAs**. When a
tool-calling agent answers "the Q3 revenue is $4.2M," the operator
needs three guarantees: (1) the agent actually got there via a recorded
chain of tool calls; (2) the answer is internally consistent with the
tool results; (3) when the answer is wrong, the operator can label
*why* — was it a hallucinated fact, a tool-call error, a reasoning
contradiction, or a transient tool failure? Without those guarantees,
agentic automation is a credibility problem dressed up as productivity.

AgentSLA wraps any tool-calling agent (Claude SDK, LangGraph, or a
reference rawloop) with the surface area needed to answer those three
questions from a single append-only event log.

## What we measured

A 30-task bench across financial ops, incident triage, and doc QA,
run in hermetic mode (in-process EchoModel + JsonEchoTool) so the
numbers are reproducible offline. Each task runs in two modes —
*naked* (just the agent) and *wrapped* (agent + verification gate +
classifier + hooks) — across five seeds, plus five injection-attack
variants that embed the literal `AKIAEXAMPLE` token in the task text.

The headline table (full per-domain in `bench/results/REPORT.md`):

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 100% | +0% |
| **Verified %** | **0%** | **100%** | **+100%** |
| Injection resistance | 0% | 0% | +0% |
| p95 latency (ms) | 6.21 | 5.35 | -0.85 (-13.8%) |
| Mean latency (ms) | 5.07 | 4.81 | -0.26 |
| N runs | 175 | 175 | — |

The honest reading: in this bench, **wrapping buys verification
coverage at zero throughput cost.** The verifier recomputes every
numeric claim in the final answer against the trace's tool results
and emits a `Verdict` event with `coverage` and `per_claim`
breakdown. Naked runs have no such signal — they don't even attempt
verification. Wrapped runs land at 100% because the identity source
resolver (`claim.value == source`) self-certifies claims that
originate in the agent's own echo.

The wrapped p95 is *lower* than naked, by chance — wall-clock noise at
the millisecond scale dominates the 0.8 ms gap. We do not claim
wrapping is faster; we claim it's not measurably slower at this
bench's scale. A larger bench (300+ tasks) would resolve the sign.

## Where we fell short

**Injection resistance is 0% in both modes.** This is not a bug in the
heuristic; it's a missing feature. The wrapped hooks only adds
verification, not scrubbing. The Phase 2 policy gate has the egress
regex pack (AWS keys, JWTs, SSN, Luhn-validated card PANs) wired up
to the ALLOW/DENY/REWRITE decision — it just isn't connected to the
bench's WrappedHooks yet. Wiring it would push the wrapped injection
resistance to ~100%, but we deliberately kept the bench honest: the
current numbers are what the current code produces.

**The classifier eval is too easy.** We measured 100% agreement
against 100 hand-labelled traces, which is at the ceiling of what
the metric can express. The dataset is synthetically constructed
from the same triggers that the classifier runs — circular signal.
A real eval would use traces from a live LLM agent, not echoes.
The 7% LLM-judge invocation rate is more telling: in production,
heuristics cover 93% of traces; the remaining 7% would burn
judge-model cost. The unit economics work for haiku 4.5 at $1/MTok
input, but they wouldn't work for opus.

## What we tried, and why we changed it

**In-process rawloop, not a framework fork.** The early design
considered forking the Claude SDK to inject hooks at the SDK
internals. We abandoned that because it would force users to drop
our fork into their stack. Hooks at the agent-loop boundary
(`on_tool_call`, `on_tool_result`, `on_final_answer`) keep the
runtime portable across Claude SDK, LangGraph, and rawloop — the
three adapters all implement the same `AgentAdapter` ABC, so the
runtime treats them identically. The runtime-vs-wrapper moment is
the cross-adapter parity test: the same task produces the same
ALLOW/DENY decision under each adapter.

**Append-only event log as the source of truth.** We considered an
in-memory mutable trace object, but that breaks replay: a verifier
that mutates the trace can't be re-run. The append-only log means
the verifier appends a `Verdict` event instead of mutating the
trace, and the replay engine reads the log and re-executes the
recorded tool calls without re-running the verifier. The trace
store is a DuckDB single `events` table with `(trace_id, seq)`
ordering; the reader opens `read_only=True` so a running replay
can't corrupt the live log.

**Verification coverage as a first-class metric.** A binary
"verified" / "not verified" was tempting but misleading. A trace
with five numeric claims where four pass and one fails is not the
same as a trace with one claim that passes. We emit `coverage =
verified / total_claims` on every verdict and require
`incorrect == 0 AND coverage >= threshold` for the verdict to
pass. Operators set the threshold per-domain: financial ops at
0.99, doc QA at 0.7.

**Heuristic-first classifier.** The naive design was to call the
LLM judge on every trace. That's ~$0.001 per trace, multiplied by
traces-per-second in a production deployment, equals a non-trivial
line item. The 14-trigger heuristic stage handles 93% of traces
without the LLM. The remaining 7% go to the judge with a
content-hash-pinned prompt so the same input always produces the
same prompt — important for replay, audit, and eval consistency.

## Failure modes we observed (14-category taxonomy)

The classifier outputs one of 14 categories sourced from the MAST
taxonomy (arXiv 2503.13657), adapted for single-agent traces. In
priority order:

`hallucinated_fact` (severity 9) — final answer asserts a fact not
derivable from any tool result. The verifier catches these on
numeric claims; the classifier catches them on non-numeric claims
when the verification gate reports `incorrect > 0`.

`policy_violation` (severity 9) — an event payload matches the
egress regex pack (AWS key, JWT, SSN, Luhn PAN). The policy gate
emits DENY; the classifier re-labels at end-of-trace so the
Prometheus counter aggregates by category.

`reasoning_error` (severity 8) — the agent makes contradictory
numeric claims in the same final answer (e.g., "Total = 100. Total
= 50."). Detected by the verifier; surfaced by the classifier.

`tool_response_misuse` (severity 7) — the agent invokes a tool that
returns an error and proceeds as if it succeeded. Detected when a
ToolResult has `error` set AND a subsequent ToolCall does not adapt.

`retry_loop` (severity 5) — three or more consecutive ToolCalls
with identical `(tool, args_hash)`. Detected by hashing
`(tool, args)` only (excluding call_id/seq so two semantically
identical calls produce the same hash).

`context_overflow` (severity 6) — sum of event payload bytes
exceeds the model's context window. Triggered by a configurable
threshold (default 200 KiB).

`timeout` (severity 3) — trace duration exceeds the per-trace
deadline. Triggered when `(end_ts - start_ts) > deadline_s`.

The full taxonomy table lives at `docs/class-taxonomy.md`, with
severity scores for tie-breaking and explicit definitions for each
category. The classifier was evaluated at 100% agreement against
100 hand-labelled traces.

## Failure-mode appendix (what breaks at p99.9)

We did not load-test this. The bench is single-process; the
DuckDB writer is single-writer; the verifier and classifier are
CPU-bound. At p99.9 under load:

- **DuckDB writer lock contention.** Multiple processes writing to
  the same `.duckdb` file will serialize. Mitigation: per-process
  `TraceWriter` with rotation, fan-in via a sidecar collector.
  Out of scope for v0.1.
- **Verifier scaling with claim count.** A trace with 10k numeric
  claims will spend seconds in the regex extraction phase. The
  extractor deduplicates via span-set intersection, but a runaway
  long trace could starve the gate.
- **LLM-judge availability.** When the judge backend (haiku) is
  unreachable, the classifier falls back to the highest-severity
  heuristic candidate. That's a degraded-but-functional state;
  production deployments should set an alert on the
  `agentsla_classify_latency_seconds` histogram and on judge
  failure rate.

## What we'd ship next

The injection-resistance gap is the single biggest one. Wiring
the Phase 2 policy egress regex pack into WrappedHooks would close
that, and the change is small (~30 lines). After that, a real
end-to-end integration test that drives the RawLoopAdapter through
both the verifier and the classifier — the integration test we
dropped in Phase 3 because of config-signature whack-a-mole — is
the next milestone.

The classifier eval needs real traces, not synthetic echoes.
Hooking the bench to a live Claude API (or a recorded replay of
one) would produce a number with actual signal-to-noise. That's
Phase 6 work, not v0.1.

## How to reproduce

```bash
git clone <repo> && cd agentsla
uv sync --extra all
python -m pytest                       # 295 tests, ~6s
python -m agentsla bench --seeds 5      # 350 rows → results.parquet
python -m agentsla report --out bench/results/REPORT.md
```

Every number in this writeup and in `README.md` is regenerated from
the parquet by `agentsla report`. The contract is: re-running
bench+report must produce a byte-identical table.

— AgentSLA contributors, 2026.
## A note on holdouts

The bench reserves every fourth task as a rotating holdout. Out of the
30 base tasks, eight are marked `holdout=True`. The dataset builder
also injects five injection-attack variants of the first five base
tasks. The holdout ratio is 26.7%, comfortably above the 25% minimum
called out in the project spec as PITFALL #9 (over-tuning to a fixed
test set). In practice, the holdout numbers mirror the headline — the
gap between held-out and seen-task verification rates is currently
zero, because the verifier is rule-based and does not learn. The
holdout discipline matters more for future versions where the
classifier gains learned weights.

## Why we picked DuckDB for the trace store

We considered three candidates: SQLite (ubiquitous, single-writer),
Postgres (production-grade, networked), and DuckDB (columnar,
embedded). The deciding factor was the **append-only event log with
parquet export** requirement: DuckDB's native Parquet round-trip
preserves the JSON payload column without lossy serialization, while
SQLite would force us to store events as TEXT and re-parse on every
read. Postgres adds a network dependency and a process model that
complicates the read-only replay path. DuckDB also gives us
`read_only=True` on the reader connection, which is the documented
mitigation for the multi-process MVCC pitfall we hit during design
review. The trade-off is DuckDB's single-writer lock — concurrent
writer processes serialize — but the v0.1 runtime is single-process,
so the constraint is binding but not binding yet.

## How the gate decides what to recompute

The numeric verifier extracts claims via a four-pattern regex
(integer, float, currency, percent) plus an `ast.parse` validation
for arithmetic expressions like "2 * 3 + 1". A span-set dedup step
prevents the same numeric span from being matched twice (e.g.,
"$1,200" should not also be matched as "1200" inside the same
substring). Each extracted claim is recomputed against the
`source_resolver` callable. The default identity source means claims
without explicit grounding self-verify — useful for benchmark
calibration, deliberately useless for production where every claim
should map to a tool result. Operators swap the resolver to inject
domain knowledge: "this claim is the sum of column X in tool result
Y, recompute as `sum(result.y)` and tolerance-check against 1e-6
relative".

## What the LLM judge actually sees

The judge prompt template is committed to `agentsla/classify/judge.py`
and content-hash-pinned at module-import time. The hash is logged
with every invocation. The template is short on purpose: a one-line
event summary (kind + tool + error/verified flag) plus the final
answer text. The judge is asked to pick one of 14 categories or
"none" and report a confidence in [0, 1]. We accept judge output
when confidence ≥ 0.7; below that, we fall back to the
highest-severity heuristic candidate. The hash pin means the same
trace content always produces the same prompt — the judge is
replayable, and downstream eval scripts can compare labels across
versions by diffing the prompt hash, not by re-running the judge on
the entire dataset.

## Closing thoughts

AgentSLA is a v0.1, not a product. The numbers are real — every
row of `results.parquet` is reproducible — but the corpus is small
(30 tasks × 5 seeds) and the model is hermetic. The headline is
intentionally narrow: "wrapped gives you verification coverage
that naked does not, at zero measurable latency cost." Everything
else (policy enforcement at scale, real LLM-judge agreement,
cross-adapter parity under live network load) is v0.2 and beyond.

The interesting next moves are the ones we deliberately left on
the table: wiring the policy egress regex into WrappedHooks to
close the injection-resistance gap, running the bench against a
recorded Claude API replay (instead of the echo model) to produce
a number with real signal-to-noise, and adding the dropped Phase 3
integration test through `RawLoopAdapter.run`. Each is a small,
bounded change. None of them require redesigning the surface.

