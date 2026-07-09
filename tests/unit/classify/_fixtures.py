"""Shared trace builder fixtures for the classify test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from agentsla.core.events import (
    ModelMessage,
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
)


def make_tool_call(
    trace_id: UUID,
    seq: int,
    *,
    tool: str = "fetch",
    args: dict[str, Any] | None = None,
    call_id: UUID | None = None,
) -> ToolCall:
    return ToolCall(
        call_id=call_id or uuid4(),
        tool=tool,
        args=args or {"q": "x"},
        trace_id=trace_id,
        seq=seq,
        args_hash="0" * 64,
    )


def make_tool_result(
    trace_id: UUID,
    seq: int,
    *,
    call_id: UUID,
    tool: str = "fetch",
    error: str | None = None,
    result: Any = None,
) -> ToolResult:
    return ToolResult(
        call_id=call_id,
        tool=tool,
        result=result if error is None else None,
        is_error=error is not None,
        error=error,
        trace_id=trace_id,
        seq=seq,
    )


def make_model_message(
    trace_id: UUID,
    seq: int,
    *,
    role: str = "assistant",
    content: str = "ok",
    model_id: str = "claude-haiku-4-5-20251001",
    response_id: str = "msg_test",
) -> ModelMessage:
    return ModelMessage(
        msg_id=uuid4(),
        trace_id=trace_id,
        seq=seq,
        role=role,  # type: ignore[arg-type]
        content=content,
        model_id=model_id,
        response_id=response_id,
    )


def make_verdict(
    trace_id: UUID,
    seq: int,
    *,
    verified: bool = True,
    coverage: float = 1.0,
) -> Verdict:
    return Verdict(
        verdict_id=uuid4(),
        trace_id=trace_id,
        seq=seq,
        verifier="numeric",
        verified=verified,
        coverage=coverage,
    )


def make_trace(
    *,
    events: list[Any] | None = None,
    final_answer: str = "",
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
    trace_id: UUID | None = None,
    task_id: str = "demo",
    model_id: str = "claude-haiku-4-5-20251001",
) -> Trace:
    tid = trace_id or uuid4()
    if events is None:
        events = []
    if not events:
        first_ts = start_ts or datetime.now(UTC)
        last_ts = end_ts or first_ts
    else:
        first_ts = start_ts or events[0].ts
        last_ts = end_ts or events[-1].ts
    return Trace(
        trace_id=tid,
        task_id=task_id,
        model_id=model_id,
        events=events,
        final_answer=final_answer,
        start_ts=first_ts,
        end_ts=last_ts,
    )
