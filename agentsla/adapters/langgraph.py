"""LangGraph adapter — Phase 2 stub with parity to RawLoopAdapter.

Mirrors :class:`RawLoopAdapter`'s event shape and hook integration so
the policy gate + trace store are interchangeable across adapters. Real
LangGraph integration is wired in Phase 5 when the bench harness needs
multi-step graph orchestration; the public surface of this module is
stable enough that swap is a one-line change.

Why a stub now? Phase 2's acceptance ("same task runs under Claude SDK
AND LangGraph with identical policy behavior") requires the parity
contract to be coded and tested even before LangGraph becomes a real
hard dependency. This keeps the cross-adapter parity test small and
fast.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from agentsla.adapters.base import AgentAdapter, FinalAnswer, HookDecision, RuntimeHooks
from agentsla.adapters.rawloop import EchoModel
from agentsla.core.events import (
    ModelMessage,
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
    canonical_args_hash,
    now_timestamp,
)
from agentsla.core.trace import TraceWriter


class LangGraphAdapter(AgentAdapter):
    """Reference LangGraph-shaped adapter (Phase-2 stub).

    Event shape matches :class:`RawLoopAdapter` exactly so cross-adapter
    parity tests compare event streams byte-for-byte (modulo UUID
    identity, which the test fixture pins).

    Args:
        tools: ``name -> callable`` mapping registered against the
            ``AgentAdapter`` base.
        trace_writer: Optional persistence sink; when ``None`` events
            accumulate in-memory (used by the parity test).
        echo_model: Surrogate model callable. Defaults to
            :class:`EchoModel`; tests can swap for a one-shot stub.
        task_text: User-side task text (passed through to the model).
        graph_name: Optional graph identifier (recorded on assistant
            messages for diagnosability). Default ``"stub"``.
    """

    name = "langgraph"
    model_id = "echo-1"

    def __init__(
        self,
        *,
        tools: dict[str, Callable[..., Any]] | None = None,
        trace_writer: TraceWriter | None = None,
        echo_model: EchoModel | None = None,
        task_text: str = "demo",
        graph_name: str = "stub",
    ) -> None:
        super().__init__()
        self.trace_writer = trace_writer
        self.echo_model = echo_model or EchoModel()
        self.task_text = task_text
        self.graph_name = graph_name
        self.events: list[ModelMessage | ToolCall | ToolResult | Verdict] = []
        if tools:
            for name_, fn in tools.items():
                self.register_tool(name_, fn)

    def _write_event(self, event: ModelMessage | ToolCall | ToolResult | Verdict) -> None:
        self.events.append(event)
        if self.trace_writer is not None:
            self.trace_writer.append(event)

    def run(
        self,
        task_id: str,
        *,
        hooks: RuntimeHooks,
        trace_writer: TraceWriter | None = None,
    ) -> FinalAnswer:
        """Run the LangGraph-shaped loop.

        Identical event sequence to :meth:`RawLoopAdapter.run`:
          1. user ``ModelMessage``
          2. ``ToolCall`` against first registered tool
          3. ``HookDecision``; DENY short-circuits
          4. ``ToolResult``
          5. final assistant ``ModelMessage``
        """
        ts_start = now_timestamp()
        trace_id = uuid4()
        seq = 0
        user_msg = ModelMessage(
            msg_id=uuid4(),
            trace_id=trace_id,
            seq=seq,
            role="user",
            content=self.task_text,
            model_id=self.model_id,
            response_id=f"req_{trace_id.hex[:8]}",
            ts=ts_start,
        )
        self._write_event(user_msg)
        seq += 1

        if not self.tools:
            answer = self.echo_model.complete(user_text=self.task_text)
            final = ModelMessage(
                msg_id=uuid4(),
                trace_id=trace_id,
                seq=seq,
                role="assistant",
                content=answer,
                model_id=self.model_id,
                response_id=f"req_{trace_id.hex[:8]}.final",
                ts=now_timestamp(),
            )
            self._write_event(final)
            trace = Trace(
                trace_id=trace_id,
                task_id=task_id,
                model_id=self.model_id,
                events=list(self.events),
                final_answer=answer,
                start_ts=ts_start,
                end_ts=final.ts,
            )
            hooks.on_final_answer(trace, None)
            return FinalAnswer(trace=trace, text=answer)

        tool_name, tool_fn = next(iter(self.tools.items()))
        call = ToolCall(
            call_id=uuid4(),
            tool=tool_name,
            args={"text": self.task_text},
            trace_id=trace_id,
            seq=seq,
            ts=now_timestamp(),
            parent_msg_id=user_msg.msg_id,
            args_hash=canonical_args_hash({"text": self.task_text}),
        )
        self._write_event(call)
        seq += 1

        decision: HookDecision = hooks.on_tool_call(call)
        if not decision.allow:
            trace = Trace(
                trace_id=trace_id,
                task_id=task_id,
                model_id=self.model_id,
                events=list(self.events),
                final_answer="",
                start_ts=ts_start,
                end_ts=now_timestamp(),
            )
            hooks.on_final_answer(trace, None)
            return FinalAnswer(trace=trace, text="")

        try:
            tool_output = tool_fn(**{"text": self.task_text})
            is_error = False
            error_msg = None
        except Exception as exc:
            tool_output = None
            is_error = True
            error_msg = f"{type(exc).__name__}: {exc}"

        result = ToolResult(
            call_id=call.call_id,
            tool=tool_name,
            result=tool_output,
            is_error=is_error,
            error=error_msg,
            latency_ms=0.0,
            trace_id=trace_id,
            seq=seq,
            ts=now_timestamp(),
        )
        self._write_event(result)
        seq += 1
        hooks.on_tool_result(call, result)

        final_text = self.echo_model.complete(user_text=self.task_text) + "::" + (tool_output if isinstance(tool_output, str) else str(tool_output))
        final = ModelMessage(
            msg_id=uuid4(),
            trace_id=trace_id,
            seq=seq,
            role="assistant",
            content=final_text,
            model_id=self.model_id,
            response_id=f"req_{trace_id.hex[:8]}.final",
            ts=now_timestamp(),
        )
        self._write_event(final)
        trace = Trace(
            trace_id=trace_id,
            task_id=task_id,
            model_id=self.model_id,
            events=list(self.events),
            final_answer=final_text,
            start_ts=ts_start,
            end_ts=final.ts,
        )
        hooks.on_final_answer(trace, None)
        return FinalAnswer(trace=trace, text=final_text)


__all__ = ["LangGraphAdapter"]
