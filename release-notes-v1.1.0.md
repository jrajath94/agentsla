# v1.1.0 — Minor: bench-real cost guards + structural-replay honesty pass

**Date:** 2026-07-17
**Tag:** [`v1.1.0`](https://github.com/jrajath94/agentsla/releases/tag/v1.1.0)
**Commit:** tagged at `main` tip

`bench-real` is the repo's only paid path. Before this release a mistyped flag could fire dozens of paid API calls; now an accidental large run is impossible by default. The release also closes a documentation-honesty gap: the docs no longer claim "deterministic replay" (adapter re-execution) when what ships is structural replay (hash re-validation + stored answer).

## Highlights

- **`--dry-plan`** — prints the full run plan (model, tasks, prompts, rows, cache hits, estimated paid calls, output path) with zero network access and no API key required. Preview every live run before spending.
- **`--max-paid-calls` (default 3)** — the run refuses to start (exit 2) when uncached prompts exceed the cap. Larger runs must raise it explicitly per the frugal ladder in `docs/GPU_API_COST_OPTIMIZATION.md` (Rung C = 3 prompts, D = 9, E = 12 — corpus max).
- **Response cache + `--resume`** — raw model responses cached at `bench/cache/real_llm` keyed `sha256(model, task_id, prompt, seed)`; `--resume` serves cached prompts at zero paid cost (rows marked `cached=true`).
- **`--overwrite` required** — committed live evidence (`bench/results/real_llm.parquet`, MiniMax-M3, 2026-07-13, 24 rows) can no longer be silently regenerated.
- **Fail-fast default** — the run stops after the first provider error, keeps the partial parquet, exits 1; `--no-fail-fast` opts back into record-and-continue. Bounds retry blast-radius against a rate-limited key.
- **Stratified task selection** — a `--tasks-per-domain 1` smoke run now covers all three domains instead of three tasks from one domain.
- **Structural-replay honesty pass** — README, WRITEUP, PRD/TRD, failure-modes, and the `replay.py` docstring now say structural replay (recorded tool-call hash re-validation + stored final answer); adapter-driven re-execution is explicitly documented as not shipped.
- **`agentsla bench --all`** now parses (README documented it; the parser rejected it).
- **Makefile gates widened** — `type` and `coverage` targets now cover `agentsla/core`, `agentsla/policy`, and `agentsla/verify`.

## Quality gates at HEAD

- `ruff check .` — clean
- `mypy --strict agentsla/core agentsla/policy agentsla/verify` — 0 findings / 18 source files
- `pytest tests/` — **557 passed** (542 → 557, +15 net from cost-guard and `--all` test layers)
- Coverage on core/policy/verify — **94.6%** (floor 85%)
- `bench-real --dry-plan` verified live: 3 tasks / 3 prompts / 6 rows / 3 estimated paid calls, no network

## Honesty notes

- No live API calls were made for this release; the committed MiniMax-M3 evidence from 2026-07-13 is unchanged and now guarded against accidental regeneration.
- Replay wording corrected everywhere: "structural replay", never "deterministic re-execution". The adapter-driven replay path remains future work and is labeled as such.

**Install:** `pip install agentsla==1.1.0` (PyPI publish step still queued for the trusted-publisher workflow once configured).
