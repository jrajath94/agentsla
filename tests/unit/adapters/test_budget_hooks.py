"""BudgetedHooks — budget enforcement wired into the runtime hook contract.

Pins the feedback-required behavior: budget pressure converts to a
policy-style DENY at the hook boundary, so the adapter degrades to its
short-circuit answer instead of crashing (graceful degradation), and the
degradation surface (breaches, denied_calls, level) is observable.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from agentsla.adapters.base import HookDecision
from agentsla.adapters.budget_hooks import BudgetedHooks
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.core.budget import BudgetManager, BudgetSpec, DegradationLevel
from agentsla.core.events import (
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
    canonical_args_hash,
    now_timestamp,
)
from agentsla.tools.deterministic import JsonEchoTool


def _make_call(trace_id: uuid.UUID, seq: int = 0) -> ToolCall:
    args = {"text": "t"}
    return ToolCall(
        call_id=uuid.uuid4(),
        tool="json_echo",
        args=args,
        trace_id=trace_id,
        seq=seq,
        ts=now_timestamp(),
        parent_msg_id=None,
        args_hash=canonical_args_hash(args),
    )


def _make_result(call: ToolCall) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool=call.tool,
        result="ok",
        is_error=False,
        error=None,
        latency_ms=0.0,
        trace_id=call.trace_id,
        seq=call.seq + 1,
        ts=now_timestamp(),
    )


def _spec(**overrides: object) -> BudgetSpec:
    defaults: dict[str, object] = {
        "max_tokens": 50_000,
        "max_cost_usd": 1.00,
        "max_calls": 50,
        "max_wall_time": timedelta(seconds=120),
    }
    defaults.update(overrides)
    return BudgetSpec(**defaults)  # type: ignore[arg-type]


class TestGracefulDegradationEndToEnd:
    def test_exhausted_call_budget_degrades_adapter_not_crashes(self) -> None:
        """The load-bearing integration: budget breach -> DENY -> short-circuit answer."""
        hooks = BudgetedHooks(BudgetManager(_spec(max_calls=0)))
        adapter = RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, task_text="degrade-me")

        out = adapter.run("task-budget", hooks=hooks)

        # No exception escaped; adapter returned its degraded (empty) answer.
        assert out.text == ""
        # Tool never executed: trace holds user msg + the denied ToolCall only.
        assert [ev.kind for ev in out.trace.events] == ["model_message", "tool_call"]
        assert hooks.denied_calls == 1
        assert len(hooks.breaches) == 1
        assert hooks.breaches[0].metric == "calls"

    def test_within_budget_run_is_untouched(self) -> None:
        hooks = BudgetedHooks(BudgetManager(_spec(max_calls=5)))
        adapter = RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, task_text="fine")

        out = adapter.run("task-ok", hooks=hooks)

        assert out.text.startswith("<echo:fine>::")
        assert hooks.denied_calls == 0
        assert hooks.breaches == []


class TestDecisionOrder:
    def test_inner_policy_deny_wins_before_budget(self) -> None:
        class DenyAll:
            def on_tool_call(self, call: ToolCall) -> HookDecision:
                return HookDecision(allow=False, reason="policy: deny")

            def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
                return None

            def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
                return None

        budget = BudgetManager(_spec(max_calls=50))
        hooks = BudgetedHooks(budget, inner=DenyAll())
        decision = hooks.on_tool_call(_make_call(uuid.uuid4()))

        assert decision.allow is False
        assert decision.reason == "policy: deny"
        # Budget was NOT charged for a policy-denied call.
        assert hooks.breaches == []

    def test_inner_allow_then_budget_charges(self) -> None:
        budget = BudgetManager(_spec(max_calls=1))
        hooks = BudgetedHooks(budget)
        trace_id = uuid.uuid4()

        first = hooks.on_tool_call(_make_call(trace_id, seq=0))
        second = hooks.on_tool_call(_make_call(trace_id, seq=2))

        assert first.allow is True
        assert second.allow is False
        assert "budget breach: calls" in second.reason
        assert second.extra["degradation_level"] == DegradationLevel.EMERGENCY.value


class TestPostResultBreachDegradesNextCall:
    def test_cost_breach_on_result_denies_subsequent_call(self) -> None:
        def pricey(_call: ToolCall, _result: ToolResult) -> tuple[int, float]:
            return (0, 2.00)  # exceeds max_cost_usd=1.00 in one shot

        budget = BudgetManager(_spec(max_cost_usd=1.00))
        hooks = BudgetedHooks(budget, cost_model=pricey)
        trace_id = uuid.uuid4()

        call = _make_call(trace_id, seq=0)
        assert hooks.on_tool_call(call).allow is True
        hooks.on_tool_result(call, _make_result(call))  # breach captured, not raised

        assert len(hooks.breaches) == 1
        assert hooks.breaches[0].metric == "cost_usd"

        nxt = hooks.on_tool_call(_make_call(trace_id, seq=2))
        assert nxt.allow is False
        assert "budget breached earlier" in nxt.reason
        assert nxt.extra["degradation_level"] == DegradationLevel.EMERGENCY.value


class TestObservability:
    def test_level_passthrough_tracks_spend(self) -> None:
        def half_dollar(_call: ToolCall, _result: ToolResult) -> tuple[int, float]:
            return (0, 0.60)

        budget = BudgetManager(_spec(max_cost_usd=1.00))
        hooks = BudgetedHooks(budget, cost_model=half_dollar)
        trace_id = uuid.uuid4()

        assert hooks.level(str(trace_id)) is DegradationLevel.FULL
        call = _make_call(trace_id)
        hooks.on_tool_call(call)
        hooks.on_tool_result(call, _make_result(call))
        assert hooks.level(str(trace_id)) is DegradationLevel.REDUCED

    def test_final_answer_delegates_to_inner(self) -> None:
        seen: list[str] = []

        class Recorder:
            def on_tool_call(self, call: ToolCall) -> HookDecision:
                return HookDecision(allow=True)

            def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
                return None

            def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
                seen.append(trace.task_id)

        hooks = BudgetedHooks(BudgetManager(_spec()), inner=Recorder())
        adapter = RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, task_text="x")
        adapter.run("task-delegate", hooks=hooks)
        assert seen == ["task-delegate"]
