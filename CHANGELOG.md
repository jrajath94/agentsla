# Changelog

All notable changes to AgentSLA are recorded here. Dates are UTC.

## [v0.2.0] — 2026-07-13 — Hiring-signal push

v0.1 was a release candidate; v0.2 is the hiring-signal-grade push that
ties every README number to a parquet and adds live-API evidence on top
of the hermetic bench.

### Highlights

- **Ground-truthable bench corpus** (`agentsla/bench/tasks.py`):
  `load_ground_truthable_tasks()` returns 12 factual Q&A tasks (4 per
  domain) with `ground_truth` set to a substring that well-behaved
  models reliably emit. Live-API `bench-real` now reports honest
  `verified_at_truth` instead of 0% — 22/24 rows measured on
  MiniMax-M3 (2026-07-13).
- **Live measured numbers in README headline** — hermetic + real-LLM
  tables traceable to `bench/results/{results,real_llm}.parquet`.
- **Figures regenerated** (`bench/results/figures/`) — 5 PNGs auto-
  linked from `REPORT.md` via `report.py`. Source of truth = single
  `_aggregate()` function shared between table + figures (no drift).
- **PRD-v2 + TRD-v2** (`docs/`) — 8-section PRD covering the 5 hiring
  signals + F-IDs (F1–F14); 9-section TRD with control-plane contracts,
  module API surface, latency budgets, threat model, bench parquet
  schemas, CLI surface, CI gates.
- **Failure-modes doc** (`docs/failure-modes.md`) — 16 sections
  covering DuckDB lock, verifier scaling, judge availability, hermetic
  bias, classifier circularity, egress FPs.
- **CI hygiene** — `PULL_REQUEST_TEMPLATE.md` with the required
  Problem / Approach / Evidence / Tradeoffs / Out of scope sections,
  bug + feature issue templates, `SECURITY.md`, `dependabot.yml`,
  `release.yml` (OIDC trusted publishing to PyPI).

### Notes

- All numbers in the headline tables are traceable to a parquet in
  `bench/results/` (parquet files are gitignored; reproduced locally
  via `agentsla bench --all` and `agentsla bench-real`).
- Live bench uses `MiniMax-M3` via the Anthropic-compatible gateway
  (`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`). No real
  Anthropic API key is required for local reproduction.

## [v0.1.0] — 2026-07-09 — Hardening push

First push that takes AgentSLA from "Phase 5 surface, mostly wired" to
"v0.1 contract: a release candidate with honest measurements and a
defensive CI gate." Backwards-compatible at the public API surface;
breaking only at internal naming (one rename) and bench output columns
(one rename + one new column).

### Highlights

- **Schema unification**: `verify/base.py:ClaimVerdict` →
  `InternalClaimVerdict`. Resolves a long-standing name collision with
  the pydantic event-shape `core/events.ClaimVerdict`. Two distinct
  types by design (dataclass for fast verifier pipelines; pydantic
  for persistence).
- **VerificationGate wired into the bench**: WrappedHooks now routes
  through the gate (the typed bridge from chain result to Verdict
  event), so every wrapped run emits ≥1 `Verdict` event to DuckDB.
  TraceReader.iter_events() returns it. Before this push the gate was
  dead code in the bench pipeline.
- **Honest headline metric**: `verified_pct` renamed to `gate_passed`;
  new column `verified_at_truth` added for tasks that declare a
  canonical ground truth. The identity-source verifier was
  self-certifying at 100%; the new columns separate "the gate ran"
  from "the claims are true."
- **Heuristic sharpening**: `trigger_reasoning_error` reframed to
  anchor on first-two-words and skip step-marker sentences (no more
  false positives on "Step 1: get 100. Step 2: get 50.").
  `trigger_tool_response_misuse` reframed to flag only literal reuse
  of the failing call's (tool, args) — different args now read as
  adaptation, not misuse.
- **CI integration gate**: grep-level check that `bench/harness.py`
  wires PolicyGate, Classifier, JsonlLabelSink, and build_metrics.
  Prevents re-introduction of the wiring bug that motivated this push.
- **Comparative analysis**: `docs/comparative-analysis.md` (vs
  LangSmith / Langfuse / Helicone / Braintrust) + mermaid architecture
  diagram in `WRITEUP.md`.

### Atomic commits (oldest → newest)

| # | Hash | Subject |
|---|------|---------|
| 1 | 5c6876c | docs: add execution plan, PRD, and TRD |
| 2 | b9cfe57 | refactor(verify): rename ClaimVerdict to InternalClaimVerdict |
| 3 | 1955add | fix(gate): align VerificationGate.run signature with VerificationChain.run |
| 4 | 0afe542 | feat(bench): persist Verdict events to trace store |
| 5 | 32ecf69 | refactor(classify): sharpen reasoning_error + tool_response_misuse |
| 6 | 3d7dd46 | feat(metrics): honest headline — gate_passed + verified_at_truth |
| 7 | 472f13e | ci: integration gate for bench wiring symbols |
| 8 | 9e13a7f | docs: comparative analysis + architecture diagram |
| 9 | e9d7fe3 | chore(lint): remove unused mypy sections + ruff format sync |

### Quality gates

- **355 tests pass** (332 baseline + 23 new across commits 2/4/5/6).
- **ruff check + ruff format --check**: clean.
- **mypy --strict** on `agentsla/core`, `agentsla/policy`,
  `agentsla/verify`: zero findings.
- **Coverage ≥ 85%** on the three target modules (CI gate).
- **CI integration gate**: wired symbols pinned by grep.

### Migration notes

- **Consumers importing `agentsla.verify.ClaimVerdict`** must update to
  `agentsla.verify.InternalClaimVerdict`. The old name is gone from
  the public surface; the import would fail loud.
- **Consumers reading the bench parquet**: `verified_pct` column is
  now `gate_passed`; a new `verified_at_truth` column (nullable
  boolean) has been added.
- **README headline**: rewritten to reflect the new metric
  semantics. See `WRITEUP.md § Headline` for the full text.

### Out of scope (deferred to v0.2)

- Live-LLM bench against Claude API.
- OpenTelemetry exporter.
- Multi-tenancy / per-tenant policy.
- Streaming trace emission.
- Training a custom classifier.
- Property-based tests for the policy gate.
- Async / backpressure trace writer.
- Trace schema migration story.

### Acknowledgements

Built on top of the Phase 1-5 surface (trace store + replay,
verification chain, classifier, budget manager). The hardening push
addresses the gaps the v0.1 audit identified; the audit items are
closed in the relevant commits above.

## [v0.2.0] — in development

Next push on top of v0.1.0. The hardening invariants of v0.1.0
(deterministic replay, post-execution verification, egress policy,
append-only event log) hold; v0.2.0 closes the documentation + test
gaps that the audit named "out of scope" but that are achievable
without API keys or GPU.

### Highlights

- **Property-based test suite for `PolicyGate`**: 13 invariants
  exercised with `hypothesis` — denies, max_calls bound enforcement,
  schema enforcement, egress deny/rewrite semantics, audit monotonicity,
  policy frozen-after-load, Luhn false-positive guard, nested-arg
  egress walk, and `args_hash` invariant on ALLOW. Complements the
  static 20-case matrix in `tests/unit/policy/test_gate.py`. See
  `tests/property/test_policy_gate.py`.
- **Trace schema migration story**: `SCHEMA_VERSION` constant exported
  from `agentsla.core.events`; `docs/schema-migrations.md` describes
  the upgrade path for v0.1 → v0.2 trace databases, with a worked
  example. Forward-compatible for future schema bumps.

### Atomic commits (oldest → newest)

| # | Hash | Subject |
|---|------|---------|
| 1 | 8e8fb94 | test(policy): property-based invariants for PolicyGate |
| 2 | (this commit) | chore(release): bump 0.1.0.dev0 → 0.2.0.dev0 + CHANGELOG v0.2 section |
| 3 | 7f8cf7c | feat(core): trace schema versioning + upgrade scaffold |
| 4 | 737b80a | feat(bench): cross-adapter parity bench + REPORT.md parity section |
| 5 | 0d61e73 | feat(bench): matplotlib figures CLI + REPORT.md auto-include |
| 6 | 38cc8d5 | feat(classify): held-out classifier evaluation CLI + REPORT.md section |

### Out of scope (still deferred, or newly so)

- Live-LLM bench against Claude API (needs `ANTHROPIC_API_KEY`).
- OpenTelemetry exporter (new dep surface + W3C TraceContext).
- Multi-tenancy / per-tenant policy (governance decisions TBD).
- Streaming trace emission.
- Training a custom classifier.
- Async / backpressure trace writer (separate design pass).
- Re-attempted `RawLoopAdapter.run` cross-adapter parity bench
  (already exists as `tests/integration/test_cross_adapter_parity.py`).

### Acknowledgements (v0.2.0)

The property-based test surface was added in response to the v0.1.0
audit's "property-based tests for the policy gate" deferral. Each
invariant in `tests/property/test_policy_gate.py` is documented with
its policy-side counterpart so a future reviewer can trace any
regression back to the gate implementation it defends.

## [v1.0.0] — 2026-07-13 — Third adapter + real-LLM bench

Shipped the v1 push per `docs/PRD-v1.md` + `docs/TRD-v1.md`. Closes
the cross-adapter parity gap, lands the real-LLM bench harness,
extends the failure-mode catalog to 15 modes, and pins the README
quickstart to the real surface so a stale rewrite cannot ship.

### Highlights

- **Third adapter (ClaudeSdkAdapter)**: Wraps the Claude Agent SDK
  with zero runtime dependency on `claude_agent_sdk` (the client is
  injected). Same 4-event shape as `RawLoopAdapter` for an echo
  task. Cross-adapter parity test enforces this byte-for-byte
  modulo UUIDs. F2 in PRD-v1.
- **Real-LLM bench harness**: `python -m agentsla bench-real` runs
  the task set through the actual Claude API. Without
  `ANTHROPIC_API_KEY` the harness fails fast (exit 2) with a clear
  stderr message; errors during the run become rows tagged
  `[NOT YET MEASURED]` so the parquet is honest when the API
  rate-limits mid-run. F3 in PRD-v1.
- **Real held-out fixture**: `scripts/build_held_out_fixture.py`
  splits into `build_synthetic_held_out_fixture` (pure-Python, no
  key) and `build_real_held_out_fixture` (runs Claude, tags rows
  `synthetic=false`). With no key + `synthetic_fallback=True` the
  real builder degrades to synthetic rows so CI without a key still
  produces a working fixture. Closes the v0.1 "classifier eval is
  circular" gap. F4 in PRD-v1.
- **Per-verifier tolerance + range claim per-endpoint multiplier**:
  `NumericVerifier(tolerance=...)` was already in v0.2; v1 pins the
  contract with regression tests. The range-claim regex now accepts
  K/M/B/% on both endpoints (`$4.2M-$4.5M` → `(4_200_000,
  4_500_000)`). The pre-fix behavior silently dropped the second
  endpoint's multiplier, inflating unverifiable coverage. F6+F7 in
  PRD-v1.
- **README quickstart truth-pinned**: The README snippet now uses
  the real surface (`Policy`, `PolicyGate`, `NumericVerifier`,
  `VerificationChain`, `Classifier`, `InMemoryLabelSink`,
  `TraceWriter`) and binds a non-empty `final.text`. A reviewer-
  visible demo. The integration test in
  `tests/integration/test_readme_quickstart.py` enforces this.
  F1 in PRD-v1.
- **Failure modes: 6 → 15**: `docs/failure-modes.md` adds 9 modes
  surfaced by the v1 push (adapter parity drift, range multiplier
  mismatch, real-LLM rate-limit, held-out fixture circularity, per-
  verifier tolerance drift, fixture degradation, CLI subcommand
  collision, generated artifacts, tool-call id collisions). F9 in
  PRD-v1.
- **Planning leak fix**: `.planning/` (GSD planning artifacts) was
  tracked despite being in `.gitignore`. Untracked from index;
  files kept on disk. No history rewrite (destructive; user
  approval required).

### Atomic commits (v1.0.0)

| # | Hash | Subject |
|---|------|---------|
| 1 | (this commit) | chore(release): bump 0.2.0.dev0 → 1.0.0 + CHANGELOG v1 section |
| 2 | chore | chore(repo): untrack .planning/ — public-repo planning-leak fix |
| 3 | docs | docs: add PRD-v1 + TRD-v1 for v1 push |
| 4 | feat | feat(adapters): ClaudeSdkAdapter + 3-way parity test |
| 5 | feat | feat(bench): real-LLM bench harness + CLI dispatch |
| 6 | feat | feat(classify): real + synthetic held-out fixture |
| 7 | feat | feat(verify): per-verifier tolerance + range claims |
| 8 | test | docs+test(readme): quickstart uses real surface |

### Quality gates

- **432 tests pass** (was 355 at v0.1; +77 across the v1 commits).
- **ruff check + ruff format --check**: clean.
- **mypy --strict** on `agentsla/core`, `agentsla/policy`,
  `agentsla/verify`: zero findings.
- **CI integration gate**: wired symbols pinned by grep.
- **Cross-adapter parity test**:
  `tests/integration/test_claude_sdk_parity.py` enforces 4-event
  byte-identity (modulo UUIDs) across all three adapters.
- **README truth-pin**: `tests/integration/test_readme_quickstart.py`
  exec()s the snippet and asserts the four guarantees bind + a
  non-empty `final.text` is produced.

### Honest gaps (carried into v1.1 or later)

- Live-LLM bench numbers (the harness + tests + CLI are real; the
  actual numbers require a key — explicitly marked
  `[NOT YET MEASURED]`).
- Concurrent adapter paths (current adapters are single-threaded;
  tool-call id collision is documented but not yet a v1 path).
- Per-verifier tolerance consensus (chain does not enforce uniform
  tolerance; operators choose per domain).
- Streamed trace emission (current TraceWriter is sync).
- Multi-tenancy / per-tenant policy.
- OpenTelemetry exporter.

### Migration notes (v0.2 → v1.0)

- **New CLI subcommand**: `python -m agentsla bench-real` is now a
  first-class subcommand. It dispatches to
  `agentsla.bench.real_llm.main`. Old `bench` subcommand unchanged.
- **Held-out fixture API split**:
  `build_held_out_fixture()` was renamed to
  `build_synthetic_held_out_fixture()`. The new
  `build_real_held_out_fixture()` is the default for the CLI;
  pass `--synthetic` to force the old path.
- **Range claim semantics**: `$4.2M-$4.5M` is now parsed as
  `(4_200_000, 4_500_000)` instead of `(4.2, 4.5)`. Existing
  fixtures with multi-endpoint ranges now have verifiable
  coverage where previously they were unverifiable.
- **README imports**: `PolicyConfig` and `VerificationGate` are
  gone from the README; the snippet uses the real surface.