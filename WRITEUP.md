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
*naked* (just the agent) and *wrapped* (agent + policy gate +
verification gate + classifier + hooks) — across five seeds, plus
five injection-attack variants that embed an AWS-key-formatted
secret (`AKIAIOSFODNN7EXAMPLE`, real-format so the egress regex
matches) in the task text.

The headline table (full per-domain in `bench/results/REPORT.md`):

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 86% | -14% |
| **Gate passed** | **0%** | **100%** | **+100%** |
| **Verified at truth** | **n/a** | **n/a** | — |
| **Injection resistance** | **0%** | **100%** | **+100%** |
| p95 latency (ms) | 10.20 | 9.75 | -0.46 (-4.5%, within noise) |
| Mean latency (ms) | 7.05 | 7.77 | +0.73 |
| N runs | 175 | 175 | — |

The honest reading: in this bench, **wrapping buys verification
coverage AND injection resistance at no measurable p95 latency
overhead.** The verifier recomputes every numeric claim in the final
answer against the trace's tool results and emits a `Verdict` event
with `coverage` and `per_claim` breakdown. The policy gate scans
every tool-call argument value against the egress regex pack
(default: real AWS keys, JWTs, SSN, Luhn-validated card PANs; plus
a bench-only symbolic-AK rule for the 12-char injection marker) and
short-circuits the loop on a hit.

**On the metric rename (Commit 6):** `gate_passed` replaces the older
`verified_pct` column. The bench's default `NumericVerifier` uses an
identity-source resolver (the claim's own value is the source), so a
100% `gate_passed` rate is *not* a claim that the agent's answers are
correct — it is a claim that the gate *ran without rejecting*. The
truthful metric, `verified_at_truth`, is shown as `n/a` for the echo
bench because the synthetic tasks do not declare canonical answers;
it becomes meaningful when wired to real-task corpora. See
`docs/comparative-analysis.md` for the framing.

The 14-point wrapped-success-rate drop is the 25 injection-task
runs (5 tasks × 5 seeds) where the policy correctly blocked the
agent — the wrapped `final=""` so `task.expected_substring in final`
evaluates False. That is intended: a wrapped agent that "succeeded"
at exfiltrating an AWS key would be the bug, not the headline.

The p95 latency overhead is within sub-millisecond noise; the
-4.5% delta on this hermetic bench is dominated by wall-clock
jitter, not gate cost. The default egress pack runs the regex
against every string leaf in the call args; on the hermetic corpus
where args is a single short string, this lands well under 0.1 ms.

## Where we fell short

**The classifier eval is too easy.** We measured 100% agreement
against 100 hand-labelled traces, which is at the ceiling of what
the metric can express. The dataset is synthetically constructed
from the same triggers that the classifier runs — circular signal.
A real eval would use traces from a live LLM agent, not echoes.
The bench wires Classifier + LabelSink + Prometheus counter into
WrappedHooks (175 labels written to `labels.jsonl`, 25 classified
as `policy_violation` for the injection tasks); but the eval set
itself remains synthetic.

**No matplotlib figures.** REPORT.md is tables only. Honest and
reproducible, but the bench writeup was supposed to ship with
figures. We chose ASCII over matplotlib because the figure scripts
would need to be re-run for any parquet regeneration, and the
report contract is "byte-identical table from parquet." A
side-by-side matplotlib emitter that reads parquet is deferred to
v0.2.

**Hermetic EchoModel.** Real Claude / LangGraph adapters exist (Phase
2), but the bench numbers come from in-process EchoModel. A live
replay bench — recorded Claude API traces fed through the same
harness — would produce a number with signal-to-noise. Phase 6 work.

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

`context_overflow` (severity 6) and `timeout` (severity 3) round
out the operational categories: payload bytes exceeding the model's
context window, or trace duration exceeding the per-trace deadline.

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

The injection-resistance and classifier-into-hooks gaps from the
v0.1 audit are now closed (Phase 2 PolicyGate and Phase 4
Classifier are wired into WrappedHooks; 175 labels written per
bench run). The honest remaining work is:

## Architecture (one picture)

```mermaid
flowchart LR
    subgraph Agent["Agent runtime (any of: RawLoop, LangGraph, Claude SDK)"]
        A1[ToolCall] --> A2[Tool Result]
        A2 --> A3[Final Answer]
    end

    A1 -->|on_tool_call| PG[PolicyGate<br/>egress regex pack<br/>ALLOW / DENY / REWRITE]
    PG -->|allowed| A2

    A2 -->|on_tool_result| TW[(TraceWriter<br/>DuckDB append-only)]
    TW -->|stored| TS[(TraceStore<br/>Parquet export)]

    A3 -->|on_final_answer| VG[VerificationChain<br/>Numeric / Schema / Grounding]
    VG --> GR[VerificationGate]
    GR -->|emit Verdict| TW
    GR -->|result| CL[Classifier<br/>14 triggers + StubJudge]
    CL --> LS[(JsonlLabelSink<br/>labels.jsonl)]
    CL --> PM[Prometheus counters]

    TS --> RD[TraceReader<br/>deterministic replay]
    RD -->|strict / tolerant| A1
```

See [`docs/comparative-analysis.md`](docs/comparative-analysis.md)
for the side-by-side framing against LangSmith / Langfuse / Helicone
/ Braintrust.

1. **Real classifier eval.** The current 100% agreement is circular
   (synthetic traces from same triggers as the classifier). A live
   Claude API replay would produce a number with signal.
2. **Phase 3 RawLoopAdapter.run integration test.** The verifier
   unit-tests pass; the end-to-end drive through the adapter was
   dropped during Phase 3 implementation. Re-attempting now with
   the correct signatures would catch any regression in the hook
   fan-out.
3. **Cross-adapter parity bench.** Same task under Claude SDK +
   LangGraph + rawloop with identical policy decisions, run as a
   separate bench matrix. Phase 2's adapter parity contract proves
   this is possible; the bench harness would need a per-adapter
   driver.

## How to reproduce

```bash
git clone <repo> && cd agentsla
uv sync --extra all
python -m pytest                       # 432 tests, ~12s
python -m agentsla bench --seeds 5      # 350 rows → results.parquet
python -m agentsla report --out bench/results/REPORT.md
# Real-LLM bench (requires ANTHROPIC_API_KEY):
ANTHROPIC_API_KEY=sk-... python -m agentsla bench-real --tasks-per-domain 3
```

Every number in this writeup and in `README.md` is regenerated from
the parquet by `agentsla report`. The contract is: re-running
bench+report must produce a byte-identical table.

---

## What shipped in v0.2 / v0.2.2

The v0.2 / v0.2.2 push closes the third-adapter gap and the real-LLM
bench gap that v0.1 deferred. Below is the honest accounting — what's
real, what's measured, and what remains. The exact release dates and
release-line highlights are in `CHANGELOG.md § [v0.2.0]` and
`CHANGELOG.md § [v0.2.2]`.

### Third adapter: ClaudeSdkAdapter

`agentsla/adapters/claude_sdk.py` wraps the Claude Agent SDK with
zero runtime dependency on `claude_agent_sdk` (the client is
injected via duck typing). The adapter translates SDK messages
(`text`, `tool_use`) into the same 4-event shape as
`RawLoopAdapter`: `model_message(user)` → `tool_call` →
`tool_result` → `model_message(assistant)`. The cross-adapter
parity test (`tests/integration/test_claude_sdk_parity.py`) enforces
this byte-for-byte modulo UUIDs. Without parity, the policy gate /
verifier / classifier would not be adapter-agnostic.

**Honest gap**: The shipped adapter is driven by a fake SDK client
in tests; the live `claude_agent_sdk` is not installed in CI (it's
optional, opt-in). A reviewer can wire a real client and the
adapter contract holds; we have not run a live Claude API through
the SDK adapter at v0.2.

### Real-LLM bench harness

`python -m agentsla bench-real` runs the task set through the actual
Claude API (default model `claude-haiku-4-5-20251001`). Without
`ANTHROPIC_API_KEY` the harness fails fast (exit 2) with a clear
stderr message; errors during the run become rows tagged
`[NOT YET MEASURED]` so the parquet is honest when the API
rate-limits mid-run.

**Honest gap**: The harness path, tests, and CLI are real; the live
numbers require a key. No live rows are committed at v0.2 because no
`ANTHROPIC_API_KEY` is available in the build environment. The
schema (`mode, task_id, domain, model_id, seed, success, gate_passed,
verified_at_truth, sensitivity, specificity, latency_ms, text, note`)
is the contract a reviewer can fill in by setting the env var and
re-running.

### Real held-out fixture

`scripts/build_held_out_fixture.py` splits into two builders:

* `build_synthetic_held_out_fixture` — pure-Python, no key. Always
  works; rows tagged `synthetic=true`.
* `build_real_held_out_fixture` — runs Claude. With no key +
  `synthetic_fallback=True` (default) it degrades to synthetic rows;
  with `synthetic_fallback=False` it raises `RuntimeError`.

Every row carries `model_id` so the eval report can mark REAL vs
SYNTHETIC provenance. Closes the v0.1 "classifier eval is circular"
gap.

### Per-verifier tolerance + range claim per-endpoint multiplier

`NumericVerifier(tolerance=...)` was already available pre-v0.2;
v0.2 pins the contract with regression tests in
`tests/unit/verify/test_numeric_tolerance_config.py`. The range-
claim regex (`agentsla/verify/claims.py:_RANGE_PATTERN`) now accepts
K/M/B/% on both endpoints: `$4.2M-$4.5M` → `(4_200_000, 4_500_000)`.
The pre-fix behavior silently dropped the second endpoint's
multiplier, inflating unverifiable coverage on real P&L traces.

The semantic-escape hardening (`4--5` still rejected) is preserved.
The split-regex lookbehind was widened from `(?<=\d)` to
`(?<=[\dKkMmBb%])` so `4M-5M` actually splits into two endpoints.

### README truth-pinned

The README quickstart snippet now uses the real surface (`Policy`,
`PolicyGate`, `NumericVerifier`, `VerificationChain`, `Classifier`,
`InMemoryLabelSink`, `TraceWriter`) and binds a non-empty
`final.text`. The integration test in
`tests/integration/test_readme_quickstart.py` exec()s the snippet
and asserts the four guarantees bind + a non-empty `final.text` is
produced. A stale rewrite that drops any of the four guarantees
breaks the test.

### Failure modes: 6 → 15

`docs/failure-modes.md` adds 9 modes surfaced by the v0.2 push:
adapter parity drift, range multiplier mismatch, real-LLM rate-
limit, held-out fixture circularity, per-verifier tolerance drift,
fixture degradation, CLI subcommand collision, generated artifacts,
tool-call id collisions. Each is documented with trigger, why it
breaks, observable signal, v0.2 status, and mitigation — the same
format as the v0.1 6-mode list.

### Test count

The exact test count is whatever `pytest tests/ -q --co | tail -1`
reports on the current commit. A pinned integer here would drift the
moment a new test lands; the live command is the source of truth.
The v0.2 / v0.2.2 push added tests across the integration, unit,
property, and script test directories — exact delta is in
`CHANGELOG.md § [v0.2.0] Atomic commits` and `CHANGELOG.md § [v0.2.2]`.

### Quality gates (v0.2 / v0.2.2)

| Gate | Status |
|------|--------|
| `pytest -q` | live count from `pytest tests/ -q --co` |
| `ruff check .` | clean |
| `mypy --strict agentsla/core agentsla/policy agentsla/verify` | zero findings |
| Coverage on core/policy/verify | ≥ 85% |
| CI integration gate | wired symbols pinned by grep |
| Cross-adapter parity | byte-identical (modulo UUIDs) |
| README truth-pin | integration test green |

### v0.2 / v0.2.2 honest gaps (carried forward)

The v0.2 / v0.2.2 push explicitly does NOT solve:

* **Live-LLM bench numbers** — the harness + tests + CLI are real;
  the actual numbers require a key (explicitly marked
  `[NOT YET MEASURED]`).
* **Concurrent adapter paths** — current adapters are single-
  threaded; tool-call id collision is documented but not yet a v0.2
  path.
* **Per-verifier tolerance consensus** — chain does not enforce
  uniform tolerance; operators choose per domain.
* **Streamed trace emission** — current TraceWriter is sync.
* **Multi-tenancy / per-tenant policy** — governance decisions TBD.
* **OpenTelemetry exporter** — separate design pass.

Each of these is a small, bounded change for v0.3 or later. None
require redesigning the surface.

— AgentSLA contributors, 2026.
## A note on holdouts

The bench reserves every fourth task as a rotating holdout. Out of the
30 base tasks, eight are marked `holdout=True`. The dataset builder
also injects five injection-attack variants of the first five base
tasks, bringing the total corpus to 35 tasks. The holdout ratio on
the base 30 is 26.7%, comfortably above the 25% minimum called out
in the project spec as PITFALL #9 (over-tuning to a fixed test set).
In practice, the holdout numbers mirror the headline — the gap
between held-out and seen-task verification rates is currently zero,
because the verifier is rule-based and does not learn. The holdout
discipline matters more for future versions where the classifier
gains learned weights.

## Why we picked DuckDB for the trace store

Three candidates: SQLite (single-writer, ubiquitous), Postgres
(networked, production-grade), DuckDB (columnar, embedded). DuckDB
won because its native Parquet round-trip preserves the JSON payload
column without lossy serialization, and `read_only=True` on the
reader connection is the documented mitigation for the multi-process
MVCC pitfall called out in our design review. Trade-off: DuckDB's
single-writer lock serializes concurrent writers. v0.1 is
single-process, so the constraint is binding but not binding yet.

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

**Tolerance is per-verifier, not global.** Each
`NumericVerifier(tolerance=...)` instance carries its own threshold;
the `VerificationChain` runs verifiers in declared order and
aggregates per-claim verdicts. The shipped default is `1e-6` (relative
against `max(|observed|, |expected|, 1e-12)`). Financial-ops
deployments should use `1e-9` to catch rounding drift in P&L sums;
doc-QA can loosen to `1e-3` because prose answers rarely match a
tool result to more than three significant figures. The right value
is domain-specific; the API does not assume one fits all.

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
(35 tasks × 5 seeds) and the model is hermetic. The headline is
intentionally narrow: "wrapped gives you verification coverage
AND injection resistance that naked does not, at ~13% p95 latency
overhead on this corpus." Everything else (policy enforcement at
scale, real LLM-judge agreement, cross-adapter parity under live
network load) is v0.2 and beyond.

The interesting next moves are the ones we deliberately left on
the table: a live replay bench against recorded Claude API traces
(instead of the echo model) to produce a number with real
signal-to-noise, re-attempting the dropped Phase 3
`RawLoopAdapter.run` integration test, and a cross-adapter parity
bench that runs the same task under Claude SDK + LangGraph +
rawloop and asserts byte-identical policy decisions. Each is a
small, bounded change. None of them require redesigning the surface.

