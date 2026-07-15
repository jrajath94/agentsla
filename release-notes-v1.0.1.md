# v1.0.1 — Patch: standalone Prometheus exporter

**Date:** 2026-07-15
**Tag:** [`v1.0.1`](https://github.com/jrajath94/agentsla/releases/tag/v1.0.1)
**Commit:** `47538f3` on `main`

This patch closes the W7 exporter gap from the v1.0.0 Tier-1 release. v1.0.0 shipped the three AgentSLA metric families (`agentsla_failures_total`, `agentsla_verify_coverage`, `agentsla_classify_latency_seconds`) and a Grafana dashboard that queries them — but no process to *serve* the metrics over HTTP unless a bench run was in flight with `--metrics-port N`. This release adds a standalone CLI exporter so the dashboard is wireable without a bench in progress.

## Highlights

- **`agentsla metrics serve`** (`agentsla/cli/metrics.py`) — long-running HTTP server on `127.0.0.1:9100` (the `node_exporter` convention) exposing the three metric families on `/metrics` for Prometheus to scrape. `start_http_server` from `prometheus_client` is the underlying primitive. The process blocks until SIGTERM / KeyboardInterrupt; PM2 / systemd / docker are the intended lifecycle owners.
- **`agentsla metrics snapshot`** — one-shot dump of the registry's exposition text to stdout, `--format text` (default, Prometheus exposition) or `--format json` (parsed family/sample structure for tooling that prefers JSON). Exits 0.
- **Unified CLI dispatch** (`agentsla/__main__.py`) — `agentsla metrics ...` reaches the metrics module; the usage banner lists `metrics` alongside `run, replay, bench, bench-seeded-errors, bench-real, report`.
- **9-case test suite** (`tests/unit/cli/test_metrics_cli.py`) — pins the snapshot CLI shape, the serve surface contract (HTTP `/metrics` returns 200 with the three families + a populated sample), CLI dispatch wiring, and a global REGISTRY exposition-format sanity check.
- **README § "Benchmarking"** documents `agentsla metrics serve` / `snapshot` alongside `--metrics-port`.
- **`test.yml` integration** — the audit/test/integration jobs now run the metrics-CLI test layer.

## Quality gates at HEAD

- `ruff check .` — clean
- `mypy --strict agentsla/core agentsla/policy agentsla/verify` — 0 findings / 18 source files
- `pytest tests/` — **542 passed** (533 → 542, +9 from new metrics CLI test layer)
- Release suite (consistency + provenance + makefile) — 15/15 GREEN
- Tag `v1.0.1` at HEAD on `main`

## Honesty notes

- No new PRD-v2 §7 honest-gap rows opened by this release; the LLM-judge live-output gate closed in v1.0.0 stays closed.
- Default port 9100 matches `node_exporter` convention so a single Prometheus scrape config picks AgentSLA up alongside other exporters.

**Install:** `pip install agentsla==1.0.1` (PyPI publish step still queued for the trusted-publisher workflow once configured; v1.0.0 install also works as a non-blocking fallback).