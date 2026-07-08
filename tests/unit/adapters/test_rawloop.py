"""Tests for RawLoopAdapter (ADAPT-01)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentsla.adapters.base import FinalAnswer, HookDecision
from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import EchoModel, RawLoopAdapter
from agentsla.core.events import (
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
)
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import ClockTool, FetchTool, JsonEchoTool


class CapturingHooks:
    """RuntimeHooks implementation that records every callback invocation."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allowed = allow
        self.tool_calls: list[ToolCall] = []
        self.tool_results: list[tuple[ToolCall, ToolResult]] = []
        self.final_answers: list[tuple[Trace, Verdict | None]] = []

    def on_tool_call(self, call: ToolCall) -> HookDecision:
        self.tool_calls.append(call)
        return HookDecision(allow=self.allowed, reason="captured")

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        self.tool_results.append((call, result))

    def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
        self.final_answers.append((trace, verdict))


class TestEchoModel:
    def test_completion_is_deterministic(self) -> None:
        m = EchoModel()
        assert m.complete(user_text="alpha") == "<echo:alpha>"
        assert m.complete(user_text="alpha") == "<echo:alpha>"


class TestRawLoopRegistration:
    def test_register_tool(self) -> None:
        a = RawLoopAdapter()
        a.register_tool("json_echo", JsonEchoTool())
        assert a.has_tool("json_echo")

    def test_register_tool_duplicate_raises(self) -> None:
        a = RawLoopAdapter()
        a.register_tool("t", lambda: 1)
        with pytest.raises(ValueError):
            a.register_tool("t", lambda: 2)


class TestRawLoopRunNoTools:
    def test_run_without_tools_returns_echo(self) -> None:
        a = RawLoopAdapter(task_text="hello")
        hooks = CapturingHooks()
        out = a.run("task-1", hooks=hooks)
        assert isinstance(out, FinalAnswer)
        assert out.text.startswith("<echo:hello>")
        # Only one ModelMessage emitted (no tool call / result).
        kinds = [ev.kind for ev in out.trace.events]
        assert kinds == ["model_message", "model_message"]
        # Both ModelMessages have the same trace_id.
        assert {ev.trace_id for ev in out.trace.events} == {out.trace.trace_id}
        # Hooks: only final-answer invoked.
        assert hooks.tool_calls == []
        assert hooks.tool_results == []
        assert len(hooks.final_answers) == 1


class TestRawLoopRunWithTool:
    def test_run_emits_full_event_sequence(self, tmp_path: Path) -> None:
        writer = TraceWriter(tmp_path / "traces.duckdb")
        try:
            a = RawLoopAdapter(
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text="seed",
            )
            hooks = CapturingHooks()
            out = a.run("task-2", hooks=hooks)
        finally:
            writer.close()

        # 4 events: user, tool_call, tool_result, final assistant.
        kinds = [ev.kind for ev in out.trace.events]
        assert kinds == ["model_message", "tool_call", "tool_result", "model_message"]

        # Hooks: tool_call seen, tool_result seen, final_answer seen.
        assert len(hooks.tool_calls) == 1
        assert hooks.tool_calls[0].tool == "json_echo"
        assert len(hooks.tool_results) == 1
        assert hooks.tool_results[0][1].is_error is False
        assert len(hooks.final_answers) == 1
        # Final text carries the tool output verbatim (json_echo's
        # canonical JSON string: ``{"text":"seed"}``).
        assert out.text.endswith("::{" + '"text":"seed"' + "}")

    def test_persisted_events_replayable(self, tmp_path: Path) -> None:
        db = tmp_path / "traces.duckdb"
        writer = TraceWriter(db)
        try:
            a = RawLoopAdapter(
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text="abc",
            )
            out = a.run("task-3", hooks=NoOpHooks())
            trace_id = out.trace.trace_id
        finally:
            writer.close()

        # Re-open via reader and reconstruct the same trace by id.
        from agentsla.core.trace import TraceReader

        with TraceReader(db) as reader:
            rebuilt = reader.read_trace(trace_id)
        assert rebuilt is not None
        kinds = [ev.kind for ev in rebuilt.events]
        assert kinds == ["model_message", "tool_call", "tool_result", "model_message"]


class TestRawLoopDenyShortCircuit:
    def test_deny_hook_skips_tool_execution(self) -> None:
        a = RawLoopAdapter(
            tools={"json_echo": JsonEchoTool()},
            task_text="never-runs",
        )
        hooks = CapturingHooks(allow=False)
        out = a.run("task-deny", hooks=hooks)
        # 2 events: user, final assistant with empty text.
        kinds = [ev.kind for ev in out.trace.events]
        assert kinds == ["model_message", "tool_call"]
        assert out.text == ""
        assert len(hooks.tool_calls) == 1
        assert hooks.tool_results == []


class TestRawLoopToolError:
    def test_tool_exception_surfaces_as_is_error(self) -> None:
        def boom(**_kwargs: Any) -> str:
            raise RuntimeError("kapow")

        a = RawLoopAdapter(tools={"boom": boom}, task_text="x")
        hooks = CapturingHooks()
        a.run("task-err", hooks=hooks)
        result = hooks.tool_results[0][1]
        assert result.is_error is True
        assert "RuntimeError" in (result.error or "")
        assert "kapow" in (result.error or "")


class TestDeterministicTools:
    def test_fetch_happy(self, tmp_path: Path) -> None:
        fixture = tmp_path / "data.txt"
        fixture.write_text("hello world", encoding="utf-8")
        tool = FetchTool(root=tmp_path)
        assert tool(path="data.txt") == "hello world"

    def test_fetch_blocks_traversal(self, tmp_path: Path) -> None:
        tool = FetchTool(root=tmp_path)
        with pytest.raises(FileNotFoundError):
            tool(path="../../etc/passwd")

    def test_fetch_missing_raises(self, tmp_path: Path) -> None:
        tool = FetchTool(root=tmp_path)
        with pytest.raises(FileNotFoundError):
            tool(path="does-not-exist.txt")

    def test_clock_returns_fixed_value(self) -> None:
        ts = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
        tool = ClockTool(fixed=ts)
        assert tool() == ts.isoformat()

    def test_clock_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError):
            ClockTool(fixed=datetime(2026, 7, 8))  # type: ignore[arg-type]

    def test_json_echo_roundtrip(self) -> None:
        tool = JsonEchoTool()
        out = tool(b=2, a=1)
        assert json.loads(out) == {"a": 1, "b": 2}
