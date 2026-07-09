# Failure modes — what breaks at p99.9

AgentSLA v0.1 is single-process, hermetic, and bench-validated at
~350-row scale. It is not load-tested. This document enumerates the
failure modes we know about, what triggers each, the observable signal
in production, and the v0.1 mitigation (if any). The list is honest:
it does not include failure modes we have not measured.

The bench reproducer is `python -m agentsla bench --seeds 5 &&
python -m agentsla report`. The writeup at `WRITEUP.md § "Failure-mode
appendix"` carries the high-level summary; this file is the
operational reference for the on-call engineer.

---

## 1. DuckDB writer lock contention

**Trigger**: Two or more `TraceWriter` instances writing to the same
`.duckdb` file from different processes or threads.

**Why it breaks**: DuckDB's MVCC design serializes concurrent writers
through a single-writer lock (cf. DuckDB docs on concurrency). The
process holding the write lock blocks every other writer; under
sustained load the tail-latency of `trace.append()` grows unbounded.

**Observable signal**: `agentsla_trace_append_latency_seconds` histogram
p99.9 climbs; threads begin queueing on the writer lock; downstream
verifier latency stays bounded but the trace store is the bottleneck.

**v0.1 status**: Single-writer per file. Multi-process deployments
are out of scope. The shipped bench is single-process.

**Mitigation**: per-process `TraceWriter` with file rotation
(`/trace-001.duckdb`, `/trace-002.duckdb`, ...), then a sidecar
collector fans the rotated files into a queryable lake. Out of scope
for v0.1.

---

## 2. Verifier scaling with claim count

**Trigger**: A long-running trace whose final answer contains thousands
of numeric claims (think: a financial-reporting agent that emits a 200-
row ledger table).

**Why it breaks**: The numeric extractor (`agentsla/verify/claims.py`)
runs four regex passes plus an `ast.parse` validation step per
arithmetic expression. Span-set dedup keeps the per-pass work
sub-quadratic, but the absolute time still scales with `len(final_answer)`.

**Observable signal**: `agentsla_verify_latency_seconds` histogram
p99.9 rises above ~50 ms; CPU is dominated by the regex engine.

**v0.1 status**: Bench traces have ≤5 numeric claims each. Wall-clock
on the bench is sub-millisecond. The latency frontier is unmeasured.

**Mitigation**: claim-count budget on `policy.yaml:max_claims_per_trace`
(short-circuits the extractor); chunked extraction (process N claims,
yield, continue). Both are small, bounded changes for v0.2.

---

## 3. LLM-judge availability

**Trigger**: The `:class:`ClaudeJudge` backend (haiku 4.5) returns a
network error, 5xx, or rate-limit. Same for any swap-in judge provider.

**Why it breaks**: The two-stage classifier calls the judge only when
the heuristics stage returns no high-severity candidate. When the
judge is unreachable, the classifier still needs to emit a label.

**Observable signal**: `agentsla_classify_judge_errors_total` counter
increments; `agentsla_classify_latency_seconds` p99 spikes on the
judge-call phase.

**v0.1 status**: The shipped classifier falls back to the highest-
severity heuristic candidate when the judge raises. This is a
degraded-but-functional state — the classifier does not block on
judge availability.

**Mitigation**: Set an alert on
`agentsla_classify_judge_errors_total` rate; configure
`policy.yaml:classify.judge_timeout_ms` to bound the wait. A circuit
breaker that disables the judge after K consecutive failures (rather
than per-call) is v0.2 work.

---

## 4. Hermetic EchoModel bias

**Trigger**: Reading `bench/results/REPORT.md` headline numbers and
assuming they describe a live Claude / LangGraph deployment.

**Why it breaks**: The shipped bench uses `EchoModel` + `JsonEchoTool`
in-process. Real LLM endpoints are non-deterministic even with
`temperature=0`; real tools have latency tails the bench does not
capture. The headline "p95 overhead ~5%" is the overhead against the
echo model, not against Claude Opus.

**Observable signal**: When the same agent is wired through
`ClaudeAgentSDK` instead of `RawLoopAdapter`, latency triples and the
p95 jumps from ~10 ms to ~400 ms because of network round-trips. The
bench does not show this.

**v0.1 status**: Documented in `WRITEUP.md § "Where we fell short"`
and `README.md § "Limitations"`. The headline is intentionally narrow.

**Mitigation**: Cross-adapter parity bench (same task under Claude
SDK + LangGraph + rawloop) is v0.2. The hermetic path stays for unit-
test reproducibility.

---

## 5. Classifier eval circularity

**Trigger**: Reading the 100% agreement score against the 100-label
held-out set and inferring the classifier is production-ready.

**Why it breaks**: The held-out set is constructed from the same
triggers the classifier uses. The agreement metric is at the ceiling
of what it can express — there is no signal above 100%.

**Observable signal**: The classifier appears to score perfectly on
the bench eval. It would also score perfectly on its own training
data; the metric does not distinguish.

**v0.1 status**: Documented in `WRITEUP.md § "Where we fell short"`.

**Mitigation**: Replace the synthetic eval set with traces recorded
from a live Claude API replay (which the bench infrastructure
already supports — `TraceWriter` is the same). v0.2 work; not in
v0.1.

---

## 6. Egress regex false positives

**Trigger**: A task whose legitimate arguments contain a string that
trivially matches the default egress pack — for example, a base64-
encoded JWT in a test fixture, or a literal hex string that the regex
mistakes for a card PAN.

**Why it breaks**: The default pack (real AWS keys, JWTs, SSN, Luhn-
validated PANs) is intentionally broad. The wider the pack, the more
denials on legitimate inputs.

**Observable signal**: `agentsla_policy_denies_total{rule=...}` rises
for non-malicious traffic; downstream `task.expected_substring in final`
evaluates False because the wrapped agent short-circuited.

**v0.1 status**: The pack is fixed (the four rules above). Operators
cannot yet disable individual rules per-tenant; that requires the
`policy.yaml` authoring path (feedback.md Item 6).

**Mitigation**: `policy.yaml:egress_rules` config (selectable per-
rule, per-tenant). The schema is being designed in feedback.md Item 6
and ships in the same commit as this document lands.

---

## Out of scope for this list

- Adversarial inputs the verifier cannot parse (claimed text with
  emoji, RTL marks, zero-width joiners): not measured.
- Replay drift across agent versions: not measured — replay is
  byte-identical because the bench is hermetic.
- Tenant isolation / multi-tenancy: v0.2 work (per ROADMAP).