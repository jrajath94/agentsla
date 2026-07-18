# v1.2.0 — Minor: execution replay + budget wired into runtime hooks

**Date:** 2026-07-17
**Tag:** [`v1.2.0`](https://github.com/jrajath94/agentsla/releases/tag/v1.2.0)
**Commit:** tagged at `main` tip

This release closes the two remaining interview-critical gaps from the 2026-07-15 workspace review: replay that actually re-drives the adapter loop, and a budget manager that is load-bearing in the runtime contract. It also completes the truth-boundary cleanup so no document claims a verifier that does not exist.

## Highlights

- **Execution replay** — `agentsla replay TRACE_ID --execute` re-drives the adapter loop with every tool stubbed to serve its recorded result (FIFO per tool, matched by `call_id`) and asserts the re-produced final answer is **byte-identical** to the recorded one, plus `args_hash` parity on every re-executed tool call. Recorded tool errors re-raise so the error path replays too.
- **Honest scope** — execution replay covers deterministic-model (rawloop-recorded) traces. Live-model traces are refused with exit 2 rather than fabricating a determinism guarantee; structural replay (hash re-validation + stored answer) remains available for every trace.
- **`BudgetedHooks`** — `BudgetManager` is now wired into the `RuntimeHooks` contract. Policy DENY wins first (and is not budget-charged); a budget breach converts to a policy-style DENY so the agent degrades to its short-circuit answer instead of crashing; post-execution breaches degrade the next call. Observable: `.breaches`, `.denied_calls`, `.level(trace_id)`.
- **Graceful-degradation proof** — integration test drives the real adapter with `max_calls=0`: tool never executes, no exception escapes, degraded answer returned.
- **Truth-boundary cleanup** — WRITEUP diagram, TRD § 1.5, verify docstrings, and the ClaimVerdict comment no longer imply Grounding/Schema verifiers exist; NumericVerifier is the one shipped verifier and every doc now says so.

## Quality gates at HEAD

- `ruff check .` + `ruff format --check .` — clean
- `mypy --strict agentsla/core agentsla/policy agentsla/verify` — 0 findings / 18 source files
- `pytest tests/` — **574 passed** (557 → 574; +17 execution-replay, +10 budget-hooks, net of shared fixtures)
- Coverage on core/policy/verify — **94.6%** (floor 85%)
- `agentsla replay --execute` smoke-verified live: recorded run → byte-identical re-execution, exit 0

## Honesty notes

- Zero paid API calls in this release; all evidence is hermetic.
- Execution replay's model allowlist is explicit (`echo-1`). Extending it to live-model traces requires model-message stubbing — documented as future work, not claimed.

**Install:** `pip install agentsla==1.2.0` (PyPI publish step still queued for the trusted-publisher workflow once configured).
