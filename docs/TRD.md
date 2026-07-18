# AgentSLA — Technical Requirements Document (v0.1 hardening)

**For:** implementing engineers (human + AI)
**Date:** 2026-07-09
**Reads-after:** `docs/PRD.md`

---

## 0. Architecture (canonical)

```
            ┌─────────────────────────────────────────────┐
            │                AgentSLA Runtime              │
 request →  │ ┌─────────┐ ┌──────────┐ ┌───────────────┐  │
            │ │ Policy  │→│ Executor │→│ Verification  │  │ → response
            │ │ Gate    │ │ (adapter │ │ Gate          │  │
            │ │         │ │  loop)   │ │               │  │
            │ └─────────┘ └────┬─────┘ └──────┬────────┘  │
            │      ↑      ┌────▼─────┐ ┌──────▼────────┐  │
            │ ┌────┴────┐ │ Trace    │ │ Failure       │  │
            │ │ Budget  │ │ Store    │ │ Classifier    │  │
            │ │ Manager │ │ (DuckDB  │ │ (heuristic    │  │
            │ │         │ │  +       │ │  → LLM judge) │  │
            │ └─────────┘ │ Parquet) │ └───────────────┘  │
            │             └──────────┘                    │
            │       ┌─────────────────┐                   │
            │       │ Prometheus /    │                   │
            │       │ metrics bundle  │                   │
            │       └─────────────────┘                   │
            └─────────────────────────────────────────────┘
```

Five components. Each is independent. Each can be replaced or tested in isolation.

---

## 1. Component contracts

### 1.1 Trace Store (`agentsla/core/trace.py`)

| Public surface | Invariant |
|---|---|
| `TraceWriter(db_path, rotate_after_bytes=50MiB).append(event)` | Event is pydantic-validated through the discriminated union before persistence. `seq` is auto-assigned via `next_seq`. |
| `TraceReader(db_path)` | `read_only=True` connection. **MUST NOT** mutate. Replay cannot corrupt live log. |
| `TraceWriter.export_parquet(out, mode="write")` | Atomic Parquet round-trip. JSON column preserves event payload losslessly. |
| Rotation | When file size > `rotate_after_bytes`: close → rename to `<path>.rotated-<unix-ts>` → reopen. |

**Pydantic event schema** (`agentsla/core/events.py`):

```python
class Event = Annotated[
    ToolCall | ToolResult | ModelMessage | Verdict,
    Field(discriminator="kind"),
]
```

`ToolCall.args_hash` is **always writer-computed** (never caller-supplied). Storage never holds a stale hash. The strict replay engine recomputes and compares.

**Invariant:** every event has `trace_id` + `seq`; the `(trace_id, seq)` pair is the primary key. Strict ordering preserved through DuckDB.

---

### 1.2 Replay Engine (`agentsla/core/replay.py`)

| Public surface | Invariant |
|---|---|
| `replay(trace_id, db_path, *, mode)` | Returns `ReplayReport`. `exit_code=0` on pass; `exit_code=1` on strict-mode drift or unknown trace. |
| `ReplayEngine.replay(trace_id, *, mode)` | Class form; one per replay run. |
| `mode="strict"` | Every recorded `ToolCall` must hash-equal its canonicalized args. Drift raises `ToolCallDriftError`. |
| `mode="tolerant"` | Records drift in the report but does not raise. |

**Hash semantics:** `args_hash = sha256(canonical_json(args))` where canonical JSON is `sort_keys=True, separators=(",", ":"), ensure_ascii=False`. Two semantically-identical tool calls always hash equal.

**Important honesty constraint:** the shipped replay engine is structural, not adapter-driven. It validates recorded tool-call hashes, counts drift, and returns the trace's recorded `final_answer` byte-for-byte. It does not re-run the adapter loop with stubbed tool outputs.

---

### 1.3 Policy (`agentsla/policy/{schema,gate,egress,loader}.py`)

```python
Policy(
    allowed_tools: list[str],            # empty = deny all
    tool_rules: list[ToolRule],          # per-tool json_schema, max_calls
    egress_rules: list[EgressRule],      # regex detectors
    max_calls_per_trace: int = 20,
    mode: Literal["enforce", "shadow"] = "enforce",
)
```

**Gate evaluation order (first FAIL wins):**

1. `allowed_tools` membership (empty list = deny all).
2. Per-tool `json_schema` (via `jsonschema.validate` if installed; pass-through otherwise).
3. Per-tool `max_calls` (incremented before check).
4. Global `max_calls_per_trace`.
5. Egress regex scan against every string leaf in `args`. PAN hit additionally requires Luhn validity.

**Egress pack (default, ordered):**

- `pan` — `\b(?:\d[ -]?){13,19}\b` + Luhn
- `ssn` — `\b\d{3}-\d{2}-\d{4}\b`
- `aws_access_key` — `\bAKIA[0-9A-Z]{16}\b`
- `jwt` — `\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b`

**Invariant:** policies are **frozen** at load time (`extra="forbid", frozen=True` on Pydantic). Runtime mutation raises. Tenant extension is via `egress_rules` list only — operators cannot redefine the pack mid-run.

---

### 1.4 Budget Manager (`agentsla/core/budget.py`)

```python
BudgetSpec(
    max_tokens: int = 50_000,
    max_cost_usd: float = 1.00,
    max_calls: int = 50,
    max_wall_time: timedelta = timedelta(seconds=120),
)
```

**Degradation levels (predefined thresholds):**

| Level | Trigger | Behavior |
|---|---|---|
| `FULL` | <50% of any ceiling | Normal operation |
| `REDUCED` | ≥50% | Skip refinement loops |
| `MINIMAL` | ≥75% | Drop optional sub-tasks |
| `EMERGENCY` | ≥90% | Return best-effort answer |

`BudgetExceededError` carries `(metric, observed, ceiling, level)` for caller-side mapping. The gate does **not** crash the loop — caller decides whether to fall through to the next level.

---

### 1.5 Verification Gate (`agentsla/verify/*.py`)

```python
# Shipped today: NumericVerifier only. The chain accepts any Verifier
# implementation; GroundingVerifier / SchemaVerifier are design targets,
# not shipped modules (see README § Limitations).
VerificationChain(verifiers=[NumericVerifier(...)])
chain.run(trace, final_answer) -> ChainResult
```

**ChainResult:**

```python
@dataclass
class ChainResult:
    verifiers: list[str]
    claims: list[ClaimVerdict]   # unified dataclass + pydantic event shape via gate
    coverage_threshold: float = 1.0

    @property total(self) -> int: ...
    @property verified(self) -> int: ...   # claims where status == "verified"
    @property incorrect(self) -> int: ...  # claims where status == "incorrect"
    @property coverage(self) -> float: ...  # verified / total
    @property passed(self) -> bool: ...    # incorrect == 0 AND coverage >= threshold
```

**Numeric claim extraction** (`agentsla/verify/claims.py`):

- Patterns: `percent`, `currency` ($/€/£/¥), `float`, `int`, `range`, `arithmetic_expression`.
- Span-set dedup prevents double-matching (e.g., "$1,200" should not also match as "1200").
- Range parser hardened against the `4--5` semantic-escape case (whitespace-bounded second sign).
- Per-claim `kind` ∈ `{int, float, currency, percent, range, expression}`.

**Verifier semantics:**

- `identity_source` (default): claim value IS the source value (self-certifying; calibration only).
- Operator-supplied `source_resolver(claim, trace) -> value | None`: maps claim to recomputable source.
- `tolerance`: relative float comparison, default `1e-6`. Per-verifier instances.

---

### 1.6 Classifier (`agentsla/classify/*.py`)

```python
Classifier(
    judge: Judge | None = None,            # defaults to StubJudge
    sink: LabelSink | None,                # JsonlLabelSink (prod) or InMemoryLabelSink (tests)
    on_classify: Callable | None,          # metrics callback
    heuristic_context: dict | None,        # per-run context (allowed_tools, deny_counts, ...)
)
classifier.classify(trace, *, verification_incorrect=0) -> ClassificationResult
```

**14-category taxonomy** (severity-ordered):

| Severity | Category | Trigger |
|---|---|---|
| 9 | `hallucinated_fact` | Verifier says `incorrect > 0`, no stronger category |
| 9 | `policy_violation` | Egress hit present |
| 8 | `reasoning_error` | Contradictory numeric claims in final answer |
| 7 | `tool_response_misuse` | Error result + subsequent call doesn't adapt |
| 6 | `tool_call_error` | Tool name ∉ `allowed_tools` |
| 6 | `context_overflow` | Event payload bytes > threshold |
| 5 | `planning_error` | No Verdict event + tool error |
| 5 | `budget_exceeded` | BudgetManager.exhausted |
| 5 | `retry_loop` | ≥3 consecutive identical `(tool, args_hash)` |
| 5 | `permission_denied` | ≥2 DENYs for same tool |
| 4 | `format_violation` | Final answer fails declared JSON schema |
| 4 | `partial_completion` | Required fields missing |
| 3 | `timeout` | Trace duration > deadline |
| 3 | `unexpected_tool_failure` | Unclassified tool error |

**Selection rule:** highest severity wins; ties broken by `CATEGORY_ORDER` (lower = higher precedence).

**LLM judge gate:** invoked when `candidates == []` OR `hallucinated_fact ∈ candidates AND verification_incorrect > 0 AND heuristic_confidence < 0.7`. Target ≤20% invocation rate.

---

### 1.7 Adapters (`agentsla/adapters/*.py`)

```python
class AgentAdapter(ABC):
    name: str
    model_id: str
    tools: dict[str, Callable]

    def register_tool(name, fn) -> None: ...
    @abstractmethod
    def run(task_id, *, hooks, trace_writer=None) -> FinalAnswer: ...

class RuntimeHooks(Protocol):
    def on_tool_call(call) -> HookDecision: ...
    def on_tool_result(call, result) -> None: ...
    def on_final_answer(trace, verdict) -> None: ...
```

Three adapters shipped: `RawLoopAdapter` (reference, hermetic), `LangGraphAdapter` (stub, parity-tested), `NoOpHooks` (default).

**Invariant:** adapters MUST emit `ToolCall` before invocation, `ToolResult` after, `on_final_answer` exactly once on success. The trace event sequence is identical across adapters (modulo UUID identity) so cross-adapter parity tests compare event streams.

---

### 1.8 Bench Harness (`agentsla/bench/{harness,tasks,report,seeded_errors}.py`)

**Per-row schema (parquet):**

```
mode                "naked" | "wrapped"
task_id             str
domain              "financial_ops" | "incident_triage" | "doc_qa"
seed                int
holdout             bool
has_injection       bool
success             bool    (final answer contains expected_substring)
verified            bool    (verification gate passed; always False for naked)
injection_resisted  bool    (injection payload did not leak)
latency_ms          float
text                str     (final answer, for debugging)
```

**30 base tasks** (10 per domain, deterministic holdouts at indices `i % 4 == 3` ⇒ 8 holdouts, ≥25%). **5 injection variants** of the first 5 base tasks.

**Run matrix:** `{naked, wrapped} × 35 tasks × 5 seeds = 350 rows`. With `--seeds 1` (CI): 70 rows.

---

## 2. P0 fixes — schema unification

### 2.1 Problem

`agentsla/core/events.py` defines `ClaimVerdict` (pydantic, event-shape):
```python
class ClaimVerdict(_StrictModel):
    claim_text: str
    passed: bool
    expected: str | None
    actual: str | None
    source_tool_id: UUID | None
    detail: str
```

`agentsla/verify/base.py` defines `ClaimVerdict` (dataclass, internal):
```python
@dataclass
class ClaimVerdict:
    claim: str
    status: str  # "verified" | "incorrect" | "unverified"
    observed: Any
    expected: Any
    confidence: float
```

`agentsla/verify/gate.py` does the manual mapping — but is never called by the bench. So:
- This was the pre-v0.2 failure mode. It is closed in the shipped code: wrapped bench runs now persist `Verdict` events.
- Replay still does not re-run the verifier or adapter loop; it validates the stored trace structurally.

### 2.2 Solution

**Step 1.** Make `verify/base.py:ClaimVerdict` and `events.py:ClaimVerdict` coexist without name collision. Two options:

- **Option A (chosen):** Rename the dataclass to `InternalClaimVerdict`; keep `events.py:ClaimVerdict` as the canonical event shape. The `VerificationGate` maps internal→event when emitting to the trace store.
- **Option B:** Collapse to one pydantic model. Higher churn — every verifier signature changes.

**Step 2.** Wire `VerificationGate.run` into `bench/harness.py:WrappedHooks.on_final_answer`. The gate emits a `Verdict` event to the `TraceWriter`. Shipped.

**Step 3.** Update `VerificationGate.run(trace: Trace, final_answer: str)` signature to match `VerificationChain.run(trace, final_answer)`. The gate's job is to take a `Trace`, run the chain, build the event, append to writer. Shipped.

### 2.3 Test-first contract

```python
# tests/integration/test_verdict_persistence.py

def test_bench_writes_verdict_event_to_trace_store(tmp_path):
    """Every wrapped bench run writes a Verdict event with coverage + per_claim."""
    db = tmp_path / "traces.duckdb"
    writer = TraceWriter(db)
    try:
        hooks = WrappedHooks(writer)
        adapter = RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, trace_writer=writer)
        adapter.run("test", hooks=hooks)
    finally:
        writer.close()

    reader = TraceReader(db)
    traces = reader.list_traces()
    assert len(traces) == 1
    events = list(reader.iter_events(traces[0].trace_id))
    verdict_events = [e for e in events if isinstance(e, Verdict)]
    assert len(verdict_events) == 1
    assert verdict_events[0].coverage >= 0.0
    assert verdict_events[0].verified in (True, False)
```

### 2.4 Acceptance

- One `ClaimVerdict` class name in the codebase (or one per layer with explicit naming).
- `bench/harness.py` writes a `Verdict` event for every wrapped run.
- 332 existing tests pass + new tests for verdict persistence.
- No regression on the seeded-error experiment (sensitivity ≥85% @ ±50%, specificity ≥90% @ 0%).

---

## 3. P1 fixes — CI integration gate + honest metrics

### 3.1 CI integration gate

`.github/workflows/test.yml` MUST include an integration-gate step that fails the build if the bench wiring is removed:

```yaml
- name: bench wiring integration gate
  run: |
    grep -q "from agentsla.policy import Policy, PolicyGate" agentsla/bench/harness.py || (echo "FAIL: PolicyGate not imported"; exit 1)
    grep -q "from agentsla.classify import" agentsla/bench/harness.py || (echo "FAIL: Classifier not imported"; exit 1)
    grep -q "JsonlLabelSink" agentsla/bench/harness.py || (echo "FAIL: JsonlLabelSink not imported"; exit 1)
    grep -q "build_metrics" agentsla/bench/harness.py || (echo "FAIL: build_metrics not called"; exit 1)
```

### 3.2 Honest `verified_pct` metric

Reframe the bench output. Add a new column `verified_at_truth` that compares extracted claim against a known ground truth (where one is available). Keep `verified` as "gate ran without exception" but **rename it to `gate_passed`** in the headline table to remove ambiguity.

Update README, WRITEUP, REPORT.md headline.

### 3.3 Architecture diagram in WRITEUP

Insert a mermaid diagram after the "What problem are we solving" section. Reference `docs/PRD.md § 0` and `docs/TRD.md § 0` for the canonical.

---

## 4. Implementation order (red/green TDD)

Each unit: write failing test → run → confirm red → implement minimum → confirm green → refactor → commit atomically.

1. **Unit 1: InternalClaimVerdict rename** — rename `verify/base.py:ClaimVerdict` to `InternalClaimVerdict`. Update all imports. (4 files touched.)
2. **Unit 2: VerificationGate signature fix** — change `run(trace_id, final_answer)` → `run(trace, final_answer)`. (3 files touched.)
3. **Unit 3: Bench writes Verdict event** — add `_emit_verdict()` helper, wire into `WrappedHooks.on_final_answer`. (2 files touched.)
4. **Unit 4: `gate_passed` metric rename** — update report.py, README, WRITEUP. (4 files touched.)
5. **Unit 5: CI integration gate** — create `.github/workflows/test.yml`. (1 file created.)
6. **Unit 6: Comparative analysis doc** — `docs/comparative-analysis.md`. (1 file created.)
7. **Unit 7: WRITEUP architecture diagram** — mermaid block. (1 file edited.)
8. **Unit 8: Reframe reasoning_error trigger** — anchor-aware contradiction check. (2 files touched.)
9. **Unit 9: Reframe tool_response_misuse trigger** — distinguish adapt vs reuse. (2 files touched.)
10. **Unit 10: mypy unused-section cleanup** — remove unused overrides. (1 file edited.)

Total: ~20 file edits, 9 atomic commits.

---

## 5. Invariants preserved across all changes

| Invariant | How preserved |
|---|---|
| Append-only event log | `TraceWriter.append` is the only mutation path. No UPDATE/DELETE in any code path. |
| Structural replay | `args_hash` is writer-computed from canonical JSON; replay re-validates hashes and returns the stored answer (drift detection, not re-execution). |
| Frozen policies | `extra="forbid", frozen=True` on Policy. Operators cannot mutate at runtime. |
| Replay-safe bench | Hermetic EchoModel + JsonEchoTool. No network, no clock, no PRNG. |
| Coverage as first-class metric | Every Verdict event has `coverage ∈ [0, 1]`. Operators set per-domain threshold. |
| Honest gaps labeled | All `[NOT YET MEASURED]` slots stay marked. No fabricated numbers. |

---

## 6. Anti-patterns explicitly forbidden

- ❌ Wrapping someone else's framework instead of building the control plane.
- ❌ LLM judge on every trace (cost unbounded; violates ≤20% target).
- ❌ Mutable trace state (breaks replay).
- ❌ Pass-through `_ALLOW` shim (already removed; integration gate prevents re-introduction).
- ❌ Fabricating benchmark numbers to make the headline look better.
- ❌ Silent failures (any error in the gate must surface as a `Verdict.verified=False`, never swallowed).
