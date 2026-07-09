# Changelog

All notable changes to AgentSLA are recorded here. Dates are UTC.

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