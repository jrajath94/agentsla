"""LangGraph adapter — parity with RawLoopAdapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentsla.adapters.base import HookDecision
from agentsla.adapters.langgraph import LangGraphAdapter
from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.core.events import (
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
)
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import JsonEchoTool


class CapturingHooks:
    def __init__(self) -> None:
        self.tool_calls: list[ToolCall] = []
        self.tool_results: list[tuple[ToolCall, ToolResult]] = []
        self.final_answers: list[tuple[Trace, Verdict | None]] = []

    def on_tool_call(self, call: ToolCall) -> HookDecision:
        self.tool_calls.append(call)
        return HookDecision(allow=True, reason="captured")

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        self.tool_results.append((call, result))

    def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
        self.final_answers.append((trace, verdict))


def test_langgraph_emits_full_event_sequence(tmp_path: Path) -> None:
    db = tmp_path / "traces.duckdb"
    writer = TraceWriter(db)
    try:
        a = LangGraphAdapter(
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text="lg",
        )
        hooks = CapturingHooks()
        out = a.run("task-lg", hooks=hooks)
    finally:
        writer.close()
    kinds = [ev.kind for ev in out.trace.events]
    assert kinds == ["model_message", "tool_call", "tool_result", "model_message"]
    assert len(hooks.tool_calls) == 1
    assert len(hooks.tool_results) == 1
    assert len(hooks.final_answers) == 1


def test_langgraph_deny_shortcircuit() -> None:
    a = LangGraphAdapter(tools={"json_echo": JsonEchoTool()}, task_text="denied")

    class Deny:
        def on_tool_call(self, call: ToolCall) -> HookDecision:
            return HookDecision(allow=False, reason="deny")

        def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
            return None

        def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
            return None

    out = a.run("t", hooks=Deny())
    kinds = [ev.kind for ev in out.trace.events]
    assert kinds == ["model_message", "tool_call"]
    assert out.text == ""


def test_langgraph_no_tools_returns_echo() -> None:
    a = LangGraphAdapter(task_text="alone")
    out = a.run("t", hooks=NoOpHooks())
    assert out.text.startswith("<echo:alone>")
    assert len(out.trace.events) == 2


def test_langgraph_tool_error_surfaces() -> None:
    def boom(**_kwargs: Any) -> str:
        raise RuntimeError("kaboom")

    a = LangGraphAdapter(tools={"boom": boom})
    hooks = CapturingHooks()
    a.run("t", hooks=hooks)
    result = hooks.tool_results[0][1]
    assert result.is_error is True
    assert "kaboom" in (result.error or "")
