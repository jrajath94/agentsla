# AgentSLA

A reliability runtime for tool-calling LLM agents. Wraps an agent with policy
gates, numeric claim verification, deterministic replay, execution budgets,
and a failure taxonomy. The goal is the one the Anthropic environments
posting states plainly: make whole classes of silent failure impossible. This
repo is the working version of that goal for numeric claims.

[![CI](https://github.com/jrajath94/agentsla/actions/workflows/test.yml/badge.svg)](https://github.com/jrajath94/agentsla/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/jrajath94/agentsla/branch/main/graph/badge.svg)](https://codecov.io/gh/jrajath94/agentsla)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Verified 2026-09-21:** 574 tests collected (560 pass, 6 skip in a full CPU
env; the 8 failures are release-provenance checks that need git tags and
bench-smoke tests that need a live bench env). CI enforces ruff, ruff format,
mypy strict, and a >=85% coverage gate.

## What it does

AgentSLA sits between your agent framework (Claude Agent SDK, LangGraph, or a
raw agent loop) and the outside world, and enforces reliability contracts on
every run:

- **Policy gate** - allowed tools, per-tool JSON Schema validation, regex
  secret screening (SSN, credit card, AWS key, JWT patterns), egress rules.
  A `mode: shadow` option logs DENY decisions without short-circuiting, so
  you can measure false-positive rates against live traffic before enforcing.
- **Verification gate** - numeric claim recomputation. It extracts numeric
  claims from the agent's response, maps each claim to a source tool output,
  recomputes the formula, and checks it within tolerance. The chain result
  carries a `coverage` fraction (verified claims over total claims), because
  "verified" is meaningless without that number.
- **Trace store** - an append-only event log (tool calls, results, model
  messages, verdicts) in DuckDB + Parquet. `agentsla replay TRACE_ID`
  re-validates recorded tool-call hashes (structural replay, works for every
  trace). `agentsla replay TRACE_ID --execute` re-drives the adapter loop
  with recorded tool results stubbed in and asserts a byte-identical final
  answer (deterministic traces only).
- **Budgets** - `BudgetedHooks` wraps the runtime hooks and converts budget
  breaches (calls, tokens, cost, wall time) into policy-style DENYs, so the
  agent degrades to a short-circuit answer instead of crashing.
- **Failure classifier** - a 14-category failure taxonomy with heuristics plus
  an optional LLM judge. The hermetic default uses a stub judge; the full
  two-stage pipeline needs a real judge (Claude Haiku, API key required).

## Measured results (self-reported; commands and evidence artifacts named
below)

**Hermetic bench** - 35 tasks across financial ops, incident triage, and doc
QA, 2 seeds, wrapped vs unwrapped = 140 rows, via
`agentsla bench --all --seeds 2`. Includes 5 injection-payload task variants.

| Metric | Naked | Wrapped | Delta |
| --- | --- | --- | --- |
| Success rate | 100% (70/70) | 86% (60/70) | -14% |
| Gate passed | 0% (no gate) | 100% (70/70) | +100% |
| Verified at truth | n/a | n/a | - |
| Injection resistance | 0% (0/10) | 100% (10/10) | +100% |
| p95 latency (ms) | 5.97 | 8.15 | +2.18 (+36.5%) |
| Mean latency (ms) | 5.37 | 6.94 | +1.57 |
| N runs | 70 | 70 | - |

The wrapped path adds ~2 ms at p95 on the hermetic bench (gate + verifier +
classifier), and ~0 ms on top of a multi-second LLM call. The success-rate
drop is expected: the gate refuses runs the naked agent would have completed.
That is the token/latency/cost/reliability envelope a safeguards team
actually needs to see.

**Real-LLM bench** - MiniMax-M3, 12 tasks x 2 modes x 1 seed = 24 rows,
measured 2026-07-13. Success rate 92% both modes, gate passed 100% wrapped,
p95 latency 2981 ms both modes (+0.0). On the real-LLM path the gate runs on
the free-text response via a synthetic tool call, so it catches the same
egress patterns without re-invoking the model.

**Seeded-error experiment** - the verification gate catches 100% of +/-50%
perturbed numeric claims (100 trials), with 100% specificity on unperturbed
claims (100 trials) (`agentsla bench-seeded-errors`).

## Evidence

README benchmark numbers are reported only when the corresponding result
artifacts and test commands exist in the repository. `bench/results/REPORT.md`
is committed and regenerates from the bench commands above. The underlying
parquet traces are not committed, so cells are traceable to the committed
REPORT.md, not to parquet files. Committing a small results.parquet/CSV under
`bench/results/` (or linking the CI artifact that produced REPORT.md) would
close that gap.

## Limitations (read before citing any number)

- Verification handles numeric claims. Qualitative judgments (e.g. "sentiment
  is positive") need an external LLM check.
- Execution replay covers deterministic traces only. Traces recorded from
  live models fall back to structural replay (hash re-validation), because a
  live model's messages would also need stubbing.
- The policy gate runs only on declared tool calls. Code the agent generates
  that makes external requests outside declared tools is outside this layer.
- The classifier's LLM-judge stage never runs in hermetic mode (stub judge by
  default). Production use needs a real judge.
- Prometheus counters are in-process. The bench does not start a `/metrics`
  HTTP server unless `--metrics-port N` is passed; the Grafana dashboard JSON
  expects live series.

## Quick start

```
pip install agentsla            # or: pip install "agentsla[all]" for adapters
```

```
from agentsla.policy import Policy, PolicyGate
from agentsla.policy.egress import default_egress_rules
from agentsla.verify import VerificationChain, NumericVerifier, identity_source
from agentsla.classify import Classifier, InMemoryLabelSink
from agentsla.core.trace import TraceWriter
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.tools.deterministic import JsonEchoTool
from pathlib import Path

policy = Policy(allowed_tools=["json_echo"], egress_rules=default_egress_rules())
gate = PolicyGate(policy)
verifier = NumericVerifier(source_resolver=identity_source, tolerance=1e-6)
chain = VerificationChain(verifiers=[verifier])
sink = InMemoryLabelSink()
classifier = Classifier(sink=sink)

writer = TraceWriter(Path("/tmp/agentsla-quickstart.duckdb"))
adapter = RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, trace_writer=writer)
final = adapter.run(task_id="demo", hooks=gate)
print(final.text)
```

Bench and metrics:

```
agentsla bench --all --seeds 2        # the command behind the headline table
agentsla bench --all --metrics-port 9090   # expose Prometheus counters live
agentsla bench-seeded-errors          # verification catch-rate experiment
pytest tests/ --cov=agentsla/core --cov=agentsla/policy --cov=agentsla/verify
```

The live bench (`bench-real`) is the only paid path. `--max-paid-calls`
defaults to 3 and a plan with more uncached prompts refuses to start. Run
`--dry-plan` first, smoke second, escalate only if the smoke changes a
conclusion.

## License

MIT
