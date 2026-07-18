# AgentSLA

[![CI](https://github.com/jrajath94/agentsla/actions/workflows/test.yml/badge.svg)](https://github.com/jrajath94/agentsla/actions/workflows/test.yml)
[![Release v1.1.0](https://img.shields.io/badge/release-v1.1.0-blue)](https://github.com/jrajath94/agentsla/releases/tag/v1.1.0)
[![codecov](https://codecov.io/gh/jrajath94/agentsla/branch/main/graph/badge.svg)](https://codecov.io/gh/jrajath94/agentsla)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

SLO-aware reliability runtime for tool-calling LLM agents.

## Overview

AgentSLA wraps any tool-calling agent (Claude SDK, LangGraph, or custom) with a verification layer that enforces reliability contracts. It provides trace replay for debugging (structural for every trace; full execution replay for deterministic traces), budget enforcement wired into the runtime hooks, and per-category failure analysis.

## Architecture

```
Request
  ├─ Policy Gate (schema validation, injection screening, egress rules)
  ├─ Agent Executor (Claude SDK, LangGraph, or custom adapter)
  ├─ Verification Gate (numeric claim recomputation)
  ├─ Trace Store (DuckDB + Parquet for analysis and structural replay)
  └─ Response (with deterministic Verdict)
```

## Key Components

- **Policy**: Allowed tools, per-tool JSON Schema validation, regex-based secret screening (SSN, credit card, AWS key, JWT patterns)
- **Verification**: Numeric recomputation today (extract claims → map to a source resolver → recompute)
- **Trace Store**: Append-only event log (tool calls, results, model messages, verdicts) for replay and metrics. `agentsla replay TRACE_ID` re-validates recorded tool-call hashes (structural); `agentsla replay TRACE_ID --execute` re-drives the adapter loop with recorded tool results stubbed in and asserts a byte-identical final answer (deterministic traces only)
- **Budget**: `BudgetedHooks` wraps any inner hooks (e.g. the policy gate) and converts budget breaches (calls / tokens / cost / wall-time) into policy-style DENYs, so the agent degrades to a short-circuit answer instead of crashing
- **Failure Classifier**: 14-category taxonomy with heuristics + optional LLM-based judgment
- **Adapters**: Claude Agent SDK, LangGraph, and raw agent loops

## Installation

Requires **Python 3.11+** (`datetime.UTC` is used in the core event
types; 3.10 and below fail at import time).

```bash
pip install agentsla

# Or with all optional adapters
pip install "agentsla[all]"
```

If your system Python is older than 3.11, use a venv or `uv`:

```bash
# uv (recommended)
uv sync --extra all
uv run python -m pytest            # always uses the venv's interpreter

# venv
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

## Quick Start

```python
from pathlib import Path

from agentsla.policy import Policy, PolicyGate
from agentsla.policy.egress import default_egress_rules
from agentsla.verify import VerificationChain, NumericVerifier, identity_source
from agentsla.classify import Classifier, InMemoryLabelSink
from agentsla.core.trace import TraceWriter, TraceReader
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.tools.deterministic import JsonEchoTool

# Build the four guarantees.
policy = Policy(allowed_tools=["json_echo"], egress_rules=default_egress_rules())
gate = PolicyGate(policy)

verifier = NumericVerifier(source_resolver=identity_source, tolerance=1e-6)
chain = VerificationChain(verifiers=[verifier])

sink = InMemoryLabelSink()
classifier = Classifier(sink=sink)

# Wrap an agent run.
trace_path = Path("/tmp/agentsla-quickstart.duckdb")
writer = TraceWriter(trace_path)
adapter = RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, trace_writer=writer)
final = adapter.run(task_id="demo", hooks=gate)
print(final.text)
```

## Authoring a policy

The full policy schema is declarative YAML. The minimum viable policy
permits a tool set and inherits the shipped egress pack:

```yaml
allowed_tools:
  - web_search
  - calculator
```

Every field is optional. To add per-tool schema validation, egress
detectors, and trace caps, see [`examples/policy.yaml`](examples/policy.yaml)
— every section is annotated. Load with:

```python
from agentsla.policy import load_policy, PolicyGate

policy = load_policy("examples/policy.yaml")
gate = PolicyGate(policy)
```

The schema validates at load time via Pydantic v2 (`extra="forbid"`,
`frozen=True`). Unknown fields raise `ValidationError`; runtime
mutation is not permitted. The shipped default pack covers PAN with
Luhn validation, SSN, AWS access keys, and JWTs. To start from the
default pack and append your own detectors, omit `egress_rules`
entirely (defaults are inserted) and add a tenant-specific rule at
the end:

```yaml
egress_rules:
  # ... default pack entries ...
  - name: internal_project_code
    regex: '\bPROJ-[0-9]{4,6}\b'
    severity: deny
```

`mode: shadow` logs DENY decisions without short-circuiting the
trace — useful when rolling out a new rule and you want to measure
false-positive rates against live traffic before enforcement.

## Testing

```bash
pytest tests/ -v --cov=agentsla/core --cov=agentsla/policy --cov=agentsla/verify
```

Coverage target: 85% on `agentsla/core`, `agentsla/policy`, and `agentsla/verify`.

## Benchmarking

```bash
agentsla bench --all --seeds 2
```

Runs 35 tasks across financial ops, incident triage, and doc QA (5 injection-payload variants embedded) in wrapped and unwrapped modes × 2 seeds — the exact command behind the committed headline table (140 rows). Omitting `--seeds` runs 5 seeds (350 rows). Outputs TTFT latency overhead, cost overhead, and verification recovery rate.

```bash
# Optional: expose Prometheus counters via HTTP for live Grafana dashboards
agentsla bench --all --metrics-port 9090
# Then add 127.0.0.1:9090 as a scrape target in Prometheus.
```

For a standalone metrics server (no bench running), the `agentsla metrics` subcommand serves the same `/metrics` endpoint independently:

```bash
# Long-running Prometheus exporter (default port 9100, default addr 127.0.0.1)
agentsla metrics serve --port 9090 --addr 0.0.0.0
# One-shot dump of the current registry (handy for debugging)
agentsla metrics snapshot --format text
agentsla metrics snapshot --format json | jq '.families[].name'
```

Both subcommands expose the three metric families `dashboards/grafana.json` queries (`agentsla_failures_total`, `agentsla_verify_coverage`, `agentsla_classify_latency_seconds`).

The seeded-error experiment (`agentsla bench-seeded-errors`) is a separate command that validates the verification gate's catch-rate on synthetic perturbed numeric outputs. See `REPORT.md § "Seeded-error experiment"` after running it.

### Headline results (latest measured run)

_Numbers below are from `bench/results/REPORT.md`, regenerated by the commands above. They are **not fabricated** — every cell is traceable to a parquet in `bench/results/`._

**Hermetic bench** — 35 tasks across `financial_ops` / `incident_triage` / `doc_qa` × 2 seeds × 2 modes = 140 rows. Includes 5 injection-payload task variants (20 rows across 2 seeds — both modes × 2 seeds). Regenerated 2026-07-14.

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% (70/70) | 85.7% (60/70) | -14.3% |
| Gate passed | 0% (no gate) | 100% (70/70) | +100% |
| Verified at truth | n/a | n/a | — |
| Injection resistance | 0% (0/10 inj) | 100% (10/10 inj) | +100% |
| p95 latency (ms) | 6.81 | 10.17 | +3.36 (+49.3%) |
| Mean latency (ms) | 5.86 | 8.50 | +2.64 |
| N runs | 70 | 70 | — |

**Real-LLM bench** (MiniMax-M3, 12 tasks across 3 domains × 2 modes × 1 seed = 24 rows; live measured 2026-07-13):

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 92% | 92% | +0% |
| Gate passed | 0% | 100% | +100% |
| Verified at truth | 92% | 92% | — |
| p95 latency (ms) | 2565.2 | 2565.2 | +0.0 |

> Source: `bench/results/REPORT.md § Real-LLM bench`. The honest-gap banner at the top of REPORT.md is suppressed once measured rows land in `real_llm.parquet` — both this README table and the REPORT table auto-regenerate from the same parquet via `agentsla report`. Refreshing the numbers costs paid API calls; follow the frugal ladder in [`docs/GPU_API_COST_OPTIMIZATION.md`](docs/GPU_API_COST_OPTIMIZATION.md) — smoke first (`--tasks-per-domain 1`, 3 prompts), escalate only if the smoke run changes a conclusion.

#### Live bench cost guards

`bench-real` is the only paid path in the repo; accidental large runs are blocked by default:

```bash
# 1. Preview the run — zero network, no API key needed.
python -m agentsla bench-real --tasks-per-domain 1 --seeds 1 --dry-plan

# 2. Smoke run (Rung C): 3 prompts, 6 rows. Fits the default --max-paid-calls 3.
ANTHROPIC_API_KEY=sk-... python -m agentsla bench-real \
    --tasks-per-domain 1 --seeds 1 --out bench/results/real_llm_smoke.parquet

# 3. Larger runs must raise the cap EXPLICITLY (Rung D: 9 prompts; Rung E: 15).
ANTHROPIC_API_KEY=sk-... python -m agentsla bench-real \
    --tasks-per-domain 3 --max-paid-calls 9 --out bench/results/real_llm_standard.parquet
```

- `--max-paid-calls` defaults to 3; a plan with more uncached prompts refuses to start (exit 2).
- Responses are cached under `bench/cache/real_llm` keyed by (model, task, prompt, seed); `--resume` replays from cache with zero paid calls (rows are marked `cached=true`).
- An existing `--out` parquet is never overwritten unless `--overwrite` is passed.
- The run stops after the first API/provider error and keeps the partial parquet (`--no-fail-fast` to opt out).

The wrapped path adds ~3 ms of overhead at p95 in the hermetic bench (gate + verifier + classifier) and ~0 ms on top of a multi-second LLM call. On the real-LLM path the gate runs on the free-text response via a synthetic `ToolCall`, so it catches the same egress patterns without re-invoking the model.

**Seeded-error experiment** — verification gate catches 100% of ±10% and ±20% perturbed numeric claims at 0% false-correction cost (see `seeded_errors_section.md` after running).

### Figures

After `agentsla bench --all`, render PNGs into `bench/results/figures/` (also auto-included by `agentsla report`):

```bash
python -m agentsla.bench.figures \
    --in bench/results/results.parquet \
    --out-dir bench/results/figures
```

Produces `success_rate.png`, `gate_passed.png`, `injection_resistance.png`, `latency_cdf.png`, `cost_per_task.png`. Figure numbers are computed by the same `_aggregate()` function as the README tables — no possibility of drift. See `bench/results/REPORT.md § Figures` after running.

## Design Notes

**Verification Coverage as a First-Class Metric**: "Verified" is meaningless without knowing how much of the response was actually checked. AgentSLA emits coverage_pct alongside every verdict.

**Honest Headline Metric**: The bench reports `gate_passed` (fraction of runs where the gate ran without rejecting) and `verified_at_truth` (fraction of gate-passed runs that also match a canonical answer, when ground truth is declared). The earlier `verified_pct` column conflated "the gate ran" with "the claims are true"; the new columns separate the two. See [WRITEUP.md](WRITEUP.md) for the full framing and `docs/comparative-analysis.md` for how AgentSLA's metrics stack up against LangSmith / Langfuse / Helicone / Braintrust.

**Append-Only Trace Log**: Single source of truth. Replay, metrics, and debugging all derive from the same immutable log.

**Numeric Recomputation Over String Matching**: Extract numeric claims from the response, map to source tool outputs, recompute the formula, check tolerance. Catches logical errors, not just hallucinations.

## Limitations

- Verification handles numeric claims. Qualitative judgments (e.g., "sentiment is positive") require an external LLM check.
- Execution replay (`agentsla replay --execute`) covers traces recorded with a deterministic model (the rawloop reference adapter). Traces recorded from live models (Claude SDK, LangGraph against a real endpoint) refuse execution replay with exit 2 — a live model's messages would also need stubbing — and fall back to structural replay (hash re-validation + stored answer).
- Policy gate runs only on declared tool calls. If an agent generates code that makes external requests outside the declared tools, this layer cannot intercept.
- Classifier uses `StubJudge` by default — the LLM-judge stage never runs in hermetic mode. Production deployments must instantiate `Classifier(judge=ClaudeJudge())` (haiku 4.5, `$ANTHROPIC_API_KEY` required) to exercise the full two-stage pipeline.
- Prometheus counters are in-process. The shipped bench writes to the default registry but does NOT start a `/metrics` HTTP server unless `--metrics-port N` is passed. The Grafana dashboard JSON expects live series; locally, run `python -m agentsla bench --metrics-port 9090` and add a scrape target. For a long-running exporter independent of bench, `agentsla metrics serve --port N` starts an HTTP server against the process-global registry — the same `/metrics` endpoint Prometheus would scrape.

## References

- Claude Agent SDK: https://github.com/anthropics/agents
- LangGraph: https://github.com/langchain-ai/langgraph
- DuckDB: https://duckdb.org
- Pydantic: https://docs.pydantic.dev

## License

MIT
