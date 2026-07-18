# AgentSLA — Comparative Analysis

A side-by-side framing of AgentSLA against the four most-adopted
commercial / open-source LLM-observability stacks as of 2026-07. The
intent is to position AgentSLA honestly: where it overlaps with the
incumbents (observability, traces), where it diverges (structural
replay, SLO-grade verification), and where it does not try to compete
(realtime dashboards, hosted SaaS, eval-marketplace).

Scope of comparison: the **runtime guarantees** AgentSLA ships — the
four guarantees named in `WRITEUP.md § Headline`:

1. Verification gate (numeric claim recomputation today).
2. Structural replay (strict + tolerant modes).
3. Budget enforcement (token + cost + wall-clock).
4. Failure taxonomy (14 categories, two-stage classification).

## At-a-glance matrix

| Capability | AgentSLA | LangSmith | Langfuse | Helicone | Braintrust |
|---|:--:|:--:|:--:|:--:|:--:|
| **Append-only trace log** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Hosted SaaS** | — | ✓ | ✓ | ✓ | ✓ |
| **Structural replay** | ✓ (strict + tolerant) | — | — | — | — |
| **Post-execution verification gate** | ✓ (numeric claim recomputation today) | partial (eval chains) | partial (eval chains) | — | ✓ (scorers) |
| **Tool-call policy gate (egress regex)** | ✓ | — | — | — | — |
| **Token / cost budget enforcement** | ✓ | partial (cost tracking) | partial (cost tracking) | ✓ (cost-only) | partial |
| **Wall-clock deadline enforcement** | ✓ | — | — | — | — |
| **Failure taxonomy (machine-readable)** | ✓ (14 categories) | partial (custom tags) | partial (custom tags) | — | partial (custom scorers) |
| **Two-stage classifier (heuristic + LLM judge)** | ✓ | — | — | — | ✓ |
| **Offline / hermetic structural replay of captured traces** | ✓ | — | — | — | — |
| **Local-first (no cloud required)** | ✓ | — | ✓ (self-host) | — | — |
| **Open-source under MIT** | ✓ | partial (closed SaaS) | ✓ (MIT) | partial (AGPL-3) | partial |

Legend: ✓ = first-class support; partial = present but not the
primary use case; — = absent.

## Where AgentSLA overlaps (and why that is fine)

**Observability (LangSmith / Langfuse / Helicone / Braintrust all win
here).** AgentSLA emits a typed `Trace` with `ToolCall`, `ToolResult`,
`ModelMessage`, and `Verdict` events; the trace store is DuckDB with
Parquet export. This is the same shape everyone else ships. We do not
try to beat the incumbents on realtime dashboards, alerting, or hosted
multi-tenant UX — that is not the thesis.

**Two-stage classification (Braintrust overlap).** Braintrust's
scorers and AgentSLA's `Classifier` share a common shape: heuristic
triggers fast-path the easy cases, an LLM judge covers the long tail.
AgentSLA's contribution is the **deterministic 14-category failure
taxonomy** that the heuristic stage emits — Braintrust leaves the
taxonomy to the user.

## Where AgentSLA diverges — the three guarantees

### 1. Structural replay (no incumbent ships this)

The `TraceReader.iter_events(trace_id)` path + canonical-JSON
`args_hash` lets AgentSLA structurally replay a recorded trace by
recomputing each recorded tool-call hash, surfacing drift, and
returning the stored final answer. The two modes:

* **Strict** — every `ToolCall.args_hash` must match exactly. Any
  drift raises a replay error and aborts.
* **Tolerant** — drift is recorded but the replay continues so the
  report can be used for triage.

Adapter-driven re-execution with stubbed tool results ships as
`agentsla replay --execute` for deterministic (rawloop-recorded)
traces; live-model traces refuse it and fall back to structural replay.

LangSmith/Langfuse ship "replay" as "re-run this trace in the UI to
re-observe the same calls" — that is a debugging affordance, not a
verification primitive. Helicone and Braintrust ship observability
but no replay primitive at all.

### 2. Post-execution verification gate (not the same as eval chains)

LangSmith and Langfuse both have "evaluators" / "evals" — but those
are typically **upstream** LLM-as-judge pipelines that score model
output before it reaches the user. AgentSLA's `VerificationGate` runs
**after** the agent finishes, **recomputes** numeric claims against
the recorded tool outputs, and emits a `Verdict` event with a
`coverage` field (`verified / total_claims`). This catches logical
errors that an LLM-judge eval would rubber-stamp ("Total is 100."
when the source arithmetic yields 50).

The distinction matters for regulated workloads (finops, compliance
audit). Eval chains answer "does this *look* right?". Verification
recomputes "is this *computable* right?".

### 3. Egress / policy gate (no incumbent competes)

`PolicyGate.on_tool_call` runs every tool-call argument value through
a regex pack (AWS keys, JWTs, SSN, Luhn-validated card PANs by
default; user-extensible in YAML). The gate returns one of three
decisions: `ALLOW`, `DENY`, `REWRITE`. A denied tool call blocks
the agent's loop; the bench's 25 injection-task runs measure this as
`injection_resistance`.

LangSmith/Langfuse can **log** policy decisions if you build the
gate yourself; they do not ship one. Helicone focuses on cost
(caching, rate limits) not content policy. Braintrust focuses on
evals, not policy.

## What AgentSLA does NOT do (honest gaps)

* **No hosted SaaS.** Single-tenant, runs in your process. This is
  deliberate — the trace store contains tool-call arguments which
  may carry PII; shipping them to a third-party SaaS would defeat
  the threat model.
* **No realtime alerting.** Prometheus counters are emitted but
  Grafana wiring is the user's job (`--metrics-port 9090` + scrape).
* **No eval marketplace.** The classifier's 14 categories ship as
  the only taxonomy. Extending requires writing a new trigger and a
  test fixture.
* **No eval chain runner.** LLM-judge pipelines are user-configured
  via `Classifier(judge=ClaudeJudge())`; no built-in recipes for
  "harmlessness", "helpfulness", or "factual accuracy" because those
  are domain-specific.
* **No automatic prompt optimization.** The bench measures the gate,
  not the upstream prompt. Optimization would need a separate
  project.

## Decision rubric — when to use what

| If you need… | Use |
|---|---|
| Hosted SaaS with dashboards + alerting for a single agent | LangSmith or Langfuse |
| Open-source self-host, basic observability | Langfuse |
| Cost / rate limiting for an OpenAI-key-only deployment | Helicone |
| Eval pipeline with custom scorers, scoring-as-a-service | Braintrust |
| **Structural replay of tool-calling agent traces (hash validation + stored-answer recovery; not re-execution)** | AgentSLA |
| **Post-execution claim recomputation (numerical correctness)** | AgentSLA |
| **Egress / data-exfiltration policy on tool calls** | AgentSLA |
| **A reliability layer that wraps any tool-calling agent (LangGraph, Claude Agent SDK, raw-loop) without owning the agent runtime** | AgentSLA |

The last row is the thesis. AgentSLA is a **runtime wrapper**, not a
new agent framework. It does not compete with the agents; it makes
them more honest about what they did and more reliable about what
they will do.
