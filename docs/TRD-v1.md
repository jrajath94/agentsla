# AgentSLA — Technical Requirements Document (v1 — FINAL)

**For:** implementing engineer (red/green TDD, no shortcuts)
**Reads-after:** `docs/PRD-v1.md`
**Supersedes:** `docs/TRD.md` (v0.1 hardening — closed)

---

## 0. Architecture (canonical, v1)

```
                          ┌─────────────────────────────────────────────┐
                          │              AgentSLA Runtime                │
   request  ─────────────▶│                                              │
                          │ ┌─────────┐ ┌──────────────┐ ┌─────────────┐ │
                          │ │ Policy  │▶│   Executor   │▶│ Verification│ │──▶ response
                          │ │ Gate    │ │  (Claude SDK │ │ Gate        │ │
                          │ │         │ │   LangGraph  │ │             │ │
                          │ │         │ │   raw loop)  │ │             │ │
                          │ └─────────┘ └──────┬───────┘ └──────┬──────┘ │
                          │      ▲      ┌───────▼──────┐  ┌──────▼──────┐ │
                          │ ┌────┴────┐ │ Trace Store  │  │  Failure    │ │
                          │ │ Budget  │ │ (DuckDB +    │  │ Classifier  │ │
                          │ │ Manager │ │  Parquet +   │  │ (heuristic  │ │
                          │ │         │ │  schema v1)  │  │  → judge)   │ │
                          │ └─────────┘ └──────────────┘  └─────────────┘ │
                          │                                              │
                          │ ┌──────────────────┐  ┌─────────────────────┐ │
                          │ │ Prometheus +     │  │ Real-LLM bench      │ │
                          │ │ Grafana JSON     │  │ (Claude Haiku 4.5)  │ │
                          │ └──────────────────┘  └─────────────────────┘ │
                          └─────────────────────────────────────────────┘
```

Seven components. Three adapters (Claude SDK / LangGraph / raw loop). Append-only event log is the single source of truth.

---

## 1. Component contracts (v1)

### 1.1 Adapters (`agentsla/adapters/*.py`)

#### Base contract (unchanged from v0.1)

```python
class AgentAdapter(ABC):
    name: str
    model_id: str
    tools: dict[str, Callable]
    def register_tool(self, name: str, fn: Callable) -> None: ...
    @abstractmethod
    def run(self, task_id: str, *, hooks: RuntimeHooks, trace_writer: TraceWriter | None = None) -> FinalAnswer: ...

class RuntimeHooks(Protocol):
    def on_tool_call(self, call: ToolCall) -> HookDecision: ...
    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None: ...
    def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None: ...
```

#### Claude SDK adapter (v1 NEW)

```python
class ClaudeSdkAdapter(AgentAdapter):
    """Wraps the Claude Agent SDK Python client.

    Uses `claude_agent_sdk` (the official Anthropic SDK for agents).
    The SDK exposes `query()` + streaming responses; we intercept each
    `tool_use` block, route through `RuntimeHooks.on_tool_call`, then
    invoke the tool via `agentsla.tools.deterministic.JsonEchoTool`
    or a user-supplied tool registry.

    Parity guarantee: emits ToolCall before invocation, ToolResult
    after, on_final_answer exactly once on success — same event
    sequence as `RawLoopAdapter` for the same task + tools.
    """
```

**Adapter test-first contract:**

```python
# tests/unit/adapters/test_claude_sdk.py
def test_claude_sdk_emits_tool_call_before_invocation(monkeypatch):
    """Wire a fake SDK that returns a single tool_use block; assert
    ToolCall event written before the tool fn runs."""

def test_claude_sdk_parity_with_rawloop_on_echo_task():
    """Same task text + same tool registry → identical event-kind sequence
    across ClaudeSdkAdapter and RawLoopAdapter (modulo UUID identity)."""

def test_claude_sdk_routes_on_tool_call_through_policy_gate():
    """Mock SDK issues a tool call that violates policy → HookDecision(allow=False)
    → tool fn never invoked → on_final_answer still called with degraded answer."""
```

### 1.2 Numeric verifier — per-verifier tolerance (v1 NEW)

```python
class NumericVerifier:
    def __init__(
        self,
        *,
        source_resolver: Callable[[ClaimVerdict, Trace], Any] | None = None,
        tolerance: float = 1e-6,                      # ← NEW (was 1e-2 global)
        require_units_match: bool = True,             # ← NEW
    ): ...
```

**Test-first contract:**

```python
def test_numeric_verifier_honors_per_instance_tolerance():
    """tolerance=1e-6 rejects a 1e-4 perturbation; tolerance=1e-2 accepts it."""

def test_numeric_verifier_require_units_match_flags_mismatch():
    """$100 claim vs 100 EUR source → verdict.verified=False, detail='unit mismatch'."""

def test_numeric_verifier_default_tolerance_is_strict():
    """Default tolerance is 1e-6, NOT 1e-2 (regression guard for finops accuracy)."""
```

### 1.3 Claim extraction — range with per-endpoint multiplier (v1 FIX)

```python
# verify/claims.py
_RANGE_PATTERN = re.compile(
    r"""
    (?P<a>[\$€£¥]?\s?-?\d[\d,\.]*\s?[KkMmBb%]?)   # first endpoint
    \s*[-–—]\s*                                    # separator
    (?P<b>[\$€£¥]?\s?-?\d[\d,\.]*\s?[KkMmBb%]?)   # second endpoint
    """,
    re.VERBOSE,
)
```

**Test-first contract:**

```python
def test_range_claim_handles_per_endpoint_multiplier():
    """'$4.2M–$4.5M' → 2 endpoints, both parsed with M multiplier."""
    claims = extract_claims("Revenue is $4.2M–$4.5M this quarter.")
    assert len(claims) == 1
    assert claims[0].kind == "range"
    assert claims[0].endpoints == (4_200_000, 4_500_000)

def test_range_claim_handles_currency_mix():
    """'$100-$200' → both USD; '€100-€200' → both EUR."""

def test_range_claim_unchanged_for_simple_range():
    """'4-5' still parses correctly (no semantic-escape regression)."""
```

### 1.4 Real-LLM bench harness (v1 NEW)

```python
# bench/real_llm.py
@dataclass
class RealLlmRow:
    mode: str                    # "naked" | "wrapped"
    task_id: str
    domain: str
    model_id: str                # "claude-haiku-4-5-20251001"
    seed: int
    success: bool
    gate_passed: bool
    verified_at_truth: bool | None
    sensitivity: float | None    # 1.0 if gate caught injected error, else 0.0
    specificity: float | None
    latency_ms: float
    text: str


def run_real_llm_bench(
    *,
    model: str = "claude-haiku-4-5-20251001",
    tasks_per_domain: int = 5,
    seeds: int = 1,
    api_key: str | None = None,
    out_path: Path = Path("bench/results/real_llm.parquet"),
) -> list[RealLlmRow]:
    """Run tasks through real Claude API. Requires ANTHROPIC_API_KEY.
    Captures real traces to .agentsla/real_llm.duckdb; runs verification
    gate on wrapped mode; emits parquet + REPORT.md section.
    """
```

**Test-first contract:**

```python
# tests/unit/bench/test_real_llm.py
def test_real_llm_bench_requires_api_key(monkeypatch):
    """No ANTHROPIC_API_KEY → clear error, exit 2, no parquet written."""

def test_real_llm_bench_schema_matches_parquet(real_llm_response):
    """Mocked Claude response → row schema valid; parquet writes; report section regenerates."""

def test_real_llm_bench_marks_unmeasured_when_no_key():
    """`--out` flag absent → run with mocked key, assert REAL rows; absent
    key → row marked `[NOT YET MEASURED]` in REPORT.md."""
```

### 1.5 Held-out fixture generator (v1 NEW)

```python
# scripts/build_held_out_fixture.py (extends existing)
def build_real_held_out_fixture(
    *,
    n_per_category: int = 10,
    model: str = "claude-haiku-4-5-20251001",
    out_path: Path = Path("tests/fixtures/held_out_labels.jsonl"),
    synthetic_fallback: bool = True,
) -> int:
    """Run real Claude on held-out task variants, label categories from
    the classifier output, write JSONL. If API key absent + synthetic_fallback,
    generate synthetic-but-distinct traces (no heuristic overlap).
    Returns count of rows written.
    """
```

**Test-first contract:**

```python
def test_held_out_fixture_writes_n_rows_per_category():
    """n_per_category=10 → 140 rows total (14 categories × 10)."""

def test_held_out_fixture_falls_back_to_synthetic():
    """No API key + synthetic_fallback=True → writes synthetic rows tagged `synthetic=true`."""

def test_held_out_fixture_fails_without_fallback_and_no_key():
    """No API key + synthetic_fallback=False → raises RuntimeError."""
```

### 1.6 README quickstart truth (v1 NEW)

```python
# tests/integration/test_readme_quickstart.py
PYTHON_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def test_readme_quickstart_imports_cleanly():
    """Extract first ```python fenced block from README.md, exec() it against
    installed package, assert no ImportError or AttributeError.
    Defends against README-vs-code drift.
    """
```

**Real README snippet (v1 corrected):**

```python
from agentsla.policy import Policy, PolicyGate
from agentsla.policy.egress import default_egress_rules
from agentsla.verify import VerificationChain, NumericVerifier, identity_source
from agentsla.classify import Classifier, InMemoryLabelSink
from agentsla.core.trace import TraceWriter, TraceReader

# Build the four guarantees.
policy = Policy(allowed_tools=["json_echo"], egress_rules=default_egress_rules())
gate = PolicyGate(policy)

verifier = NumericVerifier(source_resolver=identity_source, tolerance=1e-6)
chain = VerificationChain(verifiers=[verifier])

sink = InMemoryLabelSink()
classifier = Classifier(sink=sink)

# Wrap an agent run.
writer = TraceWriter(Path("traces.duckdb"))
hooks = AgentSLAHooks(gate=gate, chain=chain, classifier=classifier, writer=writer)
adapter = RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, trace_writer=writer)
final = adapter.run(task_id="demo", hooks=hooks)
print(final.text)
```

---

## 2. Failure modes — quality pass (v1 NEW)

`docs/failure-modes.md` ≥10 modes. Each mode has the 5-field schema:

| Field | Purpose |
|---|---|
| **Trigger** | What observable causes the failure mode |
| **Why-it-breaks** | Architectural reason it cannot be prevented by the runtime |
| **Observable signal** | Metric / log / counter that surfaces it |
| **v1 status** | `mitigated` / `partial` / `open` / `KNOWN LIMIT` |
| **Mitigation** | Concrete mitigation or honest `[KNOWN LIMIT]` |

Modes (target ≥10):

1. DuckDB single-writer lock at p99.9 (mitigated by `--seeds N` cap)
2. TraceWriter 50 MiB Parquet rotation (partial — buffered flush)
3. Replay model-version drift (mitigated — `model_id` REQUIRED)
4. Policy change between record & replay (mitigated — policy frozen)
5. LLM judge overload / quota (partial — async queue v2)
6. Context window explosion in classifier prompt (partial — threshold cutoff)
7. Hermetic EchoModel self-certifies (partial — v1 adds real-LLM bench)
8. Classifier eval circularity (partial — v1 adds real held-out fixture)
9. Regex false positives on EOL/whitespace edge cases (mitigated — `_RANGE_PATTERN` hardening)
10. Per-verifier tolerance mismatch (mitigated — v1 per-instance config)
11. Range claims with per-endpoint multiplier (mitigated — v1 regex fix)
12. Prometheus default registry collision across WrappedHooks (mitigated — module-level singleton)
13. /metrics HTTP endpoint opt-in only (KNOWN LIMIT — operationally fine)
14. Live Claude API bench requires `ANTHROPIC_API_KEY` (KNOWN LIMIT — harness path real, live numbers not in CI)

---

## 3. Implementation order (red/green TDD, atomic commits)

| # | Commit | Tests written first | Files touched |
|---|---|---|---|
| 1 | `docs: PRD v1 + TRD v1` | none (docs only) | 2 new |
| 2 | `fix(docs): README quickstart truth` | `test_readme_quickstart.py` | README.md + new test |
| 3 | `feat(adapter): Claude SDK adapter` | `test_claude_sdk.py` + `test_claude_sdk_parity.py` | new adapter + 2 tests |
| 4 | `feat(verify): per-verifier tolerance config` | `test_numeric_tolerance_config.py` | numeric.py + test |
| 5 | `fix(verify): range claims per-endpoint multiplier` | `test_range_claim_extraction.py` | claims.py + test |
| 6 | `feat(bench): real-LLM bench harness` | `test_real_llm.py` | new module + test |
| 7 | `chore(classify): real held-out fixture generator` | extends existing | script + fixture |
| 8 | `docs: failure-modes.md quality pass` | none | docs/failure-modes.md |
| 9 | `docs: WRITEUP v1` | none | WRITEUP.md |
| 10 | `chore(release): v1 tag + CHANGELOG` | full suite green | CHANGELOG.md + tag |

Each commit lands with: failing test first → minimal implementation → green → refactor → atomic commit.

---

## 4. Invariants preserved

| Invariant | How preserved |
|---|---|
| Append-only event log | `TraceWriter.append` only mutation path. No UPDATE/DELETE in any code path. |
| Deterministic replay | `args_hash` writer-computed from canonical JSON. Schema drift = gate. |
| Frozen policies | `extra="forbid", frozen=True` on Policy. Runtime mutation raises. |
| Replay-safe bench | Hermetic EchoModel + JsonEchoTool default. Real-LLM bench opt-in only. |
| Coverage as first-class metric | Every Verdict event has `coverage ∈ [0, 1]`. |
| Honest gaps labeled | All `[NOT YET MEASURED]` slots stay marked. No fabricated numbers. |
| Three-adapter parity | Event-kind sequence equality enforced by `tests/integration/test_cross_adapter_parity.py` (extended to 3-way). |
| README quickstart truth | Integration test parses README fenced block, exec()s, asserts no import error. |
| Per-verifier tolerance | `NumericVerifier(tolerance=...)` instance param. No global default change. |

---

## 5. Anti-patterns explicitly forbidden

- ❌ Wrapping someone else's framework instead of building the control plane (already avoided — we own the gates).
- ❌ LLM judge on every trace (cost unbounded; ≤20% target).
- ❌ Mutable trace state (breaks replay).
- ❌ Fabricating benchmark numbers to make the headline look better.
- ❌ Silent failures (any error in the gate must surface as a `Verdict.verified=False`, never swallowed).
- ❌ Deferring work to "v2" — v1 is FINAL; everything ships or honest gap.

---

## 6. Verification — how we know we're done

```bash
make test                        # ≥420 tests, all green
ruff check .                     # zero findings
ruff format --check .            # zero diffs
mypy --strict agentsla/core agentsla/policy agentsla/verify \
                  agentsla/classify agentsla/adapters
                                # zero findings
make bench                       # 350 rows parquet, REPORT.md regenerates
make report                      # REPORT.md with parity + held-out + figures + real-llm
make bench-real                  # real-LLM bench (skips if no API key, marks honest)
git tag v1                     # annotated
git push origin main             # public visibility
```

When all green: **tag v1, push, stop.**

---

## 7. Final commit message format

```
<type>(<scope>): <subject>

<body — what + why, ≤5 lines>

<footer — references + co-author>
```

Types: `feat`, `fix`, `perf`, `test`, `docs`, `refactor`, `bench`, `ci`, `chore`.
Subject ≤72 chars. Imperative. No period.
