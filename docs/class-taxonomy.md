# CLASS-TAXONOMY — 14-Category Failure Taxonomy

**Status:** Phase 4 hard gate. Committed before any `agentsla/classify/*.py` import.
**Derived from:** MAST taxonomy, arXiv 2503.13657 (Robey et al., 2026) — multi-agent system
failure categorisation — adapted for single-agent tool-calling traces.
**Last updated:** 2026-07-08

---

## Purpose

Every failed trace gets labelled with one of 14 categories. Labels feed (a) the
prometheus failure-counter (`agentsla_failures_total{category="..."}`),
(b) the bench-harness headline table, (c) the LLM-judge training/eval prompts,
(d) the hand-labelled reference dataset under `tests/fixtures/classify/`.

A category is assigned at the *trace* level (not the per-event level). If a
single trace exhibits multiple failure modes, the **highest-severity** label
wins (severity order declared below). Ties broken by first occurrence in
the `(trace_id, seq)` ordering.

## The 14 Categories

Each row: `{category, definition, examples, severity}`.

| # | Category | Definition | Example | Severity |
|---|----------|------------|---------|----------|
| 1 | `format_violation` | Final answer fails its declared JSON schema / structured-output contract | `expected: {"answer": str}`, agent returns prose | 4 (low — recoverable) |
| 2 | `tool_call_error` | Agent invokes a tool with the wrong name, missing required arg, or wrong type | `tool="search"` but policy only allows `fetch` | 6 |
| 3 | `tool_response_misuse` | Agent invokes tool correctly but misinterprets the result (e.g. treats error as success) | `{"error": "not_found"}` parsed as `{}` and used downstream | 7 |
| 4 | `hallucinated_fact` | Final answer asserts a fact not derivable from any tool result in the trace | `Total = 4,500` when no tool returned 4,500 | 9 |
| 5 | `reasoning_error` | Multi-step reasoning contains a logical contradiction (e.g., A>B and B>A both asserted) | `x=5, y=10, answer: "x > y"` | 8 |
| 6 | `planning_error` | Agent's tool-call sequence cannot achieve its stated goal (dead-end plan, missing step) | Plan calls `summarise` but never called `fetch` first | 5 |
| 7 | `context_overflow` | Trace's accumulated event payload exceeds the model context window | 200 tool calls in one trace before final answer | 6 |
| 8 | `budget_exceeded` | Token / cost / latency budget exhausted mid-run; agent emitted a degraded or truncated answer | `BudgetManager.degrade()` fired; final answer ends with `"…"` | 5 |
| 9 | `permission_denied` | Agent attempted an action the policy gate blocked | `PolicyGate.deny` on tool call; agent retried twice, gave up | 4 |
| 10 | `retry_loop` | Same (or near-identical) tool call repeated ≥3 times without progress | `tool=fetch(q="x")` invoked 5 times in a row | 5 |
| 11 | `policy_violation` | Final answer or intermediate observation contained disallowed content (SSN, PII, secret) | Egress regex matched AWS access key in tool result | 9 |
| 12 | `timeout` | Wall-clock or per-call deadline elapsed | `tool_call` exceeded 30s budget; runner killed call | 3 (transient) |
| 13 | `partial_completion` | Final answer addresses only some required parts of the task | Task asks for `a, b, c`; answer covers only `a, b` | 4 |
| 14 | `unexpected_tool_failure` | Tool itself raised an exception not classified above | `ToolResult.error="500 internal server error"` | 3 (transient) |

Severity (1=low, 10=high) is for tie-breaking when multiple categories fit.
`hallucinated_fact` and `policy_violation` are highest-severity; `timeout`
and `unexpected_tool_failure` are transient.

## Selection Rule (Classifier Implementation Contract)

Given a trace with N events:
1. Run heuristics first. If exactly one category matches → label it.
2. If multiple match → pick highest-severity (tie: lowest category number).
3. If none match and `verified=False` → label `hallucinated_fact` (default for
   verification-failed traces without other signal).
4. If none match and `verified=True` → label as `none` (success).

## Label Format (event shape)

A classification is **not** a new event kind. It is a side-effect that
increments a Prometheus counter and writes a JSONL record under
`tests/fixtures/classify/labels.jsonl`:

```json
{
  "trace_id": "uuid",
  "category": "hallucinated_fact",
  "confidence": 0.92,
  "source": "heuristic" | "llm_judge",
  "judge_prompt_hash": "sha256:...",
  "labeled_at": "2026-07-08T..."
}
```

The bench harness reads `labels.jsonl` to compute the
`failure_breakdown_pct` headline column.

## Heuristic Triggers (machine-checkable)

For the heuristic classifier stage:

| Category | Trigger |
|----------|---------|
| `format_violation` | declared JSON Schema exists AND final answer fails `jsonschema.validate` |
| `tool_call_error` | `ToolCall` event where `tool_name` ∉ `policy.allowed_tools` OR arg fails JSON schema for that tool |
| `tool_response_misuse` | `ToolResult.error` present AND subsequent `ToolCall` does NOT handle error |
| `hallucinated_fact` | `Verdict.verified=False` AND no `tool_response_misuse` AND no `policy_violation` triggered |
| `reasoning_error` | final answer contains contradictory numeric claims (detected by VerificationGate) |
| `planning_error` | trace ends without a `Verdict` event AND ≥1 `ToolResult.error` was encountered |
| `context_overflow` | sum of event payload bytes > model context window (configured per model) |
| `budget_exceeded` | `BudgetManager.exhausted` event present |
| `permission_denied` | `PolicyGate.decision="DENY"` event present ≥2 times for same tool |
| `retry_loop` | ≥3 consecutive `ToolCall` events with identical `(tool_name, args_hash)` |
| `policy_violation` | any event payload matches an egress regex pack hit |
| `timeout` | trace duration > `deadline_s` (default 120) |
| `partial_completion` | task defines required-answer fields; final answer missing ≥1 |
| `unexpected_tool_failure` | `ToolResult.error` not classified elsewhere |

Triggers are implemented in `agentsla/classify/heuristics.py` and unit-tested
against the 14-row fixture in `tests/unit/classify/test_heuristics.py`.

## LLM-Judge Stage (≤20% of traces)

For traces where the heuristic stage returns low confidence OR no heuristic
triggered AND the verification gate reports `incorrect > 0`, the classifier
dispatches to an LLM judge (default model: `claude-haiku-4-5`,
`temperature=0`). Prompt is **content-hash-pinned** so the same input always
produces the same prompt — verifiable via `git log --follow classify/prompts/`.

The judge returns `(category, confidence)`; we accept when `confidence ≥ 0.7`,
otherwise fall back to the highest-severity heuristic candidate.

## Reference Dataset (DATASET-01)

100 hand-labelled traces committed under `tests/fixtures/classify/labels.jsonl`.
Human labels are gold. Acceptance: classifier agreement ≥80% vs these labels
(measured by `scripts/eval_classifier_agreement.py`).

## Out of Scope (v0.1)

- Multi-agent / swarm failure modes (MAST categorisation extends to 14 → 33
  when multi-agent; we collapse to 14 for single-agent).
- Severity scoring beyond tie-breaking.
- Automatic category discovery (no clustering).
- Cost-weighted failure rates (deferred to v2).