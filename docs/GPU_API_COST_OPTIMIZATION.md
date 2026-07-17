# AgentSLA Cost-Optimization Plan

Scope: AgentSLA does not need GPUs for its core benchmark. Its expensive path
is live model/API evaluation (`agentsla bench-real`) and optional LLM-judge
classification. This document preserves result quality while minimizing paid
model calls.

## Cost Thesis

AgentSLA's evidence should be built as a three-rung ladder:

1. Hermetic CPU evidence for correctness, regression safety, and figures.
2. Tiny live-LLM smoke evidence for integration truth.
3. Escalated live-LLM evidence only where the prior rung found uncertainty.

Do not use paid live calls to rediscover behavior already proven by hermetic
tests. Use live calls only to validate model-facing assumptions.

## What Must Stay High Quality

- Policy-gate behavior must remain tested against real response text.
- `gate_passed` and `verified_at_truth` must remain separate.
- Every live row must be traceable to a parquet artifact.
- Error/rate-limit rows must stay explicit as `[NOT YET MEASURED]`.
- Hermetic evidence must never be presented as empirical live-agent evidence.

## Frugal Execution Design

### 1. Default to Hermetic Runs

Use this as the normal development gate:

```bash
uv run python -m agentsla bench --seeds 2 --out bench/results/results.parquet
uv run python -m agentsla bench-seeded-errors \
  --trials-per-cell 100 \
  --out bench/results/seeded_errors.parquet \
  --report-section-out bench/results/seeded_errors_section.md
uv run python -m agentsla report --out bench/results/REPORT.md
```

This validates the runtime without paid model calls.

### 2. Use Stratified Micro Live Runs

The live bench should run the smallest balanced set first:

```bash
python -m agentsla bench-real \
  --model <cheap-compatible-model> \
  --tasks-per-domain 1 \
  --seeds 1 \
  --out bench/results/real_llm_smoke.parquet
```

Acceptance for the smoke run:

- all three domains represented
- both naked and wrapped rows emitted
- `verified_at_truth` is populated where fixtures declare ground truth
- report generation includes the Real-LLM section

Only after this passes should the agent run `--tasks-per-domain 3`, then
`--tasks-per-domain 5`.

### 3. Add Adaptive Escalation Instead of Fixed Large Runs

Do not blindly run every live task. Use this policy:

- If smoke run has zero API/schema failures, expand by domain.
- If a domain has stable 100% agreement across two live tasks, stop expanding
  that domain unless it is the headline domain.
- If any domain has failures or high variance, spend calls only there.
- If rate limits appear, stop and keep the artifact; do not retry in a loop.

The goal is to preserve diagnostic power, not row count.

### 4. Cache Live Outputs by Prompt Fingerprint

Before adding more live calls, implement or verify a cache keyed by:

- model id
- prompt text hash
- task id
- seed
- benchmark code version

The cache should store raw assistant text and latency metadata. Regenerating
reports should read cached outputs, not call the API again.

Required invariant:

- changing policy/verifier/report code may reuse cached model text
- changing prompt/model/seed must invalidate the cache

### 5. Keep LLM Judge Off by Default

`StubJudge` remains the default for hermetic and CI runs. Production-like
LLM judge evaluation should be sampled:

- sample at most 10 to 20% of traces
- prioritize traces where heuristics disagree or confidence is low
- log sampled trace ids so the run is reproducible

The classifier quality claim should come from targeted adjudication, not from
paying for a judge on every easy trace.

## Required Code/Doc Changes — Status

1. `--limit-task-ids` or equivalent to target only uncertain tasks — **not yet
   implemented** (optional follow-on; stratified per-domain selection is in).
2. Persistent response cache for `_call_claude` — **implemented**
   (`--cache-dir`, default `bench/cache/real_llm`, keyed by
   `sha256(model_id, task_id, prompt, seed)`; `--resume` reads it).
3. `--dry-plan` mode printing estimated live calls before running —
   **implemented** (zero network, no API key required).
4. README live-bench instructions recommend the ladder (smoke → standard →
   full) — **done** (README § Live bench cost guards).
5. Tests proving cache hits do not invoke the client — **done**
   (`tests/unit/bench/test_real_llm.py::TestResponseCache`).

## Stop Gates

Stop any live run immediately if:

- missing API key
- more than one rate-limit/API error appears
- a schema change causes empty text rows
- the output parquet already exists and the caller did not pass an explicit
  overwrite flag

## Non-Goals

- Do not reduce hermetic seeded-error trials if they run locally in acceptable
  time. They are cheap and valuable.
- Do not replace `verified_at_truth` with subjective judgment.
- Do not buy more live rows merely to make a table look larger.

## Current Project State Fit

Current disk state shows AgentSLA is already structurally frugal:

- `agentsla/bench/harness.py` is hermetic and CPU-only.
- `agentsla/bench/seeded_errors.py` gives strong verifier evidence without
  paid model calls.
- `agentsla/bench/real_llm.py` is the only paid path.
- `agentsla/classify/judge.py` defaults to `StubJudge`, so LLM-judge cost is
  opt-in rather than accidental.
- `bench/results/real_llm.parquet` is treated as the artifact that suppresses
  the honest-gap banner in reports.

The optimization target is therefore not GPU rental. It is live API discipline:
minimize paid calls while keeping one real-agent trace path in the portfolio.

## Local M1 Max and Ollama Role

Use the M1 Max for all local correctness and report-generation work:

```bash
uv run pytest -q tests/unit/bench/test_real_llm.py \
  tests/unit/bench/test_report.py \
  tests/unit/bench/test_seeded_errors.py \
  tests/integration/test_bench_smoke.py
uv run python -m agentsla bench --seeds 2 --out bench/results/results.parquet
uv run python -m agentsla report --out bench/results/REPORT.md
```

Ollama can be useful as a "cheap non-API live-ish model" only if the next
agent adds an OpenAI/Ollama-compatible real-bench adapter. Do not substitute
Ollama numbers for the current `bench-real` Claude-compatible path unless the
schema records `model_id`, provider, and local runtime clearly. For now:

- use Ollama for manual prompt sanity checks
- use hermetic bench for official CPU artifacts
- use the smallest paid Claude-compatible run for official live evidence

Suggested Ollama manual check on the M1 Max:

```bash
ollama serve
ollama pull qwen3:8b
ollama run qwen3:8b "What is 19% of 2.4 million? Answer with the number only."
```

This catches prompt fragility before paid calls, but it is not a replacement
for `real_llm.parquet`.

## Russian-Doll Experiment Plan

Each rung answers one question and unlocks the next rung only if it passes.

### Rung A: Zero-Cost Invariants

Question: does the reliability runtime work without a model?

Run:

```bash
uv run pytest -q tests/unit/bench/test_real_llm.py \
  tests/unit/bench/test_report.py \
  tests/unit/verify/test_numeric.py \
  tests/unit/verify/test_range_claim_extraction.py
```

Cost: `$0`.

Promote only if all tests pass.

### Rung B: Hermetic Full Evidence

Question: are the tables, figures, verification gates, classifier wiring, and
report generation stable?

Run:

```bash
uv run python -m agentsla bench --seeds 2 --out bench/results/results.parquet
uv run python -m agentsla bench-seeded-errors \
  --trials-per-cell 100 \
  --out bench/results/seeded_errors.parquet \
  --report-section-out bench/results/seeded_errors_section.md
uv run python -m agentsla report --out bench/results/REPORT.md
```

Cost: `$0`.

Promote only if report generation succeeds and README/REPORT metrics remain
traceable to parquet.

### Rung C: Paid Smoke, 6 Calls

Question: does the live-model integration path still produce meaningful rows?

Run:

```bash
# Preview first — free, no key needed:
python -m agentsla bench-real --tasks-per-domain 1 --seeds 1 --dry-plan

python -m agentsla bench-real \
  --model <lowest-cost-compatible-model> \
  --tasks-per-domain 1 \
  --seeds 1 \
  --out bench/results/real_llm_smoke.parquet
```

This fits the default `--max-paid-calls 3` cap. Expected calls: 3 prompts total. The parquet emits 6 rows because each response
is recorded as naked and wrapped.

Estimated cost: provider-dependent, usually cents rather than dollars for a
small model because only three short prompts are sent.

Promote only if:

- all three domains produce non-empty text
- no rate-limit rows appear
- report renderer can include the Real-LLM section

### Rung D: Paid Standard, 18 Calls

Question: do results hold across a small balanced corpus?

Run:

```bash
python -m agentsla bench-real \
  --model <lowest-cost-compatible-model> \
  --tasks-per-domain 3 \
  --seeds 1 \
  --max-paid-calls 9 \
  --out bench/results/real_llm_standard.parquet
```

The cap must be raised explicitly — the default (3) refuses this run.

Expected calls: 9 prompts, 18 rows.

Estimated cost: still typically cents to low single-digit dollars depending on
provider and model. Stop here for most portfolio evidence.

### Rung E: Paid Final, 30 Calls Max

Question: do the headline live numbers survive the full intended small corpus?

Run only if Rung D changed a conclusion or the final README requires it:

```bash
python -m agentsla bench-real \
  --model <lowest-cost-compatible-model> \
  --tasks-per-domain 5 \
  --seeds 1 \
  --max-paid-calls 15 \
  --overwrite \
  --out bench/results/real_llm.parquet
```

Expected calls: 15 prompts, 30 rows (12 ground-truthable tasks exist today —
4 per domain — so `--tasks-per-domain 5` currently selects 12 tasks /
12 prompts / 24 rows until the corpus grows). `--overwrite` is required
because `bench/results/real_llm.parquet` already holds the prior artifact.

Hard cap: one run. If it rate-limits or partially fails, keep the honest rows
and stop. Do not rerun blindly.

## Implementation Hooks — Shipped

All five hooks are implemented in `agentsla/bench/real_llm.py`:

1. `--dry-plan`: prints model, task count, prompt count, row count, cache
   hits, estimated paid calls, output path, and whether the output already
   exists. Zero network; no API key required.
2. `--max-paid-calls`: default 3. The run refuses to start (exit 2) when
   planned uncached prompts exceed the cap; raise it explicitly for
   Rung D (9) / Rung E (15).
3. `--cache-dir bench/cache/real_llm` (default): caches raw model responses
   by `sha256(model_id + task_id + prompt + seed)`. Live calls always
   populate the cache; changing model/prompt/seed invalidates the key.
4. `--resume`: serves cached responses with zero paid calls; rows are
   marked `cached=true` in the parquet. A fully cached resume run works
   offline without a key.
5. Fail-fast is the default: the run stops after the first provider/API
   error and keeps the partial parquet (exit 1). `--no-fail-fast` opts out.

Test coverage (`tests/unit/bench/test_real_llm.py`):

- cache hit does not call `_call_claude` (`TestResponseCache`)
- dry plan performs zero network calls and needs no key (`TestDryPlan`)
- existing output path refuses overwrite unless `--overwrite`
  (`TestOverwriteProtection`)
- default cap blocks a 6-prompt run (`TestMaxPaidCalls`)
- fail-fast stops after the first error; `--no-fail-fast` records every
  error row (`TestFailFast`)
- smoke run covers all three domains (stratified selection fixed the
  grouped-corpus slice bug that made `--tasks-per-domain 1` all-finops)

## Cost Accounting Template

Every paid run should leave this note near the artifact:

```text
model:
provider:
date_utc:
tasks_per_domain:
seeds:
prompts_sent:
rows_written:
input_tokens_est:
output_tokens_est:
provider_cost_usd:
artifact:
reason_this_run_was_needed:
```

