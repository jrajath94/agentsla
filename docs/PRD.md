# AgentSLA — Product Requirements Document (v0.1 → v0.2)

**Author:** TDD-driven red/green execution
**For:** Anthropic Staff SWE candidacy (Tier-1 project) · Hiring signals: scale-with-SLOs · tradeoff narratives · control-plane ownership · failure-mode literacy · verifiable artifacts
**Date:** 2026-07-09

---

## 0. Thesis (the one-liner you defend cold)

AgentSLA is an **SLO-aware reliability runtime** that wraps any tool-calling LLM agent (Claude SDK, LangGraph, raw loop) with **four hard guarantees**:

1. **Policy enforcement** — every tool call passes a declarative YAML policy (allowed tools, JSON-Schema validation, per-tool/per-trace call caps, egress regex pack) before execution.
2. **Post-generation verification** — every numeric claim in the final answer is recomputed against source tool results; the gate emits a `Verdict` event with `coverage` (fraction of claims checked) and `incorrect` count.
3. **Structural replay** — every run is captured as an append-only event log; `agentsla replay <trace_id>` re-validates recorded tool-call hashes and returns the recorded final answer, converting trace drift into a reproducible audit signal without claiming full adapter re-execution.
4. **Failure attribution** — every failed trace is labeled with one of 14 categories via a two-stage classifier (heuristic → LLM judge) and emitted as a Prometheus counter.

It is the **reliability layer for agents** — the thing that turns "agent demoed well" into "agent runs in production with an SLA."

---

## 1. Why this exists (the market gap)

| Existing tool | What it does | What it doesn't do |
|---|---|---|
| **LangSmith** (LangChain) | Trace UI, prompt eval | No policy enforcement, no numeric recompute, no failure taxonomy |
| **Langfuse** | OSS tracing + eval | No pre-execution gate, no per-claim verification |
| **Helicone** | LLM proxy + caching | No agent-aware hooks (tool calls are invisible), no verification |
| **Braintrust** | Eval framework | No append-only trace log, no structural replay, no egress control |
| **Arize Phoenix** | Drift + observability | No agent control plane |

AgentSLA is the **only** tool-calling-agent runtime here that ships all four guarantees together, with replay-safe append-only traces as a first-class primitive.

---

## 2. Hiring signals this artifact produces

The five signals the Anthropic Staff rubric screens for, and how AgentSLA evidences each:

| Signal | Evidence in this artifact |
|---|---|
| **Scale with SLOs** | `bench/results/REPORT.md` — p95 latency overhead, success rate, verified%, injection resistance, all per-domain. Latency frontier is measured, not assumed. |
| **Tradeoff narratives** | WRITEUP § "What we tried, and why we changed it" documents five concrete tradeoffs (in-process rawloop vs framework fork, append-only vs mutable trace, coverage-as-metric vs binary, heuristic-first vs LLM-first, DuckDB vs SQLite/Postgres). |
| **Control-plane ownership** | The whole runtime IS the control plane — `PolicyGate` is the scheduler for tool calls; `VerificationChain` is the post-execution auditor; `Classifier` is the routing logic for failure attribution. Not a wrapper around someone else's components. |
| **Failure-mode literacy** | `docs/failure-modes.md` documents **6 known failure modes** (DuckDB writer lock, verifier scaling, judge availability, EchoModel bias, classifier eval circularity, regex false positives), each with **trigger**, **why-it-breaks**, **observable signal**, **v0.1 status**, **mitigation**. |
| **Verifiable artifacts** | `make bench && make report` regenerates every number in README. The bench is hermetic, the parquet is reproducible, the COMMIT hash is in the report. One-command repro. |

---

## 3. Functional requirements

### 3.1 MUST (v0.1 — already shipped, hardening remaining)

- **F1. Trace store.** Append-only event log over DuckDB+Parquet with strict ordering by `(trace_id, seq)`. ✓ shipped.
- **F2. Replay.** Strict + tolerant structural replay; args_hash drift detection. ✓ shipped.
- **F3. Policy gate.** Declarative YAML → Pydantic-frozen → runtime decision. Five-step evaluation (membership → schema → per-tool counts → global count → egress). ✓ shipped.
- **F4. Egress pack.** PAN-Luhn, SSN, AWS key, JWT — real-format defaults + tenant-extensible. ✓ shipped.
- **F5. Budget manager.** Token/cost/call/wall-time with 4-level degradation (FULL → REDUCED → MINIMAL → EMERGENCY). ✓ shipped.
- **F6. Numeric verifier.** Extract → recompute → tolerance-check → emit `Verdict` with coverage + per_claim. ✓ shipped. **Gap: schema unification.**
- **F7. Classifier.** 14-cat taxonomy; 14 heuristic triggers; two-stage (heuristic → judge ≤20%); Prometheus counter; JSONL sink. ✓ shipped.
- **F8. Bench harness.** 30 tasks × 3 domains × 5 seeds × 2 modes + injection = 350 rows parquet. ✓ shipped.
- **F9. Seeded-error experiment.** 20 tasks × 5 strategies × 100 trials = 10K rows; sensitivity/specificity table. ✓ shipped.
- **F10. CLI.** `run` / `replay` / `bench` / `bench-seeded-errors` / `report`. ✓ shipped.

### 3.2 MUST (v0.1 hardening — gaps to close)

- **F11. Unified `ClaimVerdict` schema.** One type, one source of truth. Eliminate the pydantic-vs-dataclass drift. → P0.
- **F12. Bench writes `Verdict` events to DuckDB.** Closed in v0.2; wrapped bench runs now persist verdict events. Structural replay still does not drive the adapter loop. → deferred follow-on, not a v0.2 blocker.
- **F13. Honest `verified_pct` metric.** Distinguish "verification ran without exception" from "claims recompute-passed against ground truth." Reframe headline. → P0.
- **F14. CI integration gate.** grep-level check that `bench/harness.py` imports `PolicyGate` + `Classifier` + `JsonlLabelSink` + `build_metrics`. Prevents silent wiring loss. → P1.
- **F15. Architecture diagram** in WRITEUP (mermaid or PNG). → P1.
- **F16. Comparative analysis** (vs LangSmith/Langfuse/Helicone/Braintrust) in a dedicated doc. → P1.

### 3.3 SHOULD (v0.2)

- **F17. Live-LLM bench.** Record real Claude API traces (Haiku 4.5); feed them through the same report path; measure real SLO attainment. → 1 GPU day.
- **F18. Cross-adapter parity bench.** Same task under Claude SDK + LangGraph + rawloop, byte-identical policy decisions. → 0.5 GPU day.
- **F19. Property-based tests** for policy gate (Hypothesis) + classifier (≥80% coverage on heuristics.py).
- **F20. Async trace writer** with backpressure (closes DuckDB single-writer lock failure mode).
- **F21. OTel exporter** alongside Prometheus.
- **F22. JSONL trace export** for piping into external log aggregators.

### 3.4 WON'T (out of scope)

- Multi-agent / swarm taxonomy (MAST 14→33 — we collapse to 14 for single-agent).
- Training a custom classifier on the 100 labelled traces.
- Real-time dashboarding beyond Prometheus scrape (operator-side concern).

---

## 4. Non-functional requirements

| Category | Requirement |
|---|---|
| **Coverage** | ≥85% on `core/`, `policy/`, `verify/` (CI gate). Stretch: ≥90% including `classify/` + `adapters/`. |
| **Lint** | `ruff check .` zero findings. `ruff format --check .` zero diffs. |
| **Type** | `mypy --strict agentsla/core agentsla/policy agentsla/verify` zero findings. |
| **Tests** | All green on Python 3.11, 3.12. Total <10s wall clock. |
| **Docs** | README has headline traceable to `make bench` + `make report`. WRITEUP ≥2000w with architecture diagram. `docs/failure-modes.md` ≥6 documented failure modes. |
| **Reproducibility** | `make bench && make report` → byte-identical table from current parquet (deterministic seeds). |
| **Integrity** | Every number in README traceable to source command. No `[FABRICATED]` placeholders. Honest gaps labeled `[NOT YET MEASURED]`. |

---

## 5. Acceptance criteria (v0.1 hardening — Definition of Done)

1. ✓ `make bench && make report` regenerates a README that matches the headline table.
2. ✓ All 332 existing tests pass + new tests for P0 fixes (target: 360+).
3. ✓ Coverage ≥93% on `core/policy/verify/classify`.
4. ✓ CI workflow exists, runs lint + type + test + integration-gate + bench-smoke.
5. ✓ `docs/PRD.md` (this doc) + `docs/TRD.md` committed.
6. ✓ WRITEUP updated with architecture diagram + comparative-analysis link.
7. ✓ `bench/results/REPORT.md` shows seeded-error experiment with sensitivity ≥85% @ ±50% perturbation, specificity ≥90% @ 0% perturbation.
8. ✓ No "two `ClaimVerdict` types" remain in the codebase.
9. ✓ `bench/harness.py` writes `Verdict` events to the trace store (verified via `agentsla report` or DuckDB query).
10. Structural replay is documented honestly as hash validation + recorded-answer recovery, not adapter-driven re-execution.

---

## 6. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Schema unification breaks 332 existing tests | High | High | Red/green TDD per change; never modify tests to match code — modify code to satisfy tests. |
| Live-LLM bench costs exceed budget | Medium | Medium | Use Haiku 4.5 (cheapest), 1000 trials max. Document as `[NOT YET MEASURED]` if not run. |
| Classifier eval stays circular | Medium | Low | Documented honestly. The 100% is a ceiling; v0.2 swaps in real traces. |
| Bench smoke in CI is slow | Low | Medium | `--seeds 1` for CI, `--seeds 5` for local. CI gate <2min. |

---

## 7. Out-of-scope (explicit non-goals for v0.1)

- **Multi-tenancy.** Single-tenant runtime. Per-tenant policy + metrics is v2.
- **Streaming responses.** Phase 1 emits complete events; streaming event emission is v2.
- **Hot-reload of policies.** Reload requires process restart. Live reload is v2.
- **Web UI.** CLI + parquet + Grafana JSON is the shipped surface.
