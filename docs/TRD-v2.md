# AgentSLA — Technical Requirements Document (v2)
*Companion to PRD-v2. Drives from `AgentSLA_Implementation_Brief.md` + actual
post-v0.1 source state. Every module is described as it exists today, with
the gaps the v2 audit must close.*

**Status:** v2 — synthesizes post-v0.1 audit + working-tree changes.
**Audience:** implementation engineers, reviewers, future contributors.
**Cut date:** 2026-07-13.

---

## 1. System architecture

```
            ┌──────────────────────────────────────────────────┐
            │                AgentSLA Runtime                  │
 request →  │ ┌──────────┐  ┌────────────┐  ┌───────────────┐ │
            │ │ Policy   │→│ Executor   │→│ Verification  │ │ → response
            │ │ Gate     │  │ (adapter)  │  │ Gate          │ │
            │ └────┬─────┘  └─────┬──────┘  └───────┬───────┘ │
            │      │              │                 │         │
            │ ┌────▼─────┐  ┌─────▼──────┐  ┌────────▼──────┐ │
            │ │ Budget   │  │ TraceStore │  │ Failure       │ │
            │ │ Manager  │  │ (DuckDB +  │  │ Classifier    │ │
            │ │          │  │ Parquet)   │  │ (heur+judge)  │ │
            │ └──────────┘  └────────────┘  └───────────────┘ │
            │ ┌────────────────────────────────────────────┐  │
            │ │ Prometheus Metrics + JsonlLabelSink        │  │
            │ └────────────────────────────────────────────┘  │
            └──────────────────────────────────────────────────┘

Adapters (3):    RawLoop | LangGraph | Claude Agent SDK
Bench (hermetic + live): {naked, wrapped} × tasks × seeds → parquet
Replay (structural): strict (hash drift = fail) | tolerant (drift recorded)
```

---

## 2. Control-plane contracts

### 2.1 Hooks surface (per adapter)
```python
class RuntimeHooks(ABC):
    on_tool_call(call: ToolCall) -> GateDecision
    on_tool_result(call: ToolCall, result: ToolResult) -> ToolResult | None
    on_final_answer(trace: Trace, verdict: Verdict | None) -> None
    budget: BudgetManager
```

**Design decision (defensible):** hooks-based middleware, not framework forks.
Proves portability — the same agent code runs against RawLoop, LangGraph, and
Claude Agent SDK without source changes. **Tradeoff:** cannot intercept
sub-calls inside a single tool (e.g. a calculator tool calling `web_search`).
We accept this — those internal calls are out of scope for the SLO contract.

### 2.2 Event log (source of truth)

| Event | Schema | Source |
|---|---|---|
| `ToolCall` | `call_id, tool, args, ts, parent_msg_id` | `agentsla/core/events.py` |
| `ToolResult` | `call_id, output, error?, ts` | `agentsla/core/events.py` |
| `ModelMessage` | `msg_id, role, content, ts` | `agentsla/core/events.py` |
| `Verdict` | `verified, verifier, detail, coverage, corrected_answer?` | `agentsla/core/events.py` |
| `FinalAnswer` | `answer, ts` | `agentsla/core/events.py` |

**Append-only invariant:** events never mutate after write. Replay reads
events in seq order, re-derives each `ToolCall.args_hash` from the recorded
args, and returns the stored final answer. Replay is *structural* — it
validates the recorded log; it does not re-drive the adapter.

### 2.3 Replay semantics

```python
def replay(trace_id: UUID, *, mode: Literal["strict", "tolerant"]) -> ReplayReport
```

| Mode | Behavior | Use case |
|---|---|---|
| `strict` | Any `args_hash` drift on a recorded ToolCall = FAIL (`ToolCallDriftError`, exit 1) | regression test that the recorded log is replay-safe |
| `tolerant` | Drift recorded in `ReplayReport.drift_details`; run does not fail | triage a drifted log — the diff list is the artifact |

**Invariant:** replay never re-executes tools or the model. It re-validates
recorded `args_hash` values and returns the trace's stored final answer
byte-for-byte. Adapter-driven re-execution with stubbed tool results is not
shipped (see `agentsla/core/replay.py` module docstring).

---

## 3. Module API surface

### 3.1 `agentsla/core/`
- `events.py` — pydantic v2 frozen models, `extra="forbid"`. Schema version stamped on every event.
- `trace.py` — `TraceWriter` (DuckDB append + Parquet export), `TraceReader` (SQL).
- `replay.py` — `replay()`, `ReplayReport`.
- `budget.py` — `BudgetManager` (token/cost/latency), degradation hooks.
- `types.py` — shared primitives (UTC-aware datetimes).
- `schema_version.py` — schema version table + `upgrade(v_from, v_to)` scaffold.

### 3.2 `agentsla/policy/`
- `schema.py` — `Policy` pydantic model. `allowed_tools: list[str]`, `tool_rules: list[ToolRule]`, `egress_rules: list[EgressRule]`, `max_calls_per_trace: int`, `mode: Literal["enforce","shadow"]`.
- `gate.py` — `PolicyGate(Policy)`. Returns `GateDecision(allow: bool, reason: str | None, decision: Literal["allow","deny","rewrite"])`. Maintains `gate.audit: list[dict]` for downstream classifier.
- `egress.py` — `EgressRule(name, regex, severity)`, `default_egress_rules()` returns the SSN/PAN/AWS-key/JWT pack.
- `loader.py` — `load_policy(yaml_path)` parses YAML → `Policy`.

### 3.3 `agentsla/verify/`
- `base.py` — `Verifier` ABC, `VerificationResult(passed, coverage, incorrect, details)`.
- `numeric.py` — `NumericVerifier(source_resolver)`. Extracts numeric claims, recomputes from source tools, tolerance check (per-verifier config).
- `claims.py` — claim extraction, including range grammar ("$4.2–4.5M").
- `chain.py` — `VerificationChain(verifiers=[...])`. Runs each verifier; combines.
- `gate.py` — `VerificationGate(chain, writer, verifier="composite")`. The bridge: chain result → `Verdict` event appended to trace store.

### 3.4 `agentsla/classify/`
- `taxonomy.py` — `FailureCategory` enum, 14 values from FISION paper.
- `heuristics.py` — `HEURISTIC_TRIGGERS: list[HeuristicTrigger]`. Maps patterns → categories.
- `classifier.py` — `Classifier(sink, on_classify, heuristic_context, judge=None)`. Two-stage: heuristics first, judge for residuals (≤20% sample by default).
- `judge.py` — `Judge` ABC, `StubJudge` (deterministic, used in hermetic), `ClaudeJudge` (live, Haiku by default).
- `metrics.py` — `build_metrics(registry=None) -> MetricsBundle(failures_total, verify_coverage, classify_latency_seconds, registry)`, `on_classify_callback(metrics)`.

### 3.5 `agentsla/adapters/`
- `base.py` — `AgentAdapter` ABC, `RuntimeHooks` ABC.
- `rawloop.py` — `RawLoopAdapter(tools, trace_writer, echo_model, task_text)`. Reference implementation. **Used by hermetic bench.**
- `langgraph.py` — `LangGraphAdapter`. Wraps a LangGraph graph; routes via hooks.
- `claude_sdk.py` — `ClaudeSdkAdapter`. Wraps Claude Agent SDK with **injected client** (no runtime network dep for tests).
- `noop_hooks.py` — `NoOpHooks`. Used for `naked` mode in bench.

### 3.6 `agentsla/bench/`
- `harness.py` — `agentsla bench` CLI. `{naked, wrapped} × tasks × seeds → results.parquet`. `--metrics-port` for live Prometheus.
- `tasks.py` — 30 tasks: `financial_ops/`, `incident_triage/`, `doc_qa/`. Substring `expected_substring`, optional `injection`, optional `ground_truth`, optional `holdout`.
- `parity.py` — `agentsla bench-parity`. Cross-adapter event-sequence parity.
- `seeded_errors.py` — `agentsla bench-seeded-errors`. Mutation strategies (±10%, ±20%) × trials → sensitivity/specificity.
- `real_llm.py` — `agentsla bench-real`. Live API. Both naked + wrapped modes. Gated `naked`: no gate. Wrapped: route response through `PolicyGate` via synthetic `ToolCall("response_text", text)`.
- `eval_classifier.py` — `agentsla eval-classifier`. Held-out traces vs hand-labeled gold.
- `figures.py` — `agentsla bench-figures`. Matplotlib PNGs for README.
- `report.py` — `agentsla report`. Reads parquet(s) → markdown.
- `upgrader.py` — schema-version upgrade tool.

### 3.7 `agentsla/cli/`
- `run.py` — `agentsla run` (single trace).
- `replay.py` — `agentsla replay <trace_id> [--strict|--tolerant]`.
- `__main__.py` — subcommand dispatcher.

---

## 4. Latency budgets

| Operation | Budget | Where measured |
|---|---|---|
| `PolicyGate.on_tool_call` | <1ms p99 | gate.py, regex compile-once + small arg scan |
| `NumericVerifier.run` (hermetic) | <5ms p99 for 30 tasks | verify/numeric.py |
| `Classifier.classify` (heuristic-only path) | <5ms p99 | classify/classifier.py |
| `Classifier.classify` (judge path) | <500ms p99 | external API |
| `agentsla bench --seeds 5` (hermetic) | <60s wall | integration test |
| `agentsla bench-real --tasks-per-domain 5` | <5min wall, 30 rows | depends on API latency |

---

## 5. Threat model

| Threat | Surface | Mitigation |
|---|---|---|
| Egress of secrets/PII in tool args | outbound tool call | `PolicyGate.on_tool_call` with regex pack |
| Tool-output prompt injection | tool result re-entering LLM context | `Classifier.heuristic_context["tool_output_injection"]` + judge fallback |
| Bypass via free-text response | model final answer | `bench/real_llm.py` wraps response in synthetic ToolCall → same gate enforces |
| Policy bypass via arg mutation | same tool name, different args | `tool_rules[].json_schema` validates per-tool args |
| Replay tamper | trace file edits | event log is append-only + UUID-keyed; replay reads from immutable DuckDB |
| Model version drift on replay | different model, different output | not applicable to structural replay (no model is invoked); live-bench rows carry `model_id` so provenance is auditable |
| Prometheus LAN exposure | `--metrics-port` exposed | `--metrics-addr` defaults to `127.0.0.1`; opt-in required for `0.0.0.0` |

---

## 6. Bench parquet schema (post-v0.1)

### `results.parquet` (hermetic)
| column | dtype | meaning |
|---|---|---|
| mode | string | "naked" \| "wrapped" |
| task_id | string | e.g. "finops-001" |
| domain | string | "financial_ops" \| "incident_triage" \| "doc_qa" |
| seed | int | seed for this run |
| holdout | bool | task is holdout (excluded from dev tuning) |
| has_injection | bool | task carries an injection payload |
| success | bool | final answer contains `expected_substring` |
| verified | bool | verification gate passed |
| verified_at_truth | bool? | final answer contains `ground_truth` (None when no truth declared) |
| injection_resisted | bool | injection payload did not leak into final answer |
| latency_ms | float | end-to-end adapter latency |
| text | string | final answer (debug) |

### `real_llm.parquet` (live)
| column | dtype | meaning |
|---|---|---|
| mode | string | "naked" \| "wrapped" |
| task_id | string | |
| domain | string | |
| model_id | string | e.g. "MiniMax-M3" |
| seed | int | |
| success | bool | |
| gate_passed | bool | real PolicyGate decision (egress regex on free text) |
| verified_at_truth | bool? | substring match against `ground_truth` |
| sensitivity | float? | (future) |
| specificity | float? | (future) |
| latency_ms | float | |
| text | string | raw Claude response |
| note | string | "[NOT YET MEASURED] ..." on API error |

### `seeded_errors.parquet`
| column | dtype | meaning |
|---|---|---|
| strategy | string | "perturb_10" \| "perturb_20" \| ... |
| n_errors_injected | int | |
| n_caught | int | |
| n_missed | int | |
| n_false_corrections | int | |
| sensitivity | float | |
| specificity | float | |
| latency_overhead_pct | float | |

### `parity.parquet`
| column | dtype | meaning |
|---|---|---|
| adapter | string | "rawloop" \| "langgraph" \| "claude_sdk" |
| task_id | string | |
| seed | int | |
| success | bool | |
| n_events | int | total events emitted |
| n_allow | int | gate.audit "allow" decisions |
| n_deny | int | gate.audit "deny" decisions |
| latency_ms | float | |

---

## 7. CLI surface

```text
agentsla run --adapter {rawloop|langgraph|claude_sdk} --task <task_id>
agentsla replay <trace_id> [--strict|--tolerant]
agentsla bench --seeds N [--metrics-port P]
agentsla bench-parity --seeds N
agentsla bench-seeded-errors --strategies 10,20 --trials 100
agentsla bench-real --model <model_id> --tasks-per-domain N
agentsla eval-classifier --gold <path>
agentsla bench-figures --in <parquet>
agentsla report --in <parquet> [--out <md>]
```

All CLIs exit `0` on success, `2` on missing required input (key, file), never `1`.

---

## 8. Test gates per module

| Module | Test file | Coverage target |
|---|---|---|
| `core/` | `tests/unit/core/*` | ≥90% |
| `policy/` | `tests/unit/policy/*` | ≥90% |
| `verify/` | `tests/unit/verify/*` | ≥90% |
| `classify/` | `tests/unit/classify/*` | ≥85% |
| `adapters/` | `tests/unit/adapters/*` | ≥85% |
| `bench/` | `tests/unit/bench/*` | ≥85% |
| integration | `tests/integration/*` | smoke per CLI |
| property | `tests/property/*` | stateful replay invariant |

---

## 9. CI gates

`ruff check .` | `mypy agentsla/core agentsla/policy agentsla/verify` | `pytest --cov` |
`grep -q "PolicyGate" agentsla/bench/harness.py` | `grep -q "Classifier" agentsla/bench/harness.py` |
`grep -q "JsonlLabelSink" agentsla/bench/harness.py` | `agentsla bench --seeds 1` (smoke)