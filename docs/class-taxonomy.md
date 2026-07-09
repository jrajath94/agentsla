# CLASS-TAXONOMY — 14-Category Failure Taxonomy

**Status:** Phase 4 hard gate. Committed before any `agentsla/classify/*.py` import.
**Derived from:** MAST taxonomy, arXiv 2503.13657 (Robey et al., 2026) — multi-agent system
failure categorisation — adapted for single-agent tool-calling traces.
**Last updated:** 2026-07-09

---

## Purpose

Every failed trace gets labelled with one of 14 categories. Labels feed
(a) the Prometheus failure-counter
(`agentsla_failures_total{category="..."}`), (b) the bench-harness
headline table, (c) the LLM-judge training/eval prompts, (d) the
hand-labelled reference dataset under `tests/fixtures/classify/`.

A category is assigned at the **trace level** (not per-event). If a
single trace exhibits multiple failure modes, the highest-severity
label wins (severity order declared below). Ties broken by first
occurrence in the `(trace_id, seq)` ordering.

The taxonomy is intentionally narrow: 14 categories, machine-checkable
heuristic triggers, one fallback path to an LLM judge. We do not
attempt automatic category discovery — operators want stable labels
they can build alerts on, not emergent cluster IDs.

---

## Overview table

Each row: `{category, definition, example, severity}`.

| #  | Category                  | Definition                                                                                | Example                                                                    | Severity            |
|----|---------------------------|-------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|---------------------|
| 1  | `format_violation`        | Final answer fails its declared JSON schema / structured-output contract                  | `expected: {"answer": str}`, agent returns prose                            | 4 (low — recoverable) |
| 2  | `tool_call_error`         | Agent invokes a tool with wrong name, missing required arg, or wrong type                 | `tool="search"` but policy only allows `fetch`                              | 6                   |
| 3  | `tool_response_misuse`    | Agent invokes tool correctly but misinterprets the result (treats error as success)        | `{"error": "not_found"}` parsed as `{}` and used downstream                 | 7                   |
| 4  | `hallucinated_fact`       | Final answer asserts a fact not derivable from any tool result in the trace                | `Total = 4,500` when no tool returned 4,500                                 | 9                   |
| 5  | `reasoning_error`         | Multi-step reasoning contains a logical contradiction                                      | `x=5, y=10, answer: "x > y"`                                                | 8                   |
| 6  | `planning_error`          | Agent's tool-call sequence cannot achieve its stated goal (dead-end plan, missing step)   | Plan calls `summarise` but never called `fetch` first                       | 5                   |
| 7  | `context_overflow`        | Trace's accumulated event payload exceeds the model context window                         | 200 tool calls in one trace before final answer                             | 6                   |
| 8  | `budget_exceeded`         | Token / cost / latency budget exhausted mid-run; agent emitted degraded or truncated answer | `BudgetManager.degrade()` fired; final answer ends with `"…"`              | 5                   |
| 9  | `permission_denied`       | Agent attempted an action the policy gate blocked                                          | `PolicyGate.deny` on tool call; agent retried twice, gave up                | 4                   |
| 10 | `retry_loop`              | Same (or near-identical) tool call repeated ≥3 times without progress                      | `tool=fetch(q="x")` invoked 5 times in a row                                | 5                   |
| 11 | `policy_violation`        | Final answer or intermediate observation contained disallowed content                      | Egress regex matched AWS access key in tool result                          | 9                   |
| 12 | `timeout`                 | Wall-clock or per-call deadline elapsed                                                    | `tool_call` exceeded 30s budget; runner killed call                         | 3 (transient)       |
| 13 | `partial_completion`      | Final answer addresses only some required parts of the task                                | Task asks for `a, b, c`; answer covers only `a, b`                           | 4                   |
| 14 | `unexpected_tool_failure` | Tool itself raised an exception not classified above                                       | `ToolResult.error="500 internal server error"`                              | 3 (transient)       |

Severity (1=low, 10=high) is for tie-breaking when multiple categories
fit. `hallucinated_fact` and `policy_violation` are highest-severity;
`timeout` and `unexpected_tool_failure` are transient.

---

## Per-category deep dive

### 1. `format_violation` (severity 4)

**Heuristic trigger:** declared JSON Schema exists AND final answer
fails `jsonschema.validate`.

**Observable signals:** `agentsla_format_violations_total` counter
increments; downstream consumers reject the trace at deserialisation.

**False-positive risk:** A trace whose final answer is prose but whose
task description did not require a JSON schema. The heuristic must
consult the task's declared `output_schema` before triggering.

**Mitigation:** surface the schema-violation diff in the agent's
working memory on the next turn; expose a `format_violation_recovery`
stat so operators can see how often the agent self-corrects.

### 2. `tool_call_error` (severity 6)

**Heuristic trigger:** `ToolCall` event where `tool_name` ∉
`policy.allowed_tools` OR arg fails JSON schema for that tool.

**Observable signals:** `agentsla_policy_denies_total{reason="unknown_tool"}`
or `{reason="schema_violation"}` counters increment.

**False-positive risk:** A retried call after the agent corrects the
arg type. The heuristic fires once per call, not per attempt; recovery
is captured by a downstream `format_violation` check on the retry.

**Mitigation:** the policy gate already emits DENY with a structured
reason; surface the exact arg-diff in the agent's next turn so it can
fix the call without re-reading the full tool schema.

### 3. `tool_response_misuse` (severity 7)

**Heuristic trigger:** `ToolResult.error` present AND subsequent
`ToolCall` does NOT handle error (i.e. uses the error payload as if it
were a success payload).

**Observable signals:** A subsequent ToolCall's args reference fields
present only on the error schema (e.g. `error_code`, `error_message`)
as if they were data fields.

**False-positive risk:** An agent that calls a different tool after
the error — the heuristic looks at the immediately next call. If the
agent chains through a successful tool call before retrying, the
trigger does not fire.

**Mitigation:** prefer tools whose error schema is structurally
distinct from the success schema; surface the error explicitly in the
tool result wrapper so the agent cannot conflate them.

### 4. `hallucinated_fact` (severity 9)

**Heuristic trigger:** `Verdict.verified=False` AND no
`tool_response_misuse` AND no `policy_violation` triggered.

**Observable signals:** the numeric verifier flagged one or more
claims as `incorrect`; no other category claimed the trace.

**False-positive risk:** a verifier with an under-specified
`source_resolver` will flag legitimate claims as hallucinated. The
default `identity_source` self-certifies; production deployments must
inject a domain-aware resolver.

**Mitigation:** when a claim is flagged, surface the trace's tool
results alongside the claim so the agent can either ground the claim
or correct itself.

### 5. `reasoning_error` (severity 8)

**Heuristic trigger:** final answer contains contradictory numeric
claims (detected by `VerificationGate`'s per-claim coverage check).

**Observable signals:** `agentsla_reasoning_contradictions_total`
counter increments; the verifier emits per-claim verdicts.

**False-positive risk:** a multi-step answer where intermediate
intermediate numbers are deliberately different (e.g. "5 * 10 = 50,
then 50 - 5 = 45"). The heuristic checks for direct contradiction,
not numeric deltas, so legitimate intermediate-step answers do not
trigger.

**Mitigation:** surface the contradicting pair to the agent verbatim;
require an explicit confirmation step before the answer is accepted.

### 6. `planning_error` (severity 5)

**Heuristic trigger:** trace ends without a `Verdict` event AND ≥1
`ToolResult.error` was encountered.

**Observable signals:** the trace terminates mid-flow; no final
assistant message; the verifier never runs.

**False-positive risk:** traces that legitimately terminate early
(e.g. a budget exhaustion that the operator wants to count as
`budget_exceeded` rather than `planning_error`). The classifier
prefers `budget_exceeded` when an exhaustion event is present, so the
fall-through to `planning_error` only fires for unobserved causes.

**Mitigation:** surface the last successful tool call and its error;
the agent can extend the plan on retry.

### 7. `context_overflow` (severity 6)

**Heuristic trigger:** sum of event payload bytes > model context
window (configured per model).

**Observable signals:** `agentsla_context_bytes_total` exceeds the
configured threshold; the trace would not fit in a single inference
request.

**False-positive risk:** traces with many small tool results that
sum to over the threshold but compress well. We measure uncompressed
bytes; compressed-size overflow is out of scope.

**Mitigation:** enable the `BudgetManager`'s mid-run summarisation;
emit a partial Verdict before the trace exceeds the window.

### 8. `budget_exceeded` (severity 5)

**Heuristic trigger:** `BudgetManager.exhausted` event present.

**Observable signals:** `agentsla_budget_exhaustion_total{axis=...}`
counter increments (`axis` ∈ {tokens, cost_usd, latency_s}).

**False-positive risk:** rare — the BudgetManager emits the event
only on configured threshold crossing.

**Mitigation:** raise the operator's threshold for cost-sensitive
runs; degrade to a cheaper model mid-run via the budget manager's
`degrade()` action.

### 9. `permission_denied` (severity 4)

**Heuristic trigger:** `PolicyGate.decision="DENY"` event present ≥2
times for the same tool.

**Observable signals:** `agentsla_policy_denies_total{tool=...}`
shows the agent retried after the first DENY.

**False-positive risk:** a deliberate retry after a tool arg
correction that the gate mis-classified as the same call. The
heuristic counts by `(tool_name, args_hash)`; distinct args hashes
do not trigger.

**Mitigation:** surface the policy rule that fired so the agent can
adjust its plan rather than retry.

### 10. `retry_loop` (severity 5)

**Heuristic trigger:** ≥3 consecutive `ToolCall` events with identical
`(tool_name, args_hash)`.

**Observable signals:** the verifier's `args_hash` sequence shows N
identical hashes; the agent is not making progress.

**False-positive risk:** idempotent read calls (e.g. `get_status`
polls) — operators should mark these idempotent in `policy.yaml` so
the heuristic skips them.

**Mitigation:** surface the loop to the agent and require an
explicit plan change before the next call.

### 11. `policy_violation` (severity 9)

**Heuristic trigger:** any event payload matches an egress regex pack
hit (PAN with Luhn, SSN, AWS access key, JWT, or operator-defined).

**Observable signals:** `agentsla_egress_hits_total{rule=...}` counter
increments; the trace is short-circuited.

**False-positive risk:** legitimate content that matches a wide
regex (e.g. base64-encoded JWT in a test fixture). Operators should
either narrow the regex or switch to `mode: shadow` to measure the
false-positive rate before enforcement.

**Mitigation:** `mode: shadow` logs without short-circuiting; the
operator can grep `tests/fixtures/classify/labels.jsonl` for the hit
count and tune the rule.

### 12. `timeout` (severity 3, transient)

**Heuristic trigger:** trace duration > `deadline_s` (default 120).

**Observable signals:** `agentsla_trace_duration_seconds` histogram
p99.9 exceeds deadline; runner kills the trace.

**False-positive risk:** traces that legitimately need more time on
a slow upstream. Operators should set per-task deadlines rather than
relying on the global default.

**Mitigation:** retry with a backoff; if the upstream is consistently
slow, switch to a streaming response shape.

### 13. `partial_completion` (severity 4)

**Heuristic trigger:** task defines required-answer fields; final
answer missing ≥1.

**Observable signals:** the verifier's `coverage` metric is below
the task's required-answer threshold.

**False-positive risk:** tasks whose required-answer fields are
optional. The heuristic must consult the task's `required_fields`
list rather than inferring it from the prompt text.

**Mitigation:** surface the missing fields to the agent; allow a
second pass that fills only the gaps.

### 14. `unexpected_tool_failure` (severity 3, transient)

**Heuristic trigger:** `ToolResult.error` not classified by any
other category.

**Observable signals:** an unclassified `ToolResult.error` event;
the classifier falls back to this category as a catch-all.

**False-positive risk:** low — this is the explicit catch-all
category. Operators should still triage: a high rate here means the
upstream taxonomy is missing a category.

**Mitigation:** file an issue with the error schema; promote
recurring patterns to a new category in a future milestone.

---

## Selection Rule (Classifier Implementation Contract)

Given a trace with N events:

1. Run heuristics first. If exactly one category matches → label it.
2. If multiple match → pick highest-severity (tie: lowest category
   number).
3. If none match and `verified=False` → label `hallucinated_fact`
   (default for verification-failed traces without other signal).
4. If none match and `verified=True` → label as `none` (success).

---

## Label Format (event shape)

A classification is **not** a new event kind. It is a side-effect
that increments a Prometheus counter and writes a JSONL record under
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

---

## Heuristic Triggers (machine-checkable)

For the heuristic classifier stage:

| Category                  | Trigger                                                                                       |
|---------------------------|-----------------------------------------------------------------------------------------------|
| `format_violation`        | declared JSON Schema exists AND final answer fails `jsonschema.validate`                       |
| `tool_call_error`         | `ToolCall` event where `tool_name` ∉ `policy.allowed_tools` OR arg fails JSON schema          |
| `tool_response_misuse`    | `ToolResult.error` present AND subsequent `ToolCall` does NOT handle error                   |
| `hallucinated_fact`       | `Verdict.verified=False` AND no `tool_response_misuse` AND no `policy_violation` triggered   |
| `reasoning_error`         | final answer contains contradictory numeric claims (detected by VerificationGate)             |
| `planning_error`          | trace ends without a `Verdict` event AND ≥1 `ToolResult.error` was encountered                 |
| `context_overflow`        | sum of event payload bytes > model context window                                              |
| `budget_exceeded`         | `BudgetManager.exhausted` event present                                                       |
| `permission_denied`       | `PolicyGate.decision="DENY"` event present ≥2 times for same tool                              |
| `retry_loop`              | ≥3 consecutive `ToolCall` events with identical `(tool_name, args_hash)`                      |
| `policy_violation`        | any event payload matches an egress regex pack hit                                             |
| `timeout`                 | trace duration > `deadline_s` (default 120)                                                    |
| `partial_completion`      | task defines required-answer fields; final answer missing ≥1                                   |
| `unexpected_tool_failure` | `ToolResult.error` not classified elsewhere                                                    |

Triggers are implemented in `agentsla/classify/heuristics.py` and
unit-tested against the 14-row fixture in
`tests/unit/classify/test_heuristics.py`.

---

## LLM-Judge Stage (≤20% of traces)

For traces where the heuristic stage returns low confidence OR no
heuristic triggered AND the verification gate reports `incorrect > 0`,
the classifier dispatches to an LLM judge (default model:
`claude-haiku-4-5`, `temperature=0`). Prompt is **content-hash-pinned**
so the same input always produces the same prompt — verifiable via
`git log --follow classify/prompts/`.

The judge returns `(category, confidence)`; we accept when
`confidence ≥ 0.7`, otherwise fall back to the highest-severity
heuristic candidate.

---

## Reference Dataset (DATASET-01)

100 hand-labelled traces committed under
`tests/fixtures/classify/labels.jsonl`. Human labels are gold.
Acceptance: classifier agreement ≥80% vs these labels (measured by
`scripts/eval_classifier_agreement.py`).

The shipped bench reports 100% agreement against this set. This is
the ceiling of what the metric can express: the held-out set was
constructed from the same triggers the classifier uses, which makes
the eval circular (see `docs/failure-modes.md § 5` for the full
discussion). A real eval would replay live Claude API traces through
the harness — deferred to v0.2.

---

## Out of Scope (v0.1)

- Multi-agent / swarm failure modes (MAST categorisation extends to
  14 → 33 when multi-agent; we collapse to 14 for single-agent).
- Severity scoring beyond tie-breaking.
- Automatic category discovery (no clustering).
- Cost-weighted failure rates (deferred to v2).