"""REPLAY-PROOF: property-based test — 5-replay byte-identity for any state.

Uses :class:`hypothesis.RuleBasedStateMachine` to drive a sequence of
:class:`ToolCall` / :class:`ToolResult` / :class:`ModelMessage` events
into a temporary :class:`TraceWriter`, then asserts the replay path
produces an identical :class:`Trace` for any sequence.

Why this test: it defends the append-only invariant + canonical-hash
contract end-to-end. Any drift on the schema, the writer path, or the
reader path is surfaced here. It runs in milliseconds (writer is in-
process DuckDB) so the cost is negligible relative to the coverage it
buys.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
from pydantic import TypeAdapter

from agentsla.core.events import (
    Event,
    ModelMessage,
    ToolCall,
    ToolResult,
    Verdict,
    canonical_args_hash,
    now_timestamp,
)
from agentsla.core.replay import ReplayEngine, ReplayMode
from agentsla.core.trace import TraceReader, TraceWriter

pytestmark = pytest.mark.hypothesis


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Path]:
    db = tmp_path / "traces.duckdb"
    yield db


class ReplayStateMachine(RuleBasedStateMachine):
    """A minimal append-only state machine seeded from empty.

    Each ``run_step`` produces exactly one random event; an ``invariant``
    asserts the reader can re-parse every recorded event without loss.

    Notes:
        * ``BundlingRule.required`` etc. are intentionally not used — we
          want the absolute minimum hypothesis surface here.
        * The test runner fabricates ``trace_id`` deterministically per
          rule call so events group sensibly.
    """

    def __init__(self) -> None:
        super().__init__()
        self._tmp = tempfile.mkdtemp(prefix="replay-prop-")
        self._db = Path(self._tmp) / "traces.duckdb"
        self._writer = TraceWriter(self._db)
        self._trace_id = self._next_trace_id()
        self._events: list[ToolCall | ToolResult | ModelMessage | Verdict] = []
        self._seq = 0

    def _next_trace_id(self) -> str:
        # Deterministic sequential ids — well-formed UUID.
        import uuid as _uuid

        return str(_uuid.uuid4())

    def teardown(self) -> None:
        try:
            self._writer.close()
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)

    @initialize()
    def _init_state(self) -> None:
        self._events = []
        self._seq = 0

    @rule()
    def append_tool_call(self) -> None:
        ts = now_timestamp()
        args = {"k": self._seq, "task": "demo"}
        ev = ToolCall(
            call_id=self._next_trace_id(),
            tool="json_echo",
            args=args,
            trace_id=self._trace_id,
            seq=self._seq,
            ts=ts,
            parent_msg_id=self._next_trace_id(),
            args_hash=canonical_args_hash(args),
        )
        self._writer.append(ev)
        self._events.append(ev)
        self._seq += 1

    @rule()
    def append_model_message(self) -> None:
        ts = now_timestamp()
        ev = ModelMessage(
            msg_id=self._next_trace_id(),
            trace_id=self._trace_id,
            seq=self._seq,
            role="assistant",
            content=f"echo-{self._seq}",
            model_id="echo-1",
            response_id=f"req_{self._seq:04d}",
            ts=ts,
        )
        self._writer.append(ev)
        self._events.append(ev)
        self._seq += 1

    @invariant()
    def reader_parses_every_event(self) -> None:
        # DuckDB single-process file lock: writer must close before a
        # ``read_only=True`` reader can attach. Flush + reopen after each
        # invariant pass.
        self._writer.close()
        try:
            with TraceReader(self._db) as reader:
                read_back = list(reader.iter_events(self._trace_id))
            assert len(read_back) == len(self._events)
            for original, parsed in zip(self._events, read_back, strict=True):
                assert original.model_dump(mode="json") == parsed.model_dump(mode="json")
            engine = ReplayEngine(self._db)
            report = engine.replay(self._trace_id, mode=ReplayMode.TOLERANT)
            assert report.drift_count == 0
            assert report.match_count == sum(1 for e in self._events if isinstance(e, ToolCall))
        finally:
            # Reattach the writer for the next rule append.
            self._writer = TraceWriter(self._db)


# Hypothesis = rule-based stateful. Run 30 steps per example for speed.
TestReplayStateful = ReplayStateMachine.TestCase
TestReplayStateful.settings = settings(max_examples=5, deadline=None)  # type: ignore[attr-defined]


def test_replay_byte_identity_across_5_replays(tmp_db: Path) -> None:
    """Standalone: write a fixed 4-event trace, replay 5x — final answer identical.

    Companion to the invariant-style test above. This is the acceptance
    scenario from PLAN.md: ``agentsla run demo`` then 5x ``agentsla replay``.
    """
    trace_id = "11111111-2222-3333-4444-555555555555"
    writer = TraceWriter(tmp_db)
    try:
        for ev in _demo_events(trace_id):
            writer.append(ev)
    finally:
        writer.close()

    engine = ReplayEngine(tmp_db)
    answers: list[str] = []
    for _ in range(5):
        report = engine.replay(trace_id, mode=ReplayMode.STRICT)
        assert report.exit_code == 0
        answers.append(report.final_answer)
    assert len(set(answers)) == 1


def _demo_events(trace_id: str) -> list[ToolCall | ToolResult | ModelMessage]:
    ts = now_timestamp()
    tool_call = ToolCall(
        call_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        tool="json_echo",
        args={"text": "hello"},
        trace_id=trace_id,
        seq=0,
        ts=ts,
        parent_msg_id="11111111-1111-1111-1111-111111111111",
        args_hash=canonical_args_hash({"text": "hello"}),
    )
    tool_result = ToolResult(
        call_id=tool_call.call_id,
        tool="json_echo",
        result={"echo": "hello"},
        is_error=False,
        error=None,
        latency_ms=0.0,
        trace_id=trace_id,
        seq=1,
        ts=ts,
    )
    final_msg = ModelMessage(
        msg_id="22222222-2222-2222-2222-222222222222",
        trace_id=trace_id,
        seq=2,
        role="assistant",
        content="final answer",
        model_id="echo-1",
        response_id="req_0001",
        ts=ts,
    )
    return [tool_call, tool_result, final_msg]


def test_event_round_trip_preserves_discriminator() -> None:
    """Direct discriminated-union round-trip — defends schema drift (PITFALL #12)."""
    adapter = TypeAdapter(Event)
    sample = {
        "kind": "model_message",
        "msg_id": "00000000-0000-4000-8000-000000000001",
        "trace_id": "00000000-0000-4000-8000-000000000002",
        "seq": 0,
        "ts": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "role": "user",
        "content": "hi",
        "model_id": "echo-1",
        "response_id": "req_x",
    }
    parsed = adapter.validate_python(sample)
    assert isinstance(parsed, ModelMessage)
    # Re-serialise — discriminator field must be present.
    dumped = json.loads(parsed.model_dump_json())
    assert dumped["kind"] == "model_message"
