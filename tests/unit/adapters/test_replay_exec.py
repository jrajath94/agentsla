"""Execution replay (adapter-driven re-execution with recorded tool stubbing).

The interview-proof contract: record a REAL tool-loop run, re-execute it
with the tools stubbed from the recording, and get a byte-identical final
answer. Divergence and not-replayable paths are pinned too.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.adapters.replay_exec import (
    ExecutionReplayReport,
    ReplayedToolError,
    _StubToolRegistry,
    replay_execution,
)
from agentsla.core.events import (
    ModelMessage,
    ToolCall,
    ToolResult,
    canonical_args_hash,
    now_timestamp,
)
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import JsonEchoTool


def _record_real_run(db: Path, *, task_text: str = "replay-me") -> uuid.UUID:
    """Run the real tool loop once and persist the trace."""
    writer = TraceWriter(db)
    try:
        adapter = RawLoopAdapter(
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text=task_text,
        )
        out = adapter.run("task-exec-replay", hooks=NoOpHooks())
        return out.trace.trace_id
    finally:
        writer.close()


class TestByteIdenticalReplay:
    def test_recorded_run_replays_byte_identical(self, tmp_path: Path) -> None:
        db = tmp_path / "traces.duckdb"
        trace_id = _record_real_run(db)

        report = replay_execution(trace_id, db)

        assert report.exit_code == 0
        assert report.byte_identical is True
        assert report.replayed_answer == report.recorded_answer
        assert report.recorded_tool_calls == 1
        assert report.replayed_tool_calls == 1
        assert report.args_hash_matches == 1

    def test_replay_is_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "traces.duckdb"
        trace_id = _record_real_run(db)

        first = replay_execution(trace_id, db)
        second = replay_execution(trace_id, db)

        assert first.byte_identical and second.byte_identical
        assert first.replayed_answer == second.replayed_answer

    def test_no_tool_trace_replays_byte_identical(self, tmp_path: Path) -> None:
        db = tmp_path / "traces.duckdb"
        writer = TraceWriter(db)
        try:
            adapter = RawLoopAdapter(trace_writer=writer, task_text="no-tools")
            out = adapter.run("task-no-tools", hooks=NoOpHooks())
            trace_id = out.trace.trace_id
        finally:
            writer.close()

        report = replay_execution(trace_id, db)
        assert report.exit_code == 0
        assert report.byte_identical is True
        assert report.recorded_tool_calls == 0


class TestDivergence:
    def test_tampered_final_answer_diverges(self, tmp_path: Path) -> None:
        """Hand-write a trace whose stored answer is NOT what re-execution produces."""
        db = tmp_path / "traces.duckdb"
        trace_id = uuid.uuid4()
        ts = now_timestamp()
        user = ModelMessage(
            msg_id=uuid.uuid4(),
            trace_id=trace_id,
            seq=0,
            role="user",
            content="tamper-task",
            model_id="echo-1",
            response_id="req_x",
            ts=ts,
        )
        call_id = uuid.uuid4()
        args = {"text": "tamper-task"}
        call = ToolCall(
            call_id=call_id,
            tool="json_echo",
            args=args,
            trace_id=trace_id,
            seq=1,
            ts=ts,
            parent_msg_id=user.msg_id,
            args_hash=canonical_args_hash(args),
        )
        result = ToolResult(
            call_id=call_id,
            tool="json_echo",
            result='{"text": "tamper-task"}',
            is_error=False,
            error=None,
            latency_ms=0.0,
            trace_id=trace_id,
            seq=2,
            ts=ts,
        )
        final = ModelMessage(
            msg_id=uuid.uuid4(),
            trace_id=trace_id,
            seq=3,
            role="assistant",
            content="TAMPERED ANSWER",
            model_id="echo-1",
            response_id="req_x.final",
            ts=ts,
        )
        writer = TraceWriter(db)
        try:
            for ev in (user, call, result, final):
                writer.append(ev)
        finally:
            writer.close()

        report = replay_execution(trace_id, db)
        assert report.exit_code == 1
        assert report.byte_identical is False
        assert report.recorded_answer == "TAMPERED ANSWER"
        assert report.replayed_answer != "TAMPERED ANSWER"
        # The stubbed tool + deterministic model still reproduce the true run:
        assert report.replayed_answer == '<echo:tamper-task>::{"text": "tamper-task"}'


class TestNotReplayable:
    def test_unknown_trace_exits_2(self, tmp_path: Path) -> None:
        db = tmp_path / "traces.duckdb"
        _record_real_run(db)  # store exists, but we ask for a different id
        report = replay_execution(uuid.uuid4(), db)
        assert report.exit_code == 2
        assert "not found" in report.note

    def test_live_model_trace_refused(self, tmp_path: Path) -> None:
        """A trace recorded with a live model must NOT pretend to replay."""
        db = tmp_path / "traces.duckdb"
        trace_id = uuid.uuid4()
        ts = now_timestamp()
        user = ModelMessage(
            msg_id=uuid.uuid4(),
            trace_id=trace_id,
            seq=0,
            role="user",
            content="live-task",
            model_id="claude-haiku-4-5-20251001",
            response_id="req_live",
            ts=ts,
        )
        final = ModelMessage(
            msg_id=uuid.uuid4(),
            trace_id=trace_id,
            seq=1,
            role="assistant",
            content="a live answer",
            model_id="claude-haiku-4-5-20251001",
            response_id="req_live.final",
            ts=ts,
        )
        writer = TraceWriter(db)
        try:
            writer.append(user)
            writer.append(final)
        finally:
            writer.close()

        report = replay_execution(trace_id, db)
        assert report.exit_code == 2
        assert "deterministic model" in report.note
        assert "claude-haiku-4-5-20251001" in report.note

    def test_string_trace_id_accepted(self, tmp_path: Path) -> None:
        db = tmp_path / "traces.duckdb"
        trace_id = _record_real_run(db)
        report = replay_execution(str(trace_id), db)
        assert report.exit_code == 0


class TestStubRegistry:
    def test_error_result_reraises(self) -> None:
        trace_id = uuid.uuid4()
        call_id = uuid.uuid4()
        ts = now_timestamp()
        args = {"text": "boom"}
        call = ToolCall(
            call_id=call_id,
            tool="fetch",
            args=args,
            trace_id=trace_id,
            seq=0,
            ts=ts,
            parent_msg_id=None,
            args_hash=canonical_args_hash(args),
        )
        result = ToolResult(
            call_id=call_id,
            tool="fetch",
            result=None,
            is_error=True,
            error="FileNotFoundError: nope",
            latency_ms=0.0,
            trace_id=trace_id,
            seq=1,
            ts=ts,
        )
        registry = _StubToolRegistry.from_events([call, result])
        stub = registry.make_stub("fetch")
        with pytest.raises(ReplayedToolError, match="FileNotFoundError"):
            stub(text="boom")

    def test_exhausted_queue_raises(self) -> None:
        registry = _StubToolRegistry()
        stub = registry.make_stub("ghost")
        with pytest.raises(ReplayedToolError, match="no recorded result"):
            stub()


class TestReportShape:
    def test_json_dict_roundtrip_keys(self, tmp_path: Path) -> None:
        db = tmp_path / "traces.duckdb"
        trace_id = _record_real_run(db)
        report = replay_execution(trace_id, db)
        payload = report.to_json_dict()
        assert payload["trace_id"] == str(trace_id)
        assert set(payload) == {
            "trace_id",
            "byte_identical",
            "recorded_answer",
            "replayed_answer",
            "recorded_tool_calls",
            "replayed_tool_calls",
            "args_hash_matches",
            "exit_code",
            "note",
        }
        assert isinstance(report, ExecutionReplayReport)
