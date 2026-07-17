# AgentSLA — Product Requirements Document (v1 — Claude SDK + Real-LLM bench)

**Author:** TDD-driven red/green execution
**For:** Anthropic Staff SWE candidacy (Tier-1 project) · Hiring signals: scale-with-SLOs · tradeoff narratives · control-plane ownership · failure-mode literacy · verifiable artifacts
**Supersedes:** `docs/PRD.md` (v0.1 hardening — closed); `docs/TRD.md` (v0.1 hardening — closed); `docs/EXECUTION.md` (all 12 DoD items shipped at v0.2 close).
**Date:** 2026-07-13

---

## 0. Thesis (the one-liner you defend cold)

AgentSLA is an **SLO-aware reliability runtime** that wraps any tool-calling LLM agent (Claude Agent SDK, LangGraph, raw loop) with **four hard guarantees**:

1. **Policy enforcement** — every tool call passes a declarative YAML policy (allowed tools, JSON-Schema validation, per-tool/per-trace call caps, egress regex pack) before execution.
2. **Post-generation verification** — every numeric claim in the final answer is recomputed against source tool results; the gate emits a `Verdict` event with `coverage` (fraction of claims checked) and `incorrect` count.
3. **Structural replay** — every run is captured as an append-only event log; `agentsla replay <trace_id>` re-validates recorded tool-call hashes and returns the recorded final answer, converting trace drift into a reproducible audit signal. Adapter-driven re-execution with stubbed tool results is not shipped.
4. **Failure attribution** — every failed trace is labeled with one of 14 categories via a two-stage classifier (heuristic → LLM judge) and emitted as a Prometheus counter.

It is the **reliability layer for agents** — the thing that turns "agent demoed well" into "agent runs in production with an SLA."

---

## 1. Current state — what v0.2 left on the table

| Capability | v0.2 status | Tier-1 ready? |
|---|---|---|
| 4 guarantees (policy / verify / replay / classifier) wired E2E | ✓ shipped | yes |
| Cross-adapter parity bench (rawloop vs langgraph, 30 paired runs) | ✓ shipped | partial — Claude SDK absent |
| Held-out classifier eval (36 synthetic traces, 100% agreement) | ✓ shipped | **no** — fixture is synthetic, eval is circular |
| Honest headline (`gate_passed`, `verified_at_truth`) | ✓ shipped | yes |
| 5 matplotlib figures + REPORT.md | ✓ shipped | yes |
| CI integration gate (grep PolicyGate + Classifier + sink + metrics) | ✓ shipped | yes |
| Failure-modes postmortem (6 modes) | ✓ shipped | partial — coverage gaps |
| Per-verifier tolerance config | ✗ 1e-2 hardcoded | no — finops wants 1e-6, doc-qa wants 1e-2 |
| Range claims with per-endpoint multipliers (`$4.2M–$4.5M`) | ✗ dropped silently | no |
| README quickstart imports nonexistent symbols | ✗ `PolicyConfig` / `VerificationGate` / `agentsla.trace` | **NO — interview footgun** |
| Claude Agent SDK adapter | ✗ not shipped | **NO — spec says "runtime not wrapper"** |
| Real-LLM bench (Claude Haiku runs) | ✗ deferred | **NO — EchoModel self-certifies** |
| Real labeled traces for classifier eval | ✗ fixture is synthetic | **NO — circular eval** |
| WRITEUP reflects v0.2 numbers | ✗ stale | partial |

**v1 mandate:** close every Tier-1 gap. Land everything in one atomic release. Push to `origin/main`.

---

## 2. v1 Functional Requirements

### 2.1 MUST — Tier-1 critical (interview-blocking)

#### F1. README quickstart is runnable end-to-end (CRITICAL)

Current README quickstart snippet imports `PolicyConfig`, `VerificationGate`, and `agentsla.trace.TraceWriter`. None of these exist in the shipped surface (`policy.Policy`, `verify.VerificationChain`, `core.trace.TraceWriter`). First 30 seconds of an interviewer's local clone fails.

**Requirement:** the README quickstart snippet must `python -c ""` import cleanly on a fresh install, with no docs-vs-code drift.

**Acceptance:**
- README quickstart snippet pinned verbatim in `tests/integration/test_readme_quickstart.py`
- Test runs `exec()` on the snippet against the installed package, asserts no `ImportError` / `AttributeError`
- CI fails if README drifts from real surface

#### F2. Claude Agent SDK adapter (CRITICAL)

Spec (`Staff_Level_Projects_Spec_July2026.md` § Project 1) explicitly states AgentSLA wraps the **Claude Agent SDK** as one of three frameworks. v0.2 ships rawloop + langgraph-stub only. Without the Claude SDK adapter, AgentSLA is two-framework (not three) and the "runtime, not wrapper" claim weakens.

**Requirement:** implement `ClaudeSdkAdapter` against `AgentAdapter` ABC. Use the official Claude Agent SDK Python client (`claude-agent-sdk`) for the agent loop. Map SDK callbacks → `RuntimeHooks` (on_tool_call, on_tool_result, on_final_answer). Emit identical event sequence to `rawloop` adapter for the same task (parity test).

**Acceptance:**
- New file: `agentsla/adapters/claude_sdk.py` (≥150 LOC, ≤400 LOC)
- New test: `tests/unit/adapters/test_claude_sdk.py` — mock SDK client, assert parity with `rawloop` adapter on shared fixture task
- New integration test: `tests/integration/test_claude_sdk_parity.py` — same 5 demo tasks run through both adapters, assert event-kind sequence equality
- `agentsla/adapters/__init__.py` exports `ClaudeSdkAdapter`
- Parity bench includes Claude SDK column (4-row parity table: rawloop × langgraph × claude_sdk × dev/null)

**Out of scope:** production-grade Claude SDK usage (this proves the adapter pattern works; real deployments wire it to the user's SDK config).

#### F3. Real-LLM bench harness path (CRITICAL)

The hermetic `EchoModel` self-certifies: any task that contains a numeric token gets echoed back as the "final answer," so `gate_passed=100%` for wrapped mode and `verified=100%` are **structural**, not empirical. A reviewer reads the headline and asks: "Does this work on a real agent?" v1 must answer yes, with measured numbers.

**Requirement:** add a `bench/real_llm.py` harness that runs the same 30 tasks through a real Claude API call (model: `claude-haiku-4-5-20251001` — cheapest, deterministic-enough), captures traces to the same DuckDB schema, runs the verification gate against the real outputs, and reports sensitivity + specificity + overhead.

**Acceptance:**
- New file: `bench/real_llm.py` (≤250 LOC)
- CLI: `python -m agentsla bench-real --model claude-haiku-4-5-20251001 --tasks-per-domain 5 --out bench/results/real_llm.parquet`
- Requires `ANTHROPIC_API_KEY` env var; fails fast with clear message if absent
- New test: `tests/unit/bench/test_real_llm.py` — mock API, assert schema, assert gate integration
- New integration test: `tests/integration/test_real_llm_smoke.py` — 1 task × 1 seed against mock, asserts parquet rows + REPORT.md section
- REPORT.md gets new "Real-LLM bench" section with sensitivity/specificity/overhead
- CI runs the test with `ANTHROPIC_API_KEY=""` → skips (no live API in CI), but does run with mock

**Honest gap:** without a real API key in this iteration, the live numbers come back as `[NOT YET MEASURED]`. The harness path, tests, and CI gate are real.

#### F4. Real held-out classifier fixture (CRITICAL)

Current held-out fixture (`tests/fixtures/held_out_labels.jsonl`, 36 traces) was generated by `scripts/build_held_out_fixture.py` from synthetic triggers. Agreement is 100% because the fixture mirrors the heuristic triggers. A reviewer reading "100% agreement" asks: "On real traces?" v1 must answer yes.

**Requirement:** build a fixture generator that produces real Claude outputs on a held-out task set (excluded from heuristics), then hand-labels the categories. Replace `tests/fixtures/held_out_labels.jsonl` with the new fixture. The agreement number will drop (good — that proves the eval is honest).

**Acceptance:**
- `scripts/build_held_out_fixture.py` extended to use Claude API; falls back to synthetic if no key
- Fixture ≥100 real traces (or synthetic with explicit `[SYNTHETIC]` marker if no API)
- New test: `tests/integration/test_held_out_eval.py` — runs classifier on fixture, asserts eval reports per-category accuracy
- Eval-classifier report transparently marks `[SYNTHETIC]` vs `[REAL]` rows
- README headline cites the real number (or honest gap if not yet run)

#### F5. WRITEUP v0.2 numbers (CRITICAL)

WRITEUP.md (16.6KB) is from v0.1 era. Doesn't cite parity bench (30 paired runs, 100% event-count agreement), held-out eval, or figures. v1 must update it.

**Acceptance:**
- WRITEUP § "What we measured" gets three new sub-sections: parity, held-out, figures
- Cross-references to `bench/results/REPORT.md` § Parity / § Held-out / § Figures
- Architecture diagram (mermaid) added in § "What problem are we solving"
- Comparative-analysis link added in § "How we compare"

#### F6. Per-verifier tolerance config (HIGH)

Hardcoded `1e-2` in `verify/numeric.py` is wrong for finops (financial accuracy wants `1e-6`) and doc-qa (citation copy-paste wants `1e-2`). v1 makes tolerance per-`NumericVerifier` instance.

**Acceptance:**
- `NumericVerifier(tolerance=...)` constructor param honored end-to-end
- `examples/policy.yaml` documents `verify.tolerance` (top-level knob) or per-tool override
- Test: `test_numeric_tolerance_config.py` — verifier with `tolerance=1e-6` catches a `1e-4` perturbation; with `tolerance=1e-2` accepts it
- README § "Tuning tolerance" explains the tradeoff

#### F7. Range claims with per-endpoint multipliers (HIGH)

`$4.2M–$4.5M` parses as `$4.2` and `M–$4.5M` garbage. Should parse both endpoints with their multipliers. Currently silently dropped → inflates "unverifiable" coverage.

**Acceptance:**
- `verify/claims.py` range regex matches per-endpoint multiplier pattern
- Test: `test_range_claim_extraction.py` — positive fixture `$4.2M–$4.5M`, `$1,200-$1,800`, `€100-€200`; negative fixtures that previously parsed now parse correctly
- Coverage metric rises for range-bearing tasks

### 2.2 SHOULD — Tier-1 polish

#### F8. failure-modes.md quality pass

v0.1-era doc, 6 modes listed. v1: every mode must have (a) concrete trigger, (b) why-it-breaks, (c) observable signal, (d) v1 status (mitigated / open / partial), (e) mitigation or `KNOWN LIMIT`. Total ≥10 modes.

#### F9. Adapters index clean-up

`agentsla/adapters/__init__.py` `__all__` lists only `["base", "rawloop"]`. Update to include `langgraph`, `claude_sdk`, `noop_hooks`, `AgentAdapter`, `RuntimeHooks`.

#### F10. Bench integration tests

Add `tests/integration/test_bench_smoke.py` — runs the full bench with `--seeds 1`, asserts parquet schema + REPORT.md sections present. Catches "bench silently produces empty report" regressions.

### 2.3 WON'T (out of scope for v1)

- Multi-agent / swarm (deferred v2)
- Hosted SaaS (deferred v2)
- Live dashboards (deferred — Prometheus /metrics is the contract)
- Async trace writer (deferred v2 — DuckDB single-writer is documented honest gap)
- OTel exporter (deferred v2)
- Migration story for tenant extensions (deferred v2)

---

## 3. Non-functional requirements

| Category | Requirement |
|---|---|
| **Coverage** | ≥85% on `core/`, `policy/`, `verify/`, `classify/`, `adapters/`. CI gate at 85%, fail at 80%. |
| **Lint** | `ruff check .` zero findings. `ruff format --check .` zero diffs. |
| **Type** | `mypy --strict agentsla/core agentsla/policy agentsla/verify agentsla/classify agentsla/adapters` zero findings. |
| **Tests** | All green on Python 3.11, 3.12. Total <15s wall clock. Target: ≥420 tests. |
| **Docs** | README quickstart pinned by integration test. WRITEUP ≥2200w with v1 numbers + mermaid diagram. failure-modes.md ≥10 modes with the 5-field schema. |
| **Reproducibility** | `make bench && make report` → byte-identical table from current parquet. |
| **Integrity** | Every number in README traceable to source command. Real numbers when measured, `[NOT YET MEASURED]` otherwise. No fabricated data. |

---

## 4. Acceptance criteria (v1 Definition of Done)

1. ✓ All 394 existing tests pass + new tests for v1 (target: ≥420).
2. ✓ `tests/integration/test_readme_quickstart.py` passes — README snippet imports cleanly.
3. ✓ `agentsla/adapters/claude_sdk.py` exists, exports `ClaudeSdkAdapter`, parity test green.
4. ✓ `bench/real_llm.py` exists, CLI runs end-to-end, REPORT.md gets "Real-LLM bench" section.
5. ✓ Held-out fixture ≥100 real traces (or honest `[SYNTHETIC]` marker if no API), eval-report marks origin.
6. ✓ WRITEUP.md cites parity + held-out + figures + mermaid.
7. ✓ Per-verifier tolerance test green; finops docs say `1e-6`, doc-qa says `1e-2`.
8. ✓ Range-claim extractor handles `$4.2M–$4.5M`; new test green.
9. ✓ failure-modes.md has ≥10 modes with full 5-field schema.
10. ✓ CI 4-job pipeline green; new integration gates enforce F1 + F2 + F3.
11. ✓ `make test && make bench && make report && make bench-real` all green from clean clone.
12. ✓ v1 tagged, CHANGELOG.md entry, pushed to `origin/main`.

---

## 5. Hiring-signal evidence map

| Signal | v1 evidence |
|---|---|
| **Scale with SLOs** | `bench/results/REPORT.md` — p95 latency overhead, gate_passed, verified_at_truth, per-domain, real-LLM section. Plus parity proof across 3 adapters. |
| **Tradeoff narratives** | WRITEUP § "What we tried" gets one new entry: hermetic EchoModel vs real Claude. v0.2 chose hermetic (reproducibility); v1 layers real on top (credibility). |
| **Control-plane ownership** | RuntimeHooks wired through 3 adapters (rawloop + langgraph + claude_sdk) — the policy gate / verifier / classifier stay constant; only the agent loop changes. |
| **Failure-mode literacy** | failure-modes.md ≥10 modes; each with concrete trigger + observable + mitigation. Plus real-LLM bench produces real failure categories. |
| **Verifiable artifacts** | `make bench && make bench-real && make report` regenerates every number. One-command repro. CI integration gates prevent silent regressions. |

---

## 6. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Real Claude API cost exceeds $20 budget | Medium | Medium | Use Haiku 4.5 (cheapest). Cap at 30 tasks × 1 seed = 30 API calls. Mark as `[NOT YET MEASURED]` if not run. |
| Claude Agent SDK changes (it's actively evolving) | Medium | High | Pin SDK version in `pyproject.toml` `[claude]` extra. Adapter tests use mock client, not real SDK. |
| README quickstart test is brittle to format | Low | Low | Test parses only the `python` fenced block; ignores markdown. |
| Held-out fixture generation requires 100+ hand-labeled traces | High | Medium | Ship `[SYNTHETIC]` fixture as honest fallback; provide labeling tool script. |

---

## 7. Out-of-scope (explicit non-goals for v1)

- **Multi-tenancy.** Single-tenant runtime. Per-tenant policy + metrics is v2.
- **Streaming responses.** Phase 1 emits complete events; streaming event emission is v2.
- **Hot-reload of policies.** Reload requires process restart. Live reload is v2.
- **Web UI.** CLI + parquet + Grafana JSON is the shipped surface.
- **Production Claude Agent SDK integration.** Adapter proves the pattern; production wiring is user's job.

---

## 8. Sequencing (commit order, atomic)

1. **docs: PRD v1 + TRD v1** — no code changes
2. **fix(docs): README quickstart truth** — fix imports, add integration test
3. **feat(adapter): Claude SDK adapter** — TDD red → green → parity test
4. **feat(verify): per-verifier tolerance config** — TDD
5. **fix(verify): range claims per-endpoint multiplier** — TDD
6. **feat(bench): real-LLM bench harness** — TDD
7. **chore(classify): real held-out fixture generator** — extends existing script
8. **docs: failure-modes.md quality pass** — ≥10 modes
9. **docs: WRITEUP v1** — cite new numbers + mermaid
10. **chore(release): v1 tag + CHANGELOG** — final
