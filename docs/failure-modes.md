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
from live Claude API calls (the recording path —
`TraceWriter` — is the same; the harness just needs a real client).
v0.2 work; not in v0.1.

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

---

# v1 additions — what we learned shipping the third adapter

The v1 push landed the ClaudeSdkAdapter, the real-LLM bench harness,
the per-endpoint range-claim parser, and the per-verifier tolerance
config. Each surfaced a new failure mode that v0.1's 6-mode list did
not name. Below are the additional modes (7-15) we now know about,
with the same format as above: trigger, why it breaks, observable
signal, v1 status, mitigation.

## 7. Adapter parity drift

**Trigger**: The Claude SDK adapter, LangGraph adapter, and RawLoop
adapter produce different event-kind sequences for the same task.

**Why it breaks**: The cross-adapter parity test
(`tests/integration/test_claude_sdk_parity.py`) asserts byte-identical
event sequences (modulo UUIDs) for an echo task. If one adapter
inserts a spurious `model_message` between `tool_call` and
`tool_result`, the policy gate sees an event the verifier does not
expect, and the Verdict emit fails silently.

**Observable signal**: `tests/integration/test_claude_sdk_parity.py`
test failure on event-sequence mismatch; or the bench's
`parity.parquet` shows diverging `event_count` across adapters.

**v1 status**: Mitigated. The parity test runs in CI. The
RawLoopAdapter, LangGraphAdapter, and ClaudeSdkAdapter all emit the
4-event shape (`model_message(user)` → `tool_call` →
`tool_result` → `model_message(assistant)`).

**Mitigation**: Keep the parity test under
`tests/integration/`. Add a 4th adapter (e.g., a LiteLLMAdapter) only
with an accompanying parity test update.

---

## 8. Per-endpoint range multiplier mismatch

**Trigger**: A real P&L trace contains `$4.2M-$4.5M` (per-endpoint
multiplier). v0.1's `_RANGE_PATTERN` matched the span but silently
dropped the second endpoint's `M`, parsing it as `(4.2, 4.5)` —
inflating unverifiable coverage because no tool result ever matches
4.5 raw dollars.

**Why it breaks**: The range claim's `[low, high]` is the interval
the verifier checks the source value against. A mismatched interval
either accepts wrong answers (when the true range is wider) or
rejects correct ones (when the parsed interval is narrower than
intended). Either failure surfaces as a false positive or false
negative on the verdict.

**Observable signal**: `verify.verified_at_truth=false` for tasks
whose answer matches a clearly-stated P&L range. Trace-level
investigation shows the range was parsed with the wrong scale.

**v1 status**: Mitigated. `_RANGE_PATTERN` now accepts K/M/B/% on
both endpoints. Tests in `tests/unit/verify/test_range_claim_extraction.py`
pin the contract (5 tests including `$4.2M-$4.5M` → `(4_200_000,
4_500_000)`).

**Mitigation**: Keep the test coverage in
`tests/unit/verify/test_range_claim_extraction.py`. Add a fuzz test
that ranges over (low, high, multiplier-endpoint) tuples and asserts
the parsed `(low, high)` matches the expected.

---

## 9. Real-LLM bench rate-limit exhaustion

**Trigger**: The real-LLM bench (`python -m agentsla bench-real`) hits
Claude's rate limit mid-run. v0.1 had no real-LLM bench; v1 added one
(the ground-truthable corpus is 12 tasks — 4 per domain — so the
largest single-seed run is 12 prompts / 24 rows).

**Why it breaks**: Without graceful degradation, a rate-limit error
aborts the run and writes zero rows — and naive retries burn more paid
calls against an already-limited key.

**Current status**: Mitigated twice over. Every `_call_claude`
exception becomes a row tagged `[NOT YET MEASURED] <exc>` with
`success=false`, so the parquet stays honest. Fail-fast is the
default: the run stops after the FIRST provider error, keeps the
partial parquet, and exits 1 (`--no-fail-fast` opts back into
record-and-continue). `--max-paid-calls` (default 3) bounds the blast
radius of any retry loop.

**Observable signal**: `python -m agentsla bench-real` prints
`[NOT YET MEASURED] rows: N/M` plus a fail-fast notice. Re-runs with
`--resume` serve already-answered prompts from `bench/cache/real_llm`
at zero paid cost and only pay for the missing ones.

**Mitigation**: For production deployments, switch to batched API
calls (`anthropic.Anthropic.messages.batch.create`) which has higher
throughput and a separate rate-limit pool. Out of scope for v1.

---

## 10. Held-out fixture circularity regression

**Trigger**: A new classifier trigger is added whose training data
overlaps with the held-out fixture in `scripts/build_held_out_fixture.py`.
The eval agreement metric returns to ceiling.

**Why it breaks**: The whole point of the held-out fixture (PRD-v1
F4) is to evaluate the classifier against patterns the heuristics
were NOT tuned against. If a new trigger is tuned against the
held-out categories, the eval loses its signal value.

**Observable signal**: `python -m agentsla eval-classifier` reports
≥95% agreement against the held-out set on categories the new trigger
was tuned for.

**v1 status**: Mitigated for v1 by pinning the fixture's row
generators in `scripts/build_held_out_fixture.py` and tagging each
row with its generator name (e.g., `gen_reasoning_error` → task_id
`finops-heldout-1`). Any future trigger addition must (a) add a new
generator with a NEW category, and (b) update the held-out fixture
to include examples from that NEW category only.

**Mitigation**: Document the rule in `agentsla/classify/` README. Add
a CI check that grep's for `train_*` references in
`scripts/build_held_out_fixture.py` and fails the build if any
generator's category overlaps with the trigger training set.

---

## 11. Per-verifier tolerance drift

**Trigger**: Two verifiers in the same `VerificationChain` use
different `tolerance` values, and one verifier's float-rounding
artifact falls inside its tolerance but outside another's.

**Why it breaks**: The chain's pass criterion is
`incorrect == 0 AND coverage >= threshold` — the same criterion for
every verifier. But the per-verifier `tolerance` controls whether a
claim is `correct` or `incorrect`. A chain mixing `1e-6` and `1e-3`
verifiers can report `incorrect > 0` on a claim one verifier would
have passed.

**v1 status**: Documented. Per-verifier tolerance is now a public
API surface (`NumericVerifier(tolerance=...)`) with a regression
test in `tests/unit/verify/test_numeric_tolerance_config.py`. The
chain does NOT enforce uniform tolerance — operators choose per
domain (financial ops at 1e-9, doc-QA at 1e-3).

**Mitigation**: Add a `VerificationChain(consensus_tolerance=...)`
kwarg that requires all verifiers' tolerances to be within
`consensus_tolerance` of each other, raising at construction time.
v0.2 work.

---

## 12. Real-LLM fixture degradation to synthetic

**Trigger**: `build_real_held_out_fixture()` is called without
`ANTHROPIC_API_KEY` and `synthetic_fallback=True` (default). The
fixture silently becomes synthetic, but the eval report still
labels it "REAL."

**Why it breaks**: The eval's REAL/SYNTHETIC provenance flag is the
operator's signal for whether the eval is honest. If the fixture
silently degrades without flagging it, the eval report misleads.

**v1 status**: Mitigated by the per-row `synthetic` field. The eval
CLI's report aggregates `synthetic=true` rows separately. But: the
default behavior of falling back without warning is still surprising
to first-time users.

**Mitigation**: Add a stderr warning when `synthetic_fallback=True`
triggers, naming the row count that was demoted. v0.2 work.

---

## 13. CLI subcommand collision

**Trigger**: Adding a new CLI subcommand (`bench-real`) without
checking the existing dispatch table.

**Why it breaks**: `python -m agentsla <new-cmd>` falls into the
default "unknown subcommand" branch and exits 1 with a non-obvious
error. The README says the subcommand exists; the CLI rejects it.

**v1 status**: Mitigated. `agentsla/__main__.py` now dispatches
`bench-real`. The dispatch table is the single source of truth;
adding a new subcommand requires editing both `__main__.py` and
`agentsla/bench/__init__.py`.

**Mitigation**: Move the dispatch to a `dict[str, Callable]` table
to make the registration explicit and grep-able. v0.2 work.

---

## 14. Generated artifacts in repo

**Trigger**: `bench/results/figures/*.png` and `eval_classifier.md`
are regeneratable from parquet via `python -m agentsla report` and
`python -m agentsla eval-classifier`. Committing them bloats the repo
and creates stale-data risk when the source parquet changes without
the artifacts being regenerated.

**Why it breaks**: A reviewer reading `REPORT.md` sees a number
that's stale relative to the latest `results.parquet`. The
"byte-identical table from parquet" contract is violated by stale
committed artifacts.

**v1 status**: Accepted. The figures and eval_classifier.md ARE
committed as a snapshot of the v1 push. They are marked as
regeneratable in their header comments.

**Mitigation**: Add a `make report` target that regenerates every
artifact in `bench/results/` and a CI check that runs `make report`
and fails if the committed artifact differs. v0.2 work.

---

## 15. Tool-call id collisions under concurrent adapters

**Trigger**: Two adapters (e.g., ClaudeSdkAdapter + a hypothetical
parallel execution path) generate `call_id` values from the same
`uuid4()` namespace and the trace store's `UNIQUE(call_id)` constraint
fires.

**Why it breaks**: A `uuid4()` collision is astronomically unlikely,
but if it happens, the second `tool_call` event with the duplicate
id fails the `UNIQUE` constraint and the trace write aborts. The
trace is left in a half-written state.

**v1 status**: Out of scope. The shipped adapters are
single-threaded. The bench harness runs one adapter per task per
seed. There is no concurrent adapter path in v1.

**Mitigation**: If a concurrent adapter path is added, generate
`call_id` with a per-adapter prefix (e.g., `claude_` + uuid4 hex) so
the trace store's UNIQUE constraint cannot fire across adapters.
v0.2 work.