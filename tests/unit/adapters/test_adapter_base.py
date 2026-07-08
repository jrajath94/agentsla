"""Tests for adapter base + NoOpHooks."""

from __future__ import annotations

import pytest

from agentsla.adapters.base import (
    AgentAdapter,
    FinalAnswer,
    HookDecision,
    RuntimeHooks,
)
from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.core.events import (
    ToolCall,
    Trace,
    now_timestamp,
)
from agentsla.core.types import new_call_id, new_trace_id


def _trace() -> Trace:
    """Tiny synthetic trace for FinalAnswer tests."""
    return Trace(
        trace_id=new_trace_id(),
        task_id="t1",
        model_id="m1",
        events=[],
        final_answer="hello",
        start_ts=now_timestamp(),
        end_ts=now_timestamp(),
    )


class TestHookDecision:
    def test_defaults_allow(self) -> None:
        d = HookDecision()
        assert d.allow is True
        assert d.reason == "phase-1 default: allow"
        assert d.rewrite_args is None
        assert d.extra == {}

    def test_rewrite_args_explicit(self) -> None:
        d = HookDecision(allow=False, reason="deny", rewrite_args={"x": 1})
        assert d.allow is False
        assert d.reason == "deny"
        assert d.rewrite_args == {"x": 1}


class TestNoOpHooks:
    def test_on_tool_call_allows(self) -> None:
        h = NoOpHooks()
        call = ToolCall(
            call_id=new_call_id(),
            tool="t",
            args={},
            trace_id=new_trace_id(),
            seq=0,
            ts=now_timestamp(),
            parent_msg_id=__import__("uuid").uuid4(),
            args_hash="0" * 64,
        )
        d = h.on_tool_call(call)
        assert isinstance(d, HookDecision)
        assert d.allow is True

    def test_on_tool_result_noop(self) -> None:
        h = NoOpHooks()
        assert h.on_tool_result(  # type: ignore[arg-type]
            call=..., result=...
        ) is None

    def test_on_final_answer_noop(self) -> None:
        h = NoOpHooks()
        assert h.on_final_answer(trace=Trace(
            trace_id=new_trace_id(),
            task_id="",
            model_id="m",
            events=[],
            final_answer="",
            start_ts=now_timestamp(),
            end_ts=now_timestamp(),
        ), verdict=None) is None


class TestProtocolCheck:
    def test_noop_satisfies_protocol(self) -> None:
        # Runtime checkable: NoOpHooks should match RuntimeHooks Protocol shape.
        assert isinstance(NoOpHooks(), RuntimeHooks)


class TestAgentAdapterABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            AgentAdapter()  # type: ignore[abstract]

    def test_subclass_must_implement_run(self) -> None:
        class Bad(AgentAdapter):
            pass

        with pytest.raises(TypeError):
            Bad()  # type: ignore[abstract]

    def test_register_and_query_tool(self) -> None:
        class Stub(AgentAdapter):
            name = "stub"
            model_id = "stub-1"

            def run(self, task_id, *, hooks, trace_writer=None):  # type: ignore[override]
                return FinalAnswer(trace=_trace())

        a = Stub()
        assert not a.has_tool("foo")
        a.register_tool("foo", lambda: 1)
        assert a.has_tool("foo")
        with pytest.raises(ValueError):
            a.register_tool("foo", lambda: 2)


class TestFinalAnswer:
    def test_text_defaults_to_trace_final_answer(self) -> None:
        fa = FinalAnswer(trace=_trace())
        assert fa.text == "hello"

    def test_text_overrides(self) -> None:
        fa = FinalAnswer(trace=_trace(), text="overridden")
        assert fa.text == "overridden"

    def test_verdict_default_none(self) -> None:
        fa = FinalAnswer(trace=_trace())
        assert fa.verdict is None
