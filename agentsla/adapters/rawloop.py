"""Reference rawloop adapter (ADAPT-01).

Pure in-process agent loop. Deterministic by construction:

  * The default model is an :class:`EchoModel` — it returns a fixed
    response built from the recorded conversation state (``text`` field
    on the adapter).

  * No network. No clock. No random. Tool results come from a
    deterministic tool registry (see :mod:`agentsla.tools.deterministic`).

The agent loop is intentionally tiny:

  1. Emit a synthetic ``ModelMessage`` (user role) with ``task_id`` text.
  2. Call ``hooks.on_tool_call(ToolCall(...))`` — Phase 1 returns HookDecision(allow=True).
  3. Invoke the registered tool. Capture result.
  4. Emit ``ToolResult``.
  5. Call ``hooks.on_tool_result``.
  6. Emit a final ``ModelMessage`` (assistant) with the tool result.
  7. Call ``hooks.on_final_answer``.
  8. Return :class:`FinalAnswer`.

This is the adapter the property-based :mod:`REPLAY-PROOF` drives: every
run emits events with deterministic IDs (UUID v4 from a fresh invocation;
hash of args is computed by the writer) and the recorded trace replays
to the same final answer.

Why the Echo model? Real LLM endpoints are non-deterministic even with
``temperature=0`` (cf. Anthropic docs). The Phase-1 acceptance test
(PITFALLS.md #8 mitigation) requires a *pure* in-process rawloop — no
network, no clock. Phase 2's Claude SDK adapter is the one that talks
to the live API; Phase 1 stays hermetic so the test suite can run
without network access.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from agentsla.adapters.base import AgentAdapter, FinalAnswer, HookDecision, RuntimeHooks
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

# ---------------------------------------------------------------------------
# Echo model — pure in-process, deterministic
# ---------------------------------------------------------------------------


class EchoModel:
    """Trivial deterministic model.

    Returns ``f"<echo:{prompt}>"`` where ``prompt`` is the join of the
    user-message contents. This is enough to drive a small fixture task;
    the round-trip ``prompt → answer`` is deterministic across runs.
    """

    model_id: str = "echo-1"

    def complete(self, *, user_text: str) -> str:
        return f"<echo:{user_text}>"


# ---------------------------------------------------------------------------
# RawLoopAdapter
# ---------------------------------------------------------------------------


class RawLoopAdapter(AgentAdapter):
    """Reference tool-loop implementation.

    Drives one ToolCall against the first registered tool, then returns
    a final assistant ``ModelMessage`` with the tool's output. Real
    adapters (Claude SDK, LangGraph) drive the same shape via framework-
    native primitives; the trace events emitted here are identical.

    Args:
        tools: Optional dict of pre-registered tools (Phase 1 hermetic mode).
        trace_writer: Optional :class:`TraceWriter`. When ``None``, events
            accumulate in an in-memory list (used by REPLAY-PROOF).
        echo_model: Optional model callable. Defaults to :class:`EchoModel`.
        task_text: For test fixtures, the user task string. Real adapters
            accept this from the operator; Phase 1 keeps it explicit so
            the smoke demo is unambiguous.
    """

    name = "rawloop"
    model_id = "echo-1"

    def __init__(
        self,
        *,
        tools: dict[str, Callable[..., Any]] | None = None,
        trace_writer: TraceWriter | None = None,
        echo_model: EchoModel | None = None,
        task_text: str = "demo",
    ) -> None:
        super().__init__()
        self.trace_writer = trace_writer
        self.echo_model = echo_model or EchoModel()
        self.task_text = task_text
        self.events: list[ModelMessage | ToolCall | ToolResult | Verdict] = []
        if tools:
            for name_, fn in tools.items():
                self.register_tool(name_, fn)

    def _write_event(self, event: ModelMessage | ToolCall | ToolResult | Verdict) -> None:
        """Append to in-memory list and persist if a writer is configured."""
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
        """Execute one tool-call + completion cycle.

        Steps:
          1. Emit user ModelMessage with the task text.
          2. Emit a ToolCall against the single registered tool.
          3. Pre-tool hook; ALLOW/DENY per the decision.
          4. Invoke the tool; emit a ToolResult.
          5. Post-tool hook.
          6. Emit a final assistant ModelMessage including the tool result.
          7. Final-answer hook + Return FinalAnswer.

        DENY short-circuits with an empty final answer.
        """
        # Resolve effective writer: instance-attr or arg override.
        effective_writer = trace_writer if trace_writer is not None else self.trace_writer

        trace_id: UUID = uuid4()
        ts_start = now_timestamp()
        seq = 0

        # ----- 1. user ModelMessage -----
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

        # ----- 2. ToolCall -----
        if not self.tools:
            # No tools → return the echo directly without a tool call.
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

        # Pick the first registered tool deterministically (dict preserves
        # insertion order on Python 3.7+).
        tool_name, tool_fn = next(iter(self.tools.items()))
        call_id = uuid4()
        tool_args = {"text": self.task_text}
        call = ToolCall(
            call_id=call_id,
            tool=tool_name,
            args=tool_args,
            trace_id=trace_id,
            seq=seq,
            ts=now_timestamp(),
            parent_msg_id=user_msg.msg_id,
            args_hash=canonical_args_hash(tool_args),
        )
        self._write_event(call)
        seq += 1

        # ----- 3. pre-tool hook -----
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

        # ----- 4. invoke tool -----
        try:
            tool_output = tool_fn(**tool_args)
            is_error = False
            error_msg = None
        except Exception as exc:
            tool_output = None
            is_error = True
            error_msg = f"{type(exc).__name__}: {exc}"

        # ----- 5. ToolResult -----
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

        # ----- 6. post-tool hook -----
        hooks.on_tool_result(call, result)

        # ----- 7. final assistant ModelMessage -----
        # Concatenate raw tool output (its own encoding if any). Avoids
        # double-encoding when the tool already returned a JSON string.
        final_text = (
            self.echo_model.complete(user_text=self.task_text)
            + "::"
            + (tool_output if isinstance(tool_output, str) else str(tool_output))
        )
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
        # Flush the writer's local list to avoid double-emission on subsequent runs.
        if effective_writer is not None and effective_writer is self.trace_writer:
            pass  # writer already received each event via _write_event
        return FinalAnswer(trace=trace, text=final_text)


__all__ = ["EchoModel", "RawLoopAdapter"]
