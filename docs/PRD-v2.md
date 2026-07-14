# AgentSLA — Product Requirements Document (v2)
*Hiring-signal-grade PRD for Anthropic Staff+ Tier-1 candidacy. Drives from the
five signals in `Staff_Level_Projects_Spec_July2026.md` Part 0.*

**Status:** v2 — synthesizes post-v0.1 audit + working-tree changes.
**Audience:** Anthropic staff interviewers, hiring managers, open-source users.
**Cut date:** 2026-07-13.

---

## 1. Mission

> Enterprises don't lack agents; they lack agents with **SLAs**. AgentSLA is the
> reliability runtime that wraps any tool-calling agent with **deterministic
> replay**, **policy enforcement**, **post-generation verification**, and a
> **failure taxonomy** — so the same agent that demos in eval becomes the agent
> that ships to production without losing a quarter to P0 incidents.

### 1.1 Thesis (interview one-liner)
AgentSLA turns the FISION/Cassandra Sentinel learnings — *every agent fails;
the question is whether you caught it and how* — into a portable open-source
runtime, with the same replay + verification + taxonomy ideas applied to
**tool-calling LLM agents** as production observability applied to services.

### 1.2 Out-of-scope (PRD-v2)
- Model training / fine-tuning (DraftForge handles that elsewhere).
- Inference control plane / P-D disagg (GoodputLab).
- New agent frameworks (we adapt to three: RawLoop, LangGraph, Claude Agent SDK).
- Closed-source hosted service. Open source under MIT.

---

## 2. The five hiring signals — per-signal PRD

Each signal gets its own acceptance test, traceable to a code path, an artifact, or a documented honest gap.

### Signal 1 — **Scale with SLOs, not just scale**

| Sub-requirement | Acceptance | Source of truth |
|---|---|---|
| SLO columns in every headline table (p95, success, gate-passed, verified@truth) | Report has 6+ metric rows; every metric has a denominator | `bench/report.py:_markdown_table` |
| At least one latency distribution figure (CDF) in the README | `figures/latency_cdf.png` exists + linked | `bench/figures.py` |
| **Live measured numbers** for at least one real API | `real_llm.parquet` exists with model_id + rows | `bench/real_llm.py:run_real_llm_bench` |
| SLO attainment stated explicitly (e.g. "at p95<800ms") | README "Results" section names a p95 budget | README |

### Signal 2 — **Tradeoff narratives**

| Sub-requirement | Acceptance | Source of truth |
|---|---|---|
| One "we tried X, failed because Y" per phase in WRITEUP.md | WRITEUP has ≥4 tradeoff paragraphs | `WRITEUP.md` |
| Quantified frontier: latency vs gate-strictness | `seeded_errors.parquet` exists + REPORT section shows sensitivity vs tolerance | `bench/seeded_errors.py` |
| Honest-gap callout on every unverifiable metric | `> **Honest gap —** ...` banner appears when `verified_at_truth` is `None` | `bench/report.py:_render_honest_gap_banner` |
| Documented "when does NOT pay" — chunked-prefill-equivalent analysis | WRITEUP "Limits" section names ≥2 architectural break-points | `WRITEUP.md` |

### Signal 3 — **Control-plane ownership**

| Sub-requirement | Acceptance | Source of truth |
|---|---|---|
| Policy engine is a first-class component (not a wrapper) | `PolicyGate` is its own module with audit trail | `agentsla/policy/gate.py` |
| Router for adapters — choose RawLoop vs LangGraph vs Claude SDK | `AgentAdapter` ABC + 3 implementations + parity bench | `agentsla/adapters/`, `bench/parity.py` |
| Replay orchestrator — strict vs tolerant modes | `replay(trace_id, mode)` returns `ReplayReport` | `agentsla/core/replay.py` |
| Budget manager with degradation hooks | `BudgetManager` consumed in all three adapters | `agentsla/core/budget.py` |

### Signal 4 — **Failure-mode literacy**

| Sub-requirement | Acceptance | Source of truth |
|---|---|---|
| 14-category failure taxonomy from FISION paper | `FailureCategory` enum has 14 values | `agentsla/classify/taxonomy.py` |
| Heuristic → category mapping table is published | `docs/class-taxonomy.md` lists every heuristic trigger | `docs/class-taxonomy.md` |
| Postmortem-style doc covers ≥3 p99.9 failure modes | `docs/failure-modes.md` has ≥3 sections (DuckDB lock, replay drift, judge overload) | `docs/failure-modes.md` |
| Seeded-error experiment proves verifier catches injected errors | `seeded_errors.parquet` + REPORT section shows sensitivity ≥85% | `bench/seeded_errors.py` |
| Classifier eval (held-out 36 traces) reports agreement | `eval_classifier.md` + "Classifier held-out evaluation" section | `bench/eval_classifier.py` |

### Signal 5 — **Verifiable artifacts**

| Sub-requirement | Acceptance | Source of truth |
|---|---|---|
| Public repo with one-command repro | `make bench` (or `agentsla bench --seeds 5`) reproduces every README number | `Makefile`, README quickstart |
| Figures in README | ≥3 PNGs linked from README "Results" | `bench/results/figures/` |
| Circulated writeup (≥2K words) | `WRITEUP.md` has ≥2000 words | `WRITEUP.md` |
| MIT license + HF dataset + v0.1 tag | `LICENSE` + dataset + git tag exist | `LICENSE`, repo tags |

---

## 3. Functional requirements (F-IDs map to test gates)

| ID | Requirement | Acceptance gate | Test file |
|---|---|---|---|
| F1 | Trace store appends events to DuckDB + Parquet | roundtrip preserves row count | `tests/unit/core/test_trace_store.py` |
| F2 | Deterministic replay reproduces byte-identical final answer across N runs | 5/5 replays match | `tests/integration/test_cli_byte_identical.py` |
| F3 | **Honest gap** surfaced when no ground truth | banner includes env-var command | `tests/unit/bench/test_report.py:TestReportCli` |
| F4 | Policy gate denies SSN/AWS-key/JWT egress on tool args | 20+ deny cases pass | `tests/unit/policy/test_gate.py` |
| F5 | Three adapters emit equivalent event sequences | parity 100% agreement | `tests/integration/test_cross_adapter_parity.py` |
| F6 | Numeric verifier catches seeded ±10% perturbations | sensitivity ≥85% | `tests/unit/bench/test_seeded_errors.py` |
| F7 | Per-verifier tolerance config (F6+F7 of PRD-v1) | tolerance per domain | `tests/unit/verify/test_numeric_tolerance_config.py` |
| F8 | Failure classifier + heuristic triggers | 14-cat mapping published | `tests/unit/classify/test_heuristics.py` |
| F9 | Classifier held-out agreement ≥80% | `eval_classifier.md` reports ≥80% | `tests/integration/test_classifier_eval.py` |
| F10 | **Bench smoke** — end-to-end CLI run produces parquet + report | smoke test exits 0 | `tests/integration/test_bench_smoke.py` |
| F11 | **Live measured numbers** for at least one model | `real_llm.parquet` ≥30 rows + section in REPORT | `tests/unit/bench/test_real_llm.py` |
| F12 | Two-stage classifier (heuristic → judge) with ≤20% judge sampling | default ratio honored | `tests/unit/classify/test_classifier.py` |
| F13 | Range-claim extraction ("$4.2–4.5M") handled | range grammar parses | `tests/unit/verify/test_range_claim_extraction.py` |
| F14 | Schema versioning + upgrade scaffold | schema version is readable from trace | `tests/unit/core/test_schema_version.py` |

---

## 4. Non-functional requirements

| Dimension | Budget | Measurement |
|---|---|---|
| Coverage on `core/`, `policy/`, `verify/`, `classify/`, `adapters/` | ≥85% | `pytest --cov` |
| mypy strict on `core/`, `policy/`, `verify/` | 0 errors | `mypy .` |
| ruff clean | 0 errors | `ruff check .` |
| Bench smoke runtime | <60s for 30 tasks × 3 seeds | `test_bench_smoke` |
| Real-LLM bench runtime | <5min for 15 tasks × 1 seed | `test_real_llm` (mocked) |
| Replay divergence (strict) | 0 events | `test_cli_byte_identical` |
| Latency overhead (wrapped vs naked, hermetic) | <100% | `REPORT.md` headline |
| Verification coverage on numeric tasks | ≥80% claims verified | `agentsla_verify_coverage` gauge |

---

## 5. Anti-fabrication invariants

These are the **non-negotiable** integrity rules. Violating any closes Tier-1.

1. **No fabricated numbers.** Every numeric claim is traceable to `bench/results/*.parquet` OR explicitly tagged `[NOT YET MEASURED]` with the env-var command to populate it.
2. **No fabricated features.** Docs describe what the code does. Roadmap items are labelled "future" and never claimed as shipped.
3. **No silent failures.** Errors surface with `RuntimeError` + clear message. CLI exit codes are 0/2 (not 1).
4. **Honest-gap banners are public.** The README, REPORT.md, and WRITEUP.md each surface their own gaps with the exact reproduction command.
5. **Hermetic bench ≠ real agent.** The README + WRITEUP explicitly say so.
6. **Live API key handling.** API keys live in env vars only, never committed, never in `.env` files inside the repo, never in any tracked file.

---

## 6. Acceptance for "v0.1 honestly complete" (v1 shipped in CHANGELOG; v2 audit re-checks every item)

- [x] Blockers 1+2 closed (PolicyGate + Classifier + Prometheus wired into harness)
- [x] Seeded-error experiment with sensitivity/specificity table in REPORT
- [x] README headline traceable to `make bench`
- [x] CI workflow with integration-gate grep
- [x] Failure-modes postmortem
- [x] Cross-adapter parity bench with event-sequence equality
- [x] Held-out classifier eval with agreement ≥80%
- [x] Real-LLM bench path (CLI + tests + schema) — measured numbers in-flight
- [x] WRITEUP ≥2K words with ≥4 tradeoff narratives
- [x] Public MIT license

---

## 7. Open honest gaps (PRD-v2 delta from PRD-v1)

| Gap | Why still open | Closure path |
|---|---|---|
| `real_llm.parquet` not yet populated against MiniMax-M3 | API key authorization + remote run pending | Run `python -m agentsla bench-real --model MiniMax-M3 --tasks-per-domain 5` with env set inline |
| README "verifier caught X%" headline | depends on real_llm numbers | auto-derived once real_llm.parquet lands |
| Classifier judge (LLM) never exercised in live bench | hermetic bench uses StubJudge by design | documented limitation; live swap is 1 line in harness |

---

## 8. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Anthropic API key drift / rate limit during live bench | Medium | real_llm path tolerates errors, writes `[NOT YET MEASURED]` rows |
| Public-repo breach: strategy docs (Anthropic_Candidacy_Playbook, MASTER_EXECUTION_PROMPT_CLAUDE, Staff_Level_Projects_Spec_July2026, briefs) leaked on `origin/main` | Confirmed (per PORTFOLIO.md §4) | **Destructive remediation required — human approval before any filter-repo or force-push** |
| Prometheus metrics endpoint exposes on LAN by default | Mitigated (defaults to 127.0.0.1) | keep `--metrics-addr 127.0.0.1` default |
| Coverage drop when new code lands | Low | CI gate enforces ≥85% |