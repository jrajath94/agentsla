"""Unit tests for ClaudeSdkAdapter — uses a fake SDK client, no real API calls.

The Claude Agent SDK is injected via duck-typing: any object with
``.query(prompt, **kw) -> Iterator[Message]`` is acceptable. These tests
use a ``FakeSdkClient`` that yields scripted messages, so the suite is
hermetic and runs without network access or an API key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentsla.adapters.base import HookDecision
from agentsla.adapters.claude_sdk import ClaudeSdkAdapter
from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.core.events import (
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
)
from agentsla.core.trace import TraceReader, TraceWriter
from agentsla.tools.deterministic import JsonEchoTool

# ---------------------------------------------------------------------------
# Fakes — substitute for `claude_agent_sdk`'s real client.
# ---------------------------------------------------------------------------


class FakeSdkMessage:
    """Stand-in for the real `claude_agent_sdk` message types.

    The Claude Agent SDK yields different message types (TextBlock,
    ToolUseBlock, ToolResultBlock). We compress them into one class with
    a ``kind`` discriminator that matches the adapter's dispatch.
    """

    def __init__(self, *, kind: str, **kw: Any) -> None:
        self.kind = kind
        for k, v in kw.items():
            setattr(self, k, v)


class FakeSdkClient:
    """Records invocations; yields scripted messages.

    Each call to ``query`` consumes the next scripted message list, so
    tests can simulate multi-turn SDK behavior.
    """

    def __init__(self, messages: list[FakeSdkMessage] | list[list[FakeSdkMessage]]) -> None:
        # Normalize: single list is treated as a single turn.
        if messages and not isinstance(messages[0], list):
            self._turns: list[list[FakeSdkMessage]] = [list(messages)]
        else:
            self._turns = [list(turn) for turn in messages]  # type: ignore[list-item]
        self._turn_index = 0
        self.calls: list[dict[str, Any]] = []

    def query(self, prompt: str, **kw: Any) -> Any:
        self.calls.append({"prompt": prompt, **kw})
        if self._turn_index >= len(self._turns):
            # No more scripted turns — yield empty (caller is mis-using the fake).
            return iter([])
        turn = self._turns[self._turn_index]
        self._turn_index += 1
        return iter(turn)


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def writer(tmp_path: Path) -> TraceWriter:
    """A per-test TraceWriter pointing at a tmp DuckDB file."""
    db = tmp_path / "traces.duckdb"
    return TraceWriter(db)


# ---------------------------------------------------------------------------
# Adapter identity + registration
# ---------------------------------------------------------------------------


class TestClaudeSdkAdapterIdentity:
    def test_name_and_model_id_defaults(self) -> None:
        a = ClaudeSdkAdapter(client=FakeSdkClient([]))
        assert a.name == "claude-sdk"
        assert a.model_id == "claude-haiku-4-5-20251001"

    def test_register_tool_roundtrip(self) -> None:
        a = ClaudeSdkAdapter(client=FakeSdkClient([]))
        a.register_tool("json_echo", JsonEchoTool())
        assert a.has_tool("json_echo")

    def test_register_tool_duplicate_raises(self) -> None:
        a = ClaudeSdkAdapter(client=FakeSdkClient([]))
        a.register_tool("t", lambda: 1)
        with pytest.raises(ValueError):
            a.register_tool("t", lambda: 2)


# ---------------------------------------------------------------------------
# Tool-call ordering contract
# ---------------------------------------------------------------------------


class TestClaudeSdkToolCallOrdering:
    def test_emits_tool_call_before_invocation(self, writer: TraceWriter) -> None:
        """Adapter must write a ToolCall event before invoking the tool fn."""
        tool_use = FakeSdkMessage(
            kind="tool_use",
            id="call_1",
            name="json_echo",
            input={"text": "hello"},
            parent_msg_id="msg_1",
        )
        final = FakeSdkMessage(kind="text", text="done", parent_msg_id="msg_1")
        fake = FakeSdkClient([tool_use, final])
        try:
            a = ClaudeSdkAdapter(
                client=fake,
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text="hello",
            )
            hooks = CapturingHooks()
            out = a.run("t-order", hooks=hooks)
        finally:
            writer.close()

        # Two event writes before tool fn runs (model_message + tool_call).
        # The captured hooks prove the order: on_tool_call fired once before
        # any tool_result, and the tool_call event precedes tool_result.
        assert len(hooks.tool_calls) == 1
        assert hooks.tool_calls[0].tool == "json_echo"
        assert len(hooks.tool_results) == 1
        # First event in the trace is the user ModelMessage, second is ToolCall.
        kinds = [ev.kind for ev in out.trace.events]
        assert kinds[0] == "model_message"
        assert kinds[1] == "tool_call"
        assert kinds[2] == "tool_result"
        assert kinds[3] == "model_message"

    def test_emits_full_event_sequence(self, writer: TraceWriter) -> None:
        """Same 4-event shape as rawloop: user → tool_call → tool_result → final."""
        tool_use = FakeSdkMessage(
            kind="tool_use",
            id="call_x",
            name="json_echo",
            input={"text": "seed"},
            parent_msg_id="msg_1",
        )
        final = FakeSdkMessage(kind="text", text="ok", parent_msg_id="msg_1")
        fake = FakeSdkClient([tool_use, final])
        try:
            a = ClaudeSdkAdapter(
                client=fake,
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text="seed",
            )
            out = a.run("t-full", hooks=CapturingHooks())
        finally:
            writer.close()

        kinds = [ev.kind for ev in out.trace.events]
        assert kinds == ["model_message", "tool_call", "tool_result", "model_message"]
        # Final answer text is whatever the mock final SDK message says.
        assert out.text == "ok"
        # No leak of the tool name in the final text.
        assert "json_echo" not in out.text
        # The tool result IS recorded on the trace (one of the 4 events above).
        assert any(ev.kind == "tool_result" for ev in out.trace.events)

    def test_persisted_events_roundtrip_via_reader(self, writer: TraceWriter) -> None:
        """TraceWriter → TraceReader round-trips the same 4-event shape."""
        tool_use = FakeSdkMessage(
            kind="tool_use",
            id="call_z",
            name="json_echo",
            input={"text": "abc"},
            parent_msg_id="msg_1",
        )
        final = FakeSdkMessage(kind="text", text="done", parent_msg_id="msg_1")
        fake = FakeSdkClient([tool_use, final])
        try:
            a = ClaudeSdkAdapter(
                client=fake,
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text="abc",
            )
            out = a.run("t-rt", hooks=NoOpHooks())
            trace_id = out.trace.trace_id
        finally:
            writer.close()

        db_path = writer.db_path
        with TraceReader(db_path) as reader:
            rebuilt = reader.read_trace(trace_id)
        assert rebuilt is not None
        kinds = [ev.kind for ev in rebuilt.events]
        assert kinds == ["model_message", "tool_call", "tool_result", "model_message"]


# ---------------------------------------------------------------------------
# Policy gate integration
# ---------------------------------------------------------------------------


class TestClaudeSdkPolicyGate:
    def test_deny_short_circuits_tool_invocation(self, writer: TraceWriter) -> None:
        """HookDecision(allow=False) → tool fn never invoked → on_final_answer still called."""
        tool_use = FakeSdkMessage(
            kind="tool_use",
            id="call_deny",
            name="json_echo",
            input={"text": "denied"},
            parent_msg_id="msg_1",
        )
        final = FakeSdkMessage(kind="text", text="ignored", parent_msg_id="msg_1")
        fake = FakeSdkClient([tool_use, final])
        try:
            a = ClaudeSdkAdapter(
                client=fake,
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text="denied",
            )
            hooks = CapturingHooks(allow=False)
            out = a.run("t-deny", hooks=hooks)
        finally:
            writer.close()

        # Tool fn was NOT invoked (no tool_result captured).
        assert hooks.tool_results == []
        assert len(hooks.tool_calls) == 1
        # Final answer hook still called exactly once with empty text.
        assert len(hooks.final_answers) == 1
        assert out.text == ""
        # Trace shape: user message + tool_call (no tool_result, no final assistant text).
        kinds = [ev.kind for ev in out.trace.events]
        assert kinds == ["model_message", "tool_call"]


# ---------------------------------------------------------------------------
# No-tool path: SDK returns only text
# ---------------------------------------------------------------------------


class TestClaudeSdkTextOnly:
    def test_text_only_run_no_tool_call(self, writer: TraceWriter) -> None:
        """SDK yields only a text message → no tool_call / tool_result events."""
        final = FakeSdkMessage(kind="text", text="<echo:alone>", parent_msg_id="msg_1")
        fake = FakeSdkClient([final])
        try:
            a = ClaudeSdkAdapter(
                client=fake,
                trace_writer=writer,
                task_text="alone",
            )
            hooks = CapturingHooks()
            out = a.run("t-text", hooks=hooks)
        finally:
            writer.close()

        # Two events: user model_message, final assistant model_message.
        kinds = [ev.kind for ev in out.trace.events]
        assert kinds == ["model_message", "model_message"]
        assert hooks.tool_calls == []
        assert hooks.tool_results == []
        assert len(hooks.final_answers) == 1
        assert out.text == "<echo:alone>"


# ---------------------------------------------------------------------------
# Tool error path
# ---------------------------------------------------------------------------


class TestClaudeSdkToolError:
    def test_tool_exception_surfaces_as_is_error(self) -> None:
        """Tool raises → ToolResult(is_error=True, error=<repr>) → no exception escapes."""

        def boom(**_kwargs: Any) -> str:
            raise RuntimeError("kapow")

        tool_use = FakeSdkMessage(
            kind="tool_use",
            id="call_err",
            name="boom",
            input={},
            parent_msg_id="msg_1",
        )
        final = FakeSdkMessage(kind="text", text="ok", parent_msg_id="msg_1")
        fake = FakeSdkClient([tool_use, final])
        a = ClaudeSdkAdapter(
            client=fake,
            tools={"boom": boom},
            task_text="x",
        )
        hooks = CapturingHooks()
        # Must NOT raise — adapter catches the tool exception.
        a.run("t-err", hooks=hooks)

        assert len(hooks.tool_results) == 1
        result = hooks.tool_results[0][1]
        assert result.is_error is True
        assert "RuntimeError" in (result.error or "")
        assert "kapow" in (result.error or "")


# ---------------------------------------------------------------------------
# Parity with rawloop
# ---------------------------------------------------------------------------


class TestClaudeSdkParityWithRawloop:
    def test_same_event_kind_sequence(self, writer: TraceWriter) -> None:
        """Same task + same tool registry → identical event-kind sequence.

        Rawloop is hermetic (EchoModel + JsonEchoTool). For ClaudeSdkAdapter
        we script the SDK to emit exactly the same tool_use + text pattern,
        so the resulting event-kind sequence must match rawloop's.
        """
        # Rawloop side
        raw = RawLoopAdapter(
            tools={"json_echo": JsonEchoTool()},
            task_text="parity",
        )
        raw_out = raw.run("parity", hooks=NoOpHooks())
        raw_kinds = [ev.kind for ev in raw_out.trace.events]

        # ClaudeSdkAdapter side (scripted SDK)
        tool_use = FakeSdkMessage(
            kind="tool_use",
            id="call_p",
            name="json_echo",
            input={"text": "parity"},
            parent_msg_id="msg_1",
        )
        final = FakeSdkMessage(
            kind="text",
            text="<echo:parity>::" + '{"text":"parity"}',
            parent_msg_id="msg_1",
        )
        fake = FakeSdkClient([tool_use, final])
        try:
            claude = ClaudeSdkAdapter(
                client=fake,
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                task_text="parity",
            )
            claude_out = claude.run("parity", hooks=NoOpHooks())
        finally:
            writer.close()
        claude_kinds = [ev.kind for ev in claude_out.trace.events]

        assert raw_kinds == claude_kinds
        # Specifically: user → tool_call → tool_result → final assistant.
        assert raw_kinds == ["model_message", "tool_call", "tool_result", "model_message"]


# ---------------------------------------------------------------------------
# SDK query call shape
# ---------------------------------------------------------------------------


class TestClaudeSdkClientQuery:
    def test_query_called_with_prompt(self) -> None:
        """Adapter calls ``client.query(prompt)`` at least once."""
        final = FakeSdkMessage(kind="text", text="ok", parent_msg_id="msg_1")
        fake = FakeSdkClient([final])
        a = ClaudeSdkAdapter(client=fake, task_text="the task")
        a.run("t-q", hooks=NoOpHooks())
        assert len(fake.calls) >= 1
        assert any("the task" in c["prompt"] for c in fake.calls)

    def test_query_called_with_tools_in_kwargs(self) -> None:
        """When tools are registered, the adapter passes them to ``query``."""
        final = FakeSdkMessage(kind="text", text="ok", parent_msg_id="msg_1")
        fake = FakeSdkClient([final])
        a = ClaudeSdkAdapter(
            client=fake,
            tools={"json_echo": JsonEchoTool()},
            task_text="hi",
        )
        a.run("t-qk", hooks=NoOpHooks())
        # At least one call carries a ``tools`` kwarg naming the registered tool.
        assert any("tools" in c and "json_echo" in (c["tools"] or []) for c in fake.calls)
