# v1.0.0 — Tier-1 release

**Date:** 2026-07-15
**Tag:** [`v1.0.0`](https://github.com/jrajath94/agentsla/releases/tag/v1.0.0)
**Commit:** HEAD of `main` after FF-merge of `phase-3/writeup-integrity`

This release closes the 2026-07-14 *Correction log* — the features the retracted `[v1.0.0] — 2026-07-13` section named (`ClaudeSdkAdapter`, cross-adapter parity test, `bench-real` CLI with `--synthetic`, held-out fixture) now exist in source, exercised by tests, and verified end-to-end.

## Highlights

- **`ClaudeSdkAdapter`** (third adapter, 352 lines, `class ClaudeSdkAdapter(AgentAdapter)`) — parity test asserts 4-event byte-identity across all three adapters
- **`bench-real` CLI + `--synthetic` flag** — wired through `ANTHROPIC_BASE_URL` for the Anthropic-compatible gateway
- **Held-out fixture** (≥30 rows, one per `FailureCategory`) — closes the v0.1 "classifier eval is circular" gap
- **Live bench populated** (`bench/results/real_llm.parquet`, 24 rows on `MiniMax-M3`) — README reports `Verified at truth = 92% / 92%` for naked vs wrapped
- **LLM-judge live output** (`bench/results/labels.jsonl`, 70 rows appended 2026-07-15 15:47 UTC) — judge correctly abstains on traces with no heuristic trigger; reframes PRD-v2 §7 row from "Documented limitation" to "Closed"
- **Honest-gap banner suppression** — `_real_llm_has_measured_truth()`; pinned by `TestReportAutoIncludesRealLlmSection`
- **WRITEUP.md + README.md integrity suites** (24+ cases total) — pin against drift classes the retraction caught
- **Release-provenance suite** (`tests/release/test_release_provenance.py`) — pins tag ↔ main ↔ branch-tip equality across push and PR contexts
- **PRD-v2 honest gaps closed 2026-07-15** — `real_llm.parquet`, README headline, strategy-docs leak

## Quality gates at HEAD

- `ruff check .` — clean
- `ruff format --check .` — clean
- `mypy --strict agentsla/core agentsla/policy agentsla/verify` (TYPING-01 strict scope) — 0 findings across 18 source files
- `pytest tests/` — 533 passed
- Coverage on `agentsla/core policy verify` — 94.59% (≥85% floor)

## Honesty notes

- The 2026-07-13 phantom `v1.0.0` tag at commit `df98a76` predated the features it claimed. The tag was re-pointed onto `HEAD` of `phase-3/writeup-integrity` (`v1.0.0 → 1d6bf03 → 332026f → 9f60163 → a2d33eb → b0b694f`). The phantom was never published to a GitHub Release page (no `release.yml` ran for it), so the tag move has no external artifact to overwrite. The phantom anchor is preserved as `tombstone-2026-07-13-v1.0.0` for audit.
- The 2026-07-14 *Correction log* is retained in `CHANGELOG.md` as audit trail.

**Install:** `pip install agentsla==1.0.0` (PyPI publish step queued for the trusted-publisher workflow once configured).
