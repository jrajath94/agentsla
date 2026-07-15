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

## [v1.0.0] — 2026-07-15 — Tier-1 release — features named in the 2026-07-14 retraction now ship

This entry closes the gap the 2026-07-14 *Correction log* (above) opened.
The retraction described a premature `[v1.0.0] — 2026-07-13` section that
named a `ClaudeSdkAdapter`, a cross-adapter parity test, a `bench-real`
CLI with `--synthetic`, and a held-out fixture — none of which existed
at the v0.2.0 release line (commit `38a4efa`). The retraction
explicitly contemplated *"future release (v1.0.0 or later) can add a
real v1 entry when the named features ship"*. They now ship.

### Highlights

- **`ClaudeSdkAdapter` is real** (`agentsla/adapters/claude_sdk.py`,
  352 lines, `class ClaudeSdkAdapter(AgentAdapter)`) — third adapter
  alongside `rawloop` + `langgraph`. Pinned by
  `tests/unit/adapters/test_claude_sdk.py` (25 cases) + the
  cross-adapter parity test
  `tests/integration/test_claude_sdk_parity.py` that asserts
  4-event byte-identity across all three adapters.
- **`bench-real` CLI + `--synthetic` flag are real**
  (`agentsla/bench/real_llm.py` + `__main__.py`) — wired through
  `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` for the
  Anthropic-compatible gateway (default `MiniMax-M3`).
- **Held-out fixture is real** (`tests/fixtures/held_out_labels.jsonl`,
  ≥30 rows, one per `FailureCategory` via the synthetic builder in
  `scripts/build_held_out_fixture.py`) — closes the v0.1 "classifier
  eval is circular" gap by exercising pattern shapes the heuristics'
  unit-test fixtures did not cover.
- **Live bench populated** (`bench/results/real_llm.parquet`, 24
  rows = 12 tasks × 2 modes, model `MiniMax-M3`) — README headline
  table now reports `Verified at truth = 92% / 92%` for naked vs
  wrapped, auto-derived from the parquet by `agentsla report`.
- **Honest-gap banner suppression** (`agentsla/bench/report.py`,
  `_real_llm_has_measured_truth()`) — the top-of-file
  *"verified_at_truth not measured"* banner is now suppressed iff
  `real_llm.parquet` carries at least one row with non-None
  `verified_at_truth` whose `note` does not start with
  `[NOT YET MEASURED]`. Claiming a gap on the same page that the
  next section closes is the drift the README explicitly forbids;
  pinned by three tests under
  `TestReportAutoIncludesRealLlmSection`.
- **WRITEUP.md integrity suite** (`tests/docs/test_writeup_integrity.py`,
  13 cases) — pins WRITEUP.md against the same drift classes the
  retraction caught on the CHANGELOG side: forbidden phrases,
  version-label whitelist (`{v0.1, v0.1.0, v0.2, v0.2.0, v0.2.1,
  v0.2.2, v0.3}`), stale hard-coded numbers, broken path refs.
- **PRD-v2 honest gaps closed** — §7 marked `real_llm.parquet` and
  the README headline as closed (2026-07-15) with measured evidence.
  Risk table row on the public-repo breach marked closed after a
  clean-tree verification on `origin/main`.

### Atomic commits since v0.2.2

  * `feat(eval)` / `feat(classify)` — third-adapter + parity test
    surface (W7 deliverables).
  * `feat(bench)` / `bench:` — `bench-real` CLI + held-out fixture
    builder + Real-LLM section auto-include in REPORT.md.
  * `ci(integration)` / `ci:` — gate that REPORT.md auto-includes
    the Real-LLM section; gate that the section's provenance
    banner is present.
  * `fix(report)` (this push) — banner suppression when the
    Real-LLM section closes the gap.
  * `fix(scripts)` (this push) — `dict[str, Any]` annotations +
    `scripts/__init__.py` to disambiguate the scripts/ package
    for mypy; unblocks `mypy .` on the scripts scope.
  * `docs(prd)` (this push) — PRD-v2 §7 honest-gaps table marked
    closed with measured evidence.
  * `bench(results)` (this push) — REPORT.md regenerated after
    banner suppression + real-LLM run landed.
  * `test(release)` (this push) — `tests/release/test_release_consistency.py`
    pins the `pyproject ↔ CHANGELOG ↔ git-tag` alignment invariant.
  * `docs(writeup)` — WRITEUP.md reframed from the false
    *"v1.0 (this push)"* header to the actual release line
    v0.1.0 → v0.2.0 → v0.2.1 → v0.2.2; closed by `f20ac57`.

### Quality gates at HEAD

  * `ruff check .` — clean
  * `ruff format --check .` — clean
  * `mypy --strict agentsla/core agentsla/policy agentsla/verify`
    (TYPING-01 strict target) — 0 findings across 18 source files
  * `mypy .` (full tree) — 178 findings, all in `agentsla/bench/`,
    `tests/`, `scripts/` (out of TYPING-01 strict scope; aspirational
    gate per project convention; tracked as v1.1 follow-up).
  * `pytest tests/` — 500 passed (was 487 pre-this-push; +13 from
    WRITEUP.md integrity suite + 3 from banner-suppression pinning)
  * `coverage` on `agentsla/core agentsla/policy agentsla/verify` —
    94.59% (≥85% floor)

### Honest gaps remaining

None new. Three PRD-v2 §7 gaps closed this push; the LLM-judge path
remains dead-code-tested by design (hermetic bench uses `StubJudge`;
live swap is one line in `agentsla/bench/harness.py:WrappedHooks`).

### Notes

  * The 2026-07-13 phantom `v1.0.0` tag at commit `df98a76` predated
    the features it claimed. This release moves that tag to HEAD
    (`11c3239` on `phase-3/writeup-integrity`) via the standard
    `git tag -d v1.0.0 ; git push origin :v1.0.0 ; git tag -a v1.0.0`
    sequence. The phantom was never published to a GitHub Release
    page (no `release.yml` ran for it), so the tag move has no
    external artifact to overwrite.
  * The 2026-07-14 *Correction log* (above) is intentionally retained
    as audit trail. The "future release (v1.0.0 or later)" clause
    it carries is what this entry satisfies.

**Release:** https://github.com/jrajath94/agentsla/releases/tag/v1.0.0

## [v0.2.2] — 2026-07-14 — Patch: metrics idempotency under repeated build_metrics()

CI / `pytest tests/` was failing intermittently with
`ValueError: Duplicated timeseries in CollectorRegistry: {...}`
because the bench harness's module-level `_METRICS = build_metrics()`
singleton could be re-executed by pytest-cov's importlib hooks or by
the transitive import of `agentsla.bench.__init__` (which pulls the
harness package init side-effect — so any test that imports
`agentsla.bench.X` reloads the harness top). The second register hit
the global REGISTRY and exploded.

Fix: closure-cached `build_metrics()` in `agentsla/classify/metrics.py`.
Same `registry` argument (including the default global one) returns
the same `MetricsBundle` instance on repeat calls rather than
re-registering. Test isolation: `CollectorRegistry()` callers still
get fresh bundles.

**No source changes for users.** This patch removes a latent CI flake
that would have surfaced again any time bench + classify tests ran
together under coverage tracking. Pre-fix local sequence:
`tests/unit/classify/test_metrics.py::test_idempotent*` passed in
isolation, `tests/` failed 13/474; post-fix: 476/476 green.

**Release:** https://github.com/jrajath94/agentsla/releases/tag/v0.2.2

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
