# Changelog

All notable changes to AgentSLA are recorded here. Dates are UTC.

## Correction log (2026-07-14)

Two earlier CHANGELOG entries are retracted as of 2026-07-14.

### Retracted: `[v1.0.0] — 2026-07-13` section

This section described a "ClaudeSdkAdapter", a cross-adapter parity test
in `tests/integration/test_claude_sdk_parity.py`, and a `bench-real`
CLI with a `--synthetic` flag — none of which existed in the v0.2.0
release (commit `38a4efa`). The numbers cited ("432 tests pass", "v1
changelog highlights", "Cross-adapter parity test enforces 4-event
byte-identity across all three adapters") were not reproducible from
`bench/results/`.

Per CLAUDE.md integrity baseline — *"No fabricated features. Document
what exists, not roadmap"* and *"No fabricated numbers. Everything from
benchmarks or marked [NOT YET MEASURED]"* — the entry is deleted from
this changelog. This correction log is the audit trail; future release
(v1.0.0 or later) can add a real v1 entry when the named features ship.

### Retracted: `[v0.2.0] — in development` section

This placeholder duplicated content already absorbed into the dated
`[v0.2.0] — 2026-07-14` entry below; the table of "atomic commits" it
contained also referenced fixups from before the CHANGELOG was
reorganized. Deleted; the dated entry is authoritative.

The currently shipped release is `[v0.2.0] — 2026-07-14` (this entry
below). v1.0.0 is deferred until the features in the retracted section
actually exist in the source tree.

## [v0.2.1] — 2026-07-14 — Patch: fix wheel entry point

Re-release of the v0.2.0 wheel with the `agentsla` console_script
fixed. v0.2.0 was the first release and shipped a wheel whose
`[project.scripts]` entry pointed at `agentsla.cli:app`, but
`app` is not defined anywhere in `agentsla.cli.__init__`; the
actual dispatcher is `agentsla.__main__:main`. A fresh venv install
of the v0.2.0 wheel would import the package but the `agentsla`
console command would fail with `ImportError: cannot import name
'app' from 'agentsla.cli'`. v0.2.1 fixes the entry point and
rebuilds the wheel.

**No source changes between v0.2.0 and v0.2.1 — this is a pure
release-process patch.** The git tag for v0.2.1 points at a commit
that adds this CHANGELOG entry + bumps `pyproject.toml` to 0.2.1.

**Release:** https://github.com/jrajath94/agentsla/releases/tag/v0.2.1

## [v0.2.0] — 2026-07-14 — Hiring-signal push

**Release:** https://github.com/jrajath94/agentsla/releases/tag/v0.2.0

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
  `release.yml` (build sdist+wheel + GitHub Release page; PyPI publish
  step dropped because the trusted publisher is not yet configured —
  see commit `38a4efa`).
- **Console script fix** (`pyproject.toml`) — entry point corrected
  from `agentsla.cli:app` (undefined) to `agentsla.__main__:main` so
  `pip install agentsla` produces a working `agentsla` CLI.
- **Repo URL fix** (`pyproject.toml`) — `[project.urls]` Repository +
  Issues retargeted from the stale `anthropic-research/agentsla`
  placeholder to the actual repo at `jrajath94/agentsla`.

### Notes

- All numbers in the headline tables are traceable to a parquet in
  `bench/results/` (parquet files are gitignored; reproduced locally
  via `agentsla bench` and `agentsla bench-real`).
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
