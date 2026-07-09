# AgentSLA

SLO-aware reliability runtime for tool-calling LLM agents.

## Overview

AgentSLA wraps any tool-calling agent (Claude SDK, LangGraph, or custom) with a verification layer that enforces reliability contracts. It provides deterministic replay for debugging, budget enforcement, and per-category failure analysis.

## Architecture

```
Request
  ├─ Policy Gate (schema validation, injection screening, egress rules)
  ├─ Agent Executor (Claude SDK, LangGraph, or custom adapter)
  ├─ Verification Gate (numeric recomputation, schema conformance, grounding)
  ├─ Trace Store (DuckDB + Parquet for analysis and replay)
  └─ Response (with deterministic Verdict)
```

## Key Components

- **Policy**: Allowed tools, per-tool JSON Schema validation, regex-based secret screening (SSN, credit card, AWS key, JWT patterns)
- **Verification**: Numeric recomputation (extract claims → map to tool calls → recompute), schema conformance checks, grounding against sources
- **Trace Store**: Append-only event log (tool calls, results, model messages, verdicts) for deterministic replay and metrics
- **Failure Classifier**: 14-category taxonomy with heuristics + optional LLM-based judgment
- **Adapters**: Claude Agent SDK, LangGraph, and raw agent loops

## Installation

```bash
pip install agentsla

# Or with all optional adapters
pip install "agentsla[all]"
```

## Quick Start

```python
from agentsla.policy import PolicyGate, PolicyConfig
from agentsla.verify import VerificationGate
from agentsla.trace import TraceWriter

# Set up gates
policy_config = PolicyConfig(
    allowed_tools=["web_search", "calculator"],
    egress_rules=["SSN", "credit_card"]
)
policy = PolicyGate(policy_config)
verifier = VerificationGate()
trace_writer = TraceWriter("traces.duckdb")

# Wrap your agent execution
response = agent.run(prompt)
policy.check(response)
verdict = verifier.check(response)
trace_writer.log(response, verdict)
```

## Testing

```bash
pytest tests/ -v --cov=agentsla
```

Coverage target: 85% on core modules (policy, verify, trace).

## Benchmarking

```bash
agentsla bench --all
```

Runs 30 tasks (10 financial ops, 10 incident triage, 10 doc QA) with wrapped and unwrapped agents. Outputs TTFT latency overhead, cost overhead, and verification recovery rate.

## Design Notes

**Verification Coverage as a First-Class Metric**: "Verified" is meaningless without knowing how much of the response was actually checked. AgentSLA emits coverage_pct alongside every verdict.

**Append-Only Trace Log**: Single source of truth. Replay, metrics, and debugging all derive from the same immutable log.

**Numeric Recomputation Over String Matching**: Extract numeric claims from the response, map to source tool outputs, recompute the formula, check tolerance. Catches logical errors, not just hallucinations.

## Limitations

- Verification handles numeric claims. Qualitative judgments (e.g., "sentiment is positive") require an external LLM check.
- Deterministic replay requires deterministic tool responses. Non-deterministic services (live APIs) will show divergence under replay.
- Policy gate runs only on declared tool calls. If an agent generates code that makes external requests outside the declared tools, this layer cannot intercept.

## References

- Claude Agent SDK: https://github.com/anthropics/agents
- LangGraph: https://github.com/langchain-ai/langgraph
- DuckDB: https://duckdb.org
- Pydantic: https://docs.pydantic.dev

## License

MIT
