"""Cross-adapter parity extended to three-way: rawloop x langgraph x claude_sdk.

Each demo task runs through all three adapters. The resulting event-kind
sequences are compared modulo UUIDs (every adapter generates its own
trace_id and call_id; we only assert shape, not identity).

The ClaudeSdkAdapter side uses a scripted fake SDK client, so no real
network is involved — the parity test stays hermetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentsla.adapters.claude_sdk import ClaudeSdkAdapter
from agentsla.adapters.langgraph import LangGraphAdapter
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.core.events import ModelMessage, ToolCall, ToolResult, Verdict
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import JsonEchoTool

# ---------------------------------------------------------------------------
# Fake SDK client — minimal surface for these tests.
# ---------------------------------------------------------------------------


class FakeSdkMessage:
    def __init__(self, *, kind: str, **kw: Any) -> None:
        self.kind = kind
        for k, v in kw.items():
            setattr(self, k, v)


class FakeSdkClient:
    """One-turn scripted SDK: yields tool_use then text."""

    def __init__(self, task_text: str, tool_name: str = "json_echo") -> None:
        self._task_text = task_text
        self._tool_name = tool_name
        self.calls: list[dict[str, Any]] = []

    def query(self, prompt: str, **kw: Any) -> Any:
        self.calls.append({"prompt": prompt, **kw})
        return iter(
            [
                FakeSdkMessage(
                    kind="tool_use",
                    id="call_p",
                    name=self._tool_name,
                    input={"text": self._task_text},
                    parent_msg_id="msg_1",
                ),
                FakeSdkMessage(
                    kind="text",
                    text=f"<echo:{self._task_text}>::" + '{"text":"' + self._task_text + '"}',
                    parent_msg_id="msg_1",
                ),
            ]
        )


# ---------------------------------------------------------------------------
# Demo tasks
# ---------------------------------------------------------------------------


DEMO_TASKS = [
    "echo-1",
    "echo-2",
    "echo-3",
]


def _kinds(events: list[ToolCall | ToolResult | ModelMessage | Verdict]) -> list[str]:
    return [ev.kind for ev in events]


def _claude(task_text: str, tmp_path: Path) -> list[str]:
    """Run ClaudeSdkAdapter on ``task_text``; return event-kind sequence."""
    writer = TraceWriter(tmp_path / "claude.duckdb")
    try:
        a = ClaudeSdkAdapter(
            client=FakeSdkClient(task_text),
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text=task_text,
        )
        out = a.run("parity", hooks=NoOpHooks())
    finally:
        writer.close()
    return _kinds(list(out.trace.events))


def _rawloop(task_text: str) -> list[str]:
    a = RawLoopAdapter(
        tools={"json_echo": JsonEchoTool()},
        task_text=task_text,
    )
    out = a.run("parity", hooks=NoOpHooks())
    return _kinds(list(out.trace.events))


def _langgraph(task_text: str) -> list[str]:
    a = LangGraphAdapter(
        tools={"json_echo": JsonEchoTool()},
        task_text=task_text,
    )
    out = a.run("parity", hooks=NoOpHooks())
    return _kinds(list(out.trace.events))


# ---------------------------------------------------------------------------
# Hooks placeholder — tests use NoOpHooks via direct call.
# ---------------------------------------------------------------------------
from agentsla.adapters.noop_hooks import NoOpHooks  # noqa: E402

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_three_way_parity_on_echo_task(tmp_path: Path) -> None:
    """All three adapters produce identical event-kind sequence for echo task."""
    task = "echo-1"
    writer = TraceWriter(tmp_path / "claude.duckdb")
    try:
        claude_a = ClaudeSdkAdapter(
            client=FakeSdkClient(task),
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text=task,
        )
        claude_out = claude_a.run("parity", hooks=NoOpHooks())
        claude_kinds = _kinds(list(claude_out.trace.events))
    finally:
        writer.close()

    raw_kinds = _rawloop(task)
    lg_kinds = _langgraph(task)

    expected = ["model_message", "tool_call", "tool_result", "model_message"]
    assert raw_kinds == expected
    assert lg_kinds == expected
    assert claude_kinds == expected


def test_three_way_parity_across_demo_tasks(tmp_path: Path) -> None:
    """All three adapters produce identical event-kind sequence for all demo tasks."""
    for task in DEMO_TASKS:
        writer = TraceWriter(tmp_path / f"claude-{task}.duckdb")
        try:
            claude_a = ClaudeSdkAdapter(
                client=FakeSdkClient(task),
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text=task,
            )
            claude_out = claude_a.run("parity", hooks=NoOpHooks())
            claude_kinds = _kinds(list(claude_out.trace.events))
        finally:
            writer.close()

        raw_kinds = _rawloop(task)
        lg_kinds = _langgraph(task)

        assert claude_kinds == raw_kinds == lg_kinds, (
            f"parity broken for task={task!r}: rawloop={raw_kinds}, langgraph={lg_kinds}, claude_sdk={claude_kinds}"
        )
        assert claude_kinds == ["model_message", "tool_call", "tool_result", "model_message"]


def test_three_way_parity_event_count_matches(tmp_path: Path) -> None:
    """All three adapters produce the same event count (4) for an echo task."""
    task = "count-task"
    writer = TraceWriter(tmp_path / "claude-count.duckdb")
    try:
        claude_a = ClaudeSdkAdapter(
            client=FakeSdkClient(task),
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text=task,
        )
        claude_out = claude_a.run("parity", hooks=NoOpHooks())
        claude_count = len(claude_out.trace.events)
    finally:
        writer.close()

    raw_count = len(_rawloop(task))
    lg_count = len(_langgraph(task))

    assert raw_count == lg_count == claude_count == 4
