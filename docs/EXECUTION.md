# AgentSLA — Execution Plan (Target State, No Stages)

**STATUS: HISTORICAL — all 12 DoD items shipped at v0.2 close (see CHANGELOG `[v0.2.0]`/`[v0.2.2]`).** Retained as the trajectory record for the v0.1→v0.2 push and as interview-prep context. For active execution plans going forward, see `docs/PRD-v1.md` (Claude SDK + Real-LLM bench push) + `docs/TRD-v1.md`.

**Purpose:** one document. Tells the implementing engineer (human or AI) exactly what ships and when to stop. No v0.1 / v0.2 / v0.3 phases — every item below is the **target state** for this push. Anything not on this list is out of scope.

**Read after:** none. This is the source of truth for execution.
**Supersedes:** `docs/PRD.md`, `docs/TRD.md` (kept for interview-prep context; this file drives code).

---

## 1. The Definition of Done — stop when ALL of these are true

1. **`bench/harness.py` writes a `Verdict` event to DuckDB** for every wrapped run. `TraceReader.iter_events(trace_id)` returns ≥1 `Verdict` per wrapped trace. Verified by an integration test.
2. **One `ClaimVerdict` type per layer.** Internal verify uses `InternalClaimVerdict` (dataclass); event emission uses `events.ClaimVerdict` (pydantic). No name collision. No silent mapping in dead code paths.
3. **`VerificationGate.run(trace, final_answer)` signature matches `VerificationChain.run(trace, final_answer)`.** Gate is no longer dead code in the bench; it is the bridge between chain result and trace event.
4. **CI workflow at `.github/workflows/test.yml`** runs ruff + mypy + pytest + bench-smoke + integration gate. Failing the gate fails the build.
5. **Integration gate** is a grep-level check that `bench/harness.py` imports `PolicyGate`, `Classifier`, `JsonlLabelSink`, `build_metrics`. Prevents re-introduction of the wiring bug that motivated this push.
6. **Headline metric `verified_pct` is honest.** Renamed to `gate_passed` in the bench output; new column `verified_at_truth` added where ground truth is available. README headline + WRITEUP + REPORT.md all updated.
7. **Reframed `trigger_reasoning_error`** does anchor-aware contradiction detection. No more false positives on multi-step answers.
8. **Reframed `trigger_tool_response_misuse`** distinguishes "agent adapted to error" from "agent reused error as data."
9. **README updated** with: refactored headline, link to `docs/EXECUTION.md`, link to comparative analysis.
10. **WRITEUP updated** with mermaid architecture diagram and comparative-analysis link.
11. **Comparative analysis doc** at `docs/comparative-analysis.md` (vs LangSmith/Langfuse/Helicone/Braintrust).
12. **Final commit:** all P0/P1 items atomic, conventional-commits subject ≤72 chars, push to `origin/phase-1/implement-trace-replay-rawloop`. Working tree clean. 332 existing tests + new tests = ≥350 tests passing. Coverage ≥93%.

When all 12 are true, **stop**.

---

## 2. Best SWE principles applied

| Principle | Application |
|---|---|
| **YAGNI** | No live-LLM bench, no OTel, no multi-tenancy. Ship the four guarantees well, not eight poorly. |
| **KISS** | Schema unification = one rename. No new abstraction. |
| **TDD (red/green/refactor)** | Every commit lands with a failing test first → implementation → green → commit. |
| **DRY** | The `VerificationGate` already does the pydantic mapping; use it instead of letting the bench bypass it. |
| **Atomic commits** | One logical change per commit. Conventional Commits prefix. Subject ≤72 chars. |
| **No dead code** | `_ALLOW` shim already removed. `VerificationGate` will be alive after this push. |
| **Honest measurement** | Reframing `verified_pct` → `gate_passed` removes a misleading headline. |
| **One-command repro** | `make test && make bench && make report` regenerates everything. |
| **Defensive integration gates** | A grep gate in CI prevents future contributors from silently removing the wiring. |

---

## 3. Commit plan (atomic, in order)

Each commit is one logical unit. Each is independently revertable. Each passes all tests.

### Commit 1: `docs: add execution plan`
- Files: `docs/EXECUTION.md` (new), `docs/PRD.md` (kept), `docs/TRD.md` (kept).
- Tests: none (docs only).
- Acceptance: `git log --oneline` shows the new commit. No code changes.

### Commit 2: `refactor(verify): rename ClaimVerdict to InternalClaimVerdict`
- Files: `agentsla/verify/base.py`, `agentsla/verify/numeric.py`, `agentsla/verify/chain.py`, `agentsla/verify/gate.py`, `agentsla/verify/__init__.py`.
- Tests: existing 332 still pass. Add `tests/unit/verify/test_internal_claim_verdict.py` to pin the public surface.
- Acceptance: `grep -r "ClaimVerdict" agentsla/verify/` shows only `InternalClaimVerdict` and the import from `core/events`.

### Commit 3: `fix(gate): align VerificationGate.run signature with VerificationChain.run`
- Files: `agentsla/verify/gate.py`.
- Tests: `tests/unit/verify/test_verification_gate.py` — `run(trace, final_answer)` returns `GateResult` with `verdict` and `chain` populated; `Verdict` event appended to writer.
- Acceptance: `mypy --strict agentsla/verify` passes. `pytest tests/unit/verify` green.

### Commit 4: `feat(bench): persist Verdict events to trace store`
- Files: `agentsla/bench/harness.py`.
- Tests: `tests/integration/test_verdict_persistence.py` — wrapped run produces ≥1 Verdict event; naked run produces 0.
- Acceptance: `pytest tests/integration` green. Manual check: bench parquet → DuckDB query → Verdict row exists.

### Commit 5: `refactor(classify): sharpen reasoning_error and tool_response_misuse heuristics`
- Files: `agentsla/classify/heuristics.py`, `tests/unit/classify/test_heuristics.py`.
- Tests: add positive + negative fixtures for each refined trigger.
- Acceptance: existing 100% agreement on synthetic dataset still holds; new edge cases covered.

### Commit 6: `feat(metrics): honest headline — gate_passed + verified_at_truth`
- Files: `agentsla/bench/harness.py`, `agentsla/bench/report.py`, `README.md`, `WRITEUP.md`, `bench/results/REPORT.md`.
- Tests: `tests/unit/bench/test_report.py` — new columns appear with correct schema.
- Acceptance: report regenerates with renamed columns. README headline no longer misleading.

### Commit 7: `ci: github actions workflow with integration gate`
- Files: `.github/workflows/test.yml`.
- Tests: workflow YAML valid; integration-grep step runs locally.
- Acceptance: `yamllint .github/workflows/test.yml` clean; manual `act` run green.

### Commit 8: `docs: comparative analysis + architecture diagram`
- Files: `docs/comparative-analysis.md` (new), `WRITEUP.md` (mermaid block).
- Tests: none.
- Acceptance: links resolve. Mermaid renders in GitHub.

### Commit 9: `chore(lint): mypy unused-section cleanup + ruff sync`
- Files: `pyproject.toml`.
- Tests: `mypy agentsla/core agentsla/policy agentsla/verify` clean; `ruff check .` zero findings.
- Acceptance: no unused overrides; no warnings.

### Commit 10: `release: v0.1 hardening tag`
- Files: `CHANGELOG.md` (new).
- Tests: full `make test && make bench && make report` green from clean clone.
- Acceptance: tag `v0.1.0` annotated. Working tree clean. Pushed to origin.

---

## 4. Out of scope (explicit non-goals — do NOT add to this push)

- Live-LLM bench against Claude API (cost + non-determinism; deferred).
- OTel exporter (Prometheus covers the on-call needs).
- Multi-tenancy / per-tenant policy (single-tenant is the contract).
- Streaming trace emission (in-memory list is sufficient for v0.1).
- Training a custom classifier (heuristic + judge is sufficient).
- Property-based tests for policy gate (add later; not in this push).
- Async / backpressure trace writer (DuckDB single-writer is documented honest gap).
- Migration story for trace schema evolution (JSON column absorbs change).

---

## 5. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Schema rename breaks 332 tests | High | TDD per commit; never modify tests to match code — fix code. |
| Verdict event doubles trace size | Low | Parquet compresses; bench adds ~1% to row size. |
| CI integration gate is too strict | Low | Gate checks import lines only, not implementation details. |
| `verified_pct` rename breaks downstream consumers | Low | README is the only consumer; this repo is v0.1 hardening. |

---

## 6. Verification — how to know we're done

```bash
# 1. All tests pass + integration tests pass + coverage holds
make test                           # ≥350 tests
pytest --cov=agentsla/core --cov=agentsla/policy --cov=agentsla/verify --cov-report=term-missing
                                   # ≥93% coverage

# 2. Lint + type clean
ruff check .                        # zero findings
ruff format --check .               # zero diffs
mypy --strict agentsla/core agentsla/policy agentsla/verify
                                   # zero findings, no unused-section warning

# 3. Bench regenerates headline from parquet
make bench && make report           # REPORT.md headline matches README

# 4. Verdict events persist
python -c "
from pathlib import Path
import duckdb
con = duckdb.connect('bench/results/results.duckdb', read_only=True)
print(con.execute('SELECT COUNT(*) FROM events WHERE kind = verdict').fetchone())
"
# Expect: at least 175 wrapped runs' worth of verdict rows

# 5. CI integration gate runs locally
bash .github/workflows/test.yml    # or `act` if installed
                                   # all steps green
```

When 1–5 all green: **push and stop.**