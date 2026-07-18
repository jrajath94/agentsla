"""Execution replay — adapter-driven re-execution with recorded tool-result stubbing.

This is the second half of the replay story. :mod:`agentsla.core.replay`
ships *structural* replay (recorded tool-call hash re-validation + the
stored final answer). This module ships *execution* replay: it re-drives
the actual adapter loop with every tool stubbed to return its recorded
result, then checks the re-produced final answer byte-for-byte against
the recorded one.

Scope (stated precisely, so the claim stays true):

  * Execution replay is shipped for traces recorded by the
    :class:`~agentsla.adapters.rawloop.RawLoopAdapter` (``model_id ==
    "echo-1"``). The rawloop model is deterministic by construction, so
    stubbing the tools is sufficient to reproduce the run.
  * Traces recorded from live-model adapters (Claude SDK, LangGraph
    against a real endpoint) are refused with ``exit_code=2`` — a live
    model's messages would also need stubbing, and pretending otherwise
    would fabricate a determinism guarantee the adapter cannot give.

How stubbing works: recorded ``(ToolCall, ToolResult)`` pairs are matched
by ``call_id`` and queued FIFO per tool name. Each stub invocation pops
the next recorded result for that tool. A recorded error result re-raises
(as :class:`ReplayedToolError`) so the adapter's error path is exercised
the same way it was live.

Exit codes (mirrors :mod:`agentsla.core.replay` semantics):

  * ``0`` — re-executed final answer is byte-identical AND every
    re-executed tool call's ``args_hash`` matches the recording.
  * ``1`` — re-execution completed but diverged (answer or hashes).
  * ``2`` — trace not found, or not replayable by execution (live-model
    trace, missing user message). ``note`` says why.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.core.events import ModelMessage, ToolCall, ToolResult
from agentsla.core.trace import TraceReader

#: The only model execution replay can honestly re-drive today.
_DETERMINISTIC_MODEL_IDS = frozenset({"echo-1"})


class ReplayedToolError(Exception):
    """Raised by a stub when the recorded ToolResult was an error.

    Reproduces the adapter's error path. The recorded error string is the
    message; the original exception *type* is not reconstructed (the
    adapter's final answer does not depend on it — only on the fact that
    the tool raised).
    """


@dataclass
class _StubToolRegistry:
    """FIFO of recorded results per tool name, matched by call_id."""

    queues: dict[str, deque[ToolResult]] = field(default_factory=lambda: defaultdict(deque))

    @classmethod
    def from_events(cls, events: list[Any]) -> _StubToolRegistry:
        calls: dict[UUID, ToolCall] = {}
        registry = cls()
        for ev in events:
            if isinstance(ev, ToolCall):
                calls[ev.call_id] = ev
            elif isinstance(ev, ToolResult) and ev.call_id in calls:
                registry.queues[calls[ev.call_id].tool].append(ev)
        return registry

    def make_stub(self, tool: str) -> Any:
        """Build the stub callable registered under ``tool``."""

        def _stub(**_kwargs: Any) -> Any:
            if not self.queues[tool]:
                raise ReplayedToolError(f"no recorded result left for tool {tool!r}")
            recorded = self.queues[tool].popleft()
            if recorded.is_error:
                raise ReplayedToolError(recorded.error or "recorded tool error")
            return recorded.result

        return _stub


@dataclass
class ExecutionReplayReport:
    """Outcome of one execution replay."""

    trace_id: UUID
    byte_identical: bool
    recorded_answer: str
    replayed_answer: str
    recorded_tool_calls: int
    replayed_tool_calls: int
    args_hash_matches: int
    exit_code: int
    note: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "trace_id": str(self.trace_id),
            "byte_identical": self.byte_identical,
            "recorded_answer": self.recorded_answer,
            "replayed_answer": self.replayed_answer,
            "recorded_tool_calls": self.recorded_tool_calls,
            "replayed_tool_calls": self.replayed_tool_calls,
            "args_hash_matches": self.args_hash_matches,
            "exit_code": self.exit_code,
            "note": self.note,
        }


def _not_replayable(trace_id: UUID, note: str) -> ExecutionReplayReport:
    return ExecutionReplayReport(
        trace_id=trace_id,
        byte_identical=False,
        recorded_answer="",
        replayed_answer="",
        recorded_tool_calls=0,
        replayed_tool_calls=0,
        args_hash_matches=0,
        exit_code=2,
        note=note,
    )


def replay_execution(trace_id: UUID | str, db_path: Path) -> ExecutionReplayReport:
    """Re-execute the recorded trace and compare byte-for-byte.

    Reads the trace, rebuilds a fresh :class:`RawLoopAdapter` whose tools
    are stubs serving the recorded results, re-runs it, and reports
    whether the re-produced final answer (and every re-executed tool
    call's ``args_hash``) matches the recording.
    """
    tid = trace_id if isinstance(trace_id, UUID) else UUID(trace_id)

    with TraceReader(db_path) as reader:
        trace = reader.read_trace(tid)
    if trace is None:
        return _not_replayable(tid, "trace not found")
    if trace.model_id not in _DETERMINISTIC_MODEL_IDS:
        return _not_replayable(
            tid,
            f"execution replay requires a deterministic model; trace was recorded with model_id={trace.model_id!r}. Structural replay still applies.",
        )

    task_text = next(
        (ev.content for ev in trace.events if isinstance(ev, ModelMessage) and ev.role == "user"),
        None,
    )
    if task_text is None:
        return _not_replayable(tid, "trace has no user ModelMessage; cannot reconstruct the task input")

    # The recorded final answer lives in the LAST assistant ModelMessage:
    # TraceReader.read_trace reconstructs Trace with final_answer="" (no
    # trace-metadata table in the store), so the event log is the source
    # of truth. Rawloop always writes its final answer as the terminal
    # assistant message, byte-for-byte.
    recorded_answer = next(
        (ev.content for ev in reversed(trace.events) if isinstance(ev, ModelMessage) and ev.role == "assistant"),
        trace.final_answer,
    )

    recorded_calls = [ev for ev in trace.events if isinstance(ev, ToolCall)]
    registry = _StubToolRegistry.from_events(trace.events)
    stub_tools = {tool: registry.make_stub(tool) for tool in registry.queues}

    adapter = RawLoopAdapter(tools=stub_tools, task_text=task_text)
    replayed = adapter.run(trace.task_id, hooks=NoOpHooks())

    replayed_calls = [ev for ev in replayed.trace.events if isinstance(ev, ToolCall)]
    hash_matches = sum(1 for rec, rep in zip(recorded_calls, replayed_calls, strict=False) if rec.tool == rep.tool and rec.args_hash == rep.args_hash)
    identical = replayed.text == recorded_answer and len(replayed_calls) == len(recorded_calls) and hash_matches == len(recorded_calls)
    return ExecutionReplayReport(
        trace_id=tid,
        byte_identical=identical,
        recorded_answer=recorded_answer,
        replayed_answer=replayed.text,
        recorded_tool_calls=len(recorded_calls),
        replayed_tool_calls=len(replayed_calls),
        args_hash_matches=hash_matches,
        exit_code=0 if identical else 1,
    )


__all__ = [
    "ExecutionReplayReport",
    "ReplayedToolError",
    "replay_execution",
]
