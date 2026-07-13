"""Claude Agent SDK adapter (ADAPT-02 — ClaudeSdkAdapter).

Wraps the Claude Agent SDK (``claude_agent_sdk``) as one of three runtime
adapters (rawloop + langgraph + claude_sdk). The SDK exposes a
``.query(prompt, **kw) -> Iterator[Message]`` API where each yielded
message carries a ``kind`` discriminator (``text`` for assistant prose,
``tool_use`` for a proposed invocation). The adapter translates those
events into the same append-only event sequence as :class:`RawLoopAdapter`
so the policy gate, trace store, verifier, and classifier remain
adapter-agnostic.

Design rules:

  * **No SDK import.** The SDK is *injected* via a ``client`` parameter so
    this module has zero runtime dependency on ``claude_agent_sdk``. Tests
    pass a fake; production users pass ``claude_agent_sdk.ClaudeSDKClient``
    (or whatever the upstream SDK exposes). The only contract is the duck-
    typed ``query(prompt, **kw) -> Iterator[Any]`` method.
  * **Parity event sequence.** Same 4-event shape as rawloop for an echo
    task: ``model_message`` (user) -> ``tool_call`` -> ``tool_result`` ->
    ``model_message`` (assistant). The cross-adapter parity test enforces
    this byte-for-byte (modulo UUIDs).
  * **Hooks-first.** Every tool call is gated through
    ``hooks.on_tool_call`` before the registered tool fn runs; a DENY
    decision short-circuits the loop, leaving ``on_final_answer`` to be
    invoked once with an empty final answer.
  * **Tool errors are surfaced, not swallowed.** A tool that raises turns
    into ``ToolResult(is_error=True, error=...)`` and the loop continues
    to the final-answer hook.

The default ``model_id`` is ``"claude-haiku-4-5-20251001"`` (cheapest
production model; spec'd in TRD-v1 § 1.4 + PRD-v1 § 2.1 F3). Override
via the ``model_id`` kwarg when wiring a different Claude generation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, cast
from uuid import uuid4

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
# Constants
# ---------------------------------------------------------------------------

#: Default Claude model identifier (PRD-v1 § 2.1 F3 — cheapest production model).
DEFAULT_MODEL_ID = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# ClaudeSdkAdapter
# ---------------------------------------------------------------------------


class ClaudeSdkAdapter(AgentAdapter):
    """Adapter that drives the Claude Agent SDK through AgentSLA's hook surface.

    Args:
        client: Duck-typed SDK client. Must expose
            ``query(prompt, **kw) -> Iterable[Any]``. Production users pass
            ``claude_agent_sdk.ClaudeSDKClient``; tests pass a fake.
        tools: Optional dict of pre-registered tools (mirrors rawloop API).
        trace_writer: Optional :class:`TraceWriter` for persistence; when
            ``None`` events accumulate in-memory (used by parity tests).
        task_text: User-side task text. Passed into the SDK as the prompt
            argument; the SDK is responsible for any framing.
        model_id: Identifier of the Claude model being driven. Default
            :data:`DEFAULT_MODEL_ID` (``claude-haiku-4-5-20251001``).
        max_messages: Safety cap on the number of SDK messages consumed
            per run. Defaults to ``16``; protects against runaway agents
            in long-running test environments.

    Event sequence (parity with :class:`RawLoopAdapter`):

        1. ``ModelMessage`` (role=user) carrying ``task_text``.
        2. ``ToolCall`` for the first ``tool_use`` message emitted by the SDK.
        3. ``ToolResult`` carrying the tool's output (or error).
        4. ``ModelMessage`` (role=assistant) carrying the SDK's final text.
        5. ``hooks.on_final_answer`` once. Return :class:`FinalAnswer`.

    On policy DENY: only events 1-2 are emitted; on_final_answer is still
    invoked once with an empty final answer (matches rawloop's short-
    circuit contract).
    """

    name = "claude-sdk"
    model_id = DEFAULT_MODEL_ID

    def __init__(
        self,
        *,
        client: Any,
        tools: dict[str, Callable[..., Any]] | None = None,
        trace_writer: TraceWriter | None = None,
        task_text: str = "demo",
        model_id: str = DEFAULT_MODEL_ID,
        max_messages: int = 16,
    ) -> None:
        super().__init__()
        self.client = client
        self.trace_writer = trace_writer
        self.task_text = task_text
        self.model_id = model_id
        self.max_messages = max_messages
        self.events: list[ModelMessage | ToolCall | ToolResult | Verdict] = []
        if tools:
            for name_, fn in tools.items():
                self.register_tool(name_, fn)

    # ----- internals -----

    def _write_event(self, event: ModelMessage | ToolCall | ToolResult | Verdict) -> None:
        """Append to in-memory list and persist if a writer is configured."""
        self.events.append(event)
        if self.trace_writer is not None:
            self.trace_writer.append(event)

    @staticmethod
    def _iter_sdk_messages(client: Any, prompt: str, **kw: Any) -> Iterable[Any]:
        """Wrap ``client.query`` to tolerate either a list or an iterator."""
        result = client.query(prompt, **kw)
        # ``result`` is expected to be an iterator (duck-typed). Tests pass
        # either an ``iter([...])`` (already an iterator) or a list. Make
        # sure we always return an iterable that can be exhausted once.
        if isinstance(result, list):
            return iter(result)
        # ``result`` is itself iterable; cast through Iterable[Any] for the
        # type-checker without runtime conversion.
        return cast(Iterable[Any], result)

    def _emit_user_message(self, trace_id: Any) -> ModelMessage:
        """Step 1: emit the user-side ModelMessage."""
        msg = ModelMessage(
            msg_id=uuid4(),
            trace_id=trace_id,
            seq=0,
            role="user",
            content=self.task_text,
            model_id=self.model_id,
            response_id=f"req_{trace_id.hex[:8]}",
            ts=now_timestamp(),
        )
        self._write_event(msg)
        return msg

    def _emit_final_message(
        self,
        trace_id: Any,
        seq: int,
        content: str,
    ) -> ModelMessage:
        """Emit the final assistant ModelMessage carrying the SDK's terminal text."""
        msg = ModelMessage(
            msg_id=uuid4(),
            trace_id=trace_id,
            seq=seq,
            role="assistant",
            content=content,
            model_id=self.model_id,
            response_id=f"req_{trace_id.hex[:8]}.final",
            ts=now_timestamp(),
        )
        self._write_event(msg)
        return msg

    def _resolve_final_text(self, tool_output: Any, tool_name: str | None) -> str:
        """Build the assistant's final answer text from the tool output.

        Mirrors rawloop's contract: the final assistant message contains the
        tool output verbatim (when a tool was called) or just the prompt
        echo when no tool was invoked. We do NOT double-encode strings the
        tool already serialized (e.g. ``JsonEchoTool`` returns JSON text).
        """
        if tool_output is None:
            return f"<echo:{self.task_text}>"
        if tool_name is None:
            return f"<echo:{self.task_text}>"
        if isinstance(tool_output, str):
            return f"<echo:{self.task_text}>::{tool_output}"
        return f"<echo:{self.task_text}>::{tool_output!s}"

    # ----- main entry point -----

    def run(
        self,
        task_id: str,
        *,
        hooks: RuntimeHooks,
        trace_writer: TraceWriter | None = None,
    ) -> FinalAnswer:
        """Drive the Claude Agent SDK loop until a terminal text message.

        Sequence:
          1. Emit user ``ModelMessage``.
          2. ``client.query(prompt, tools=list(self.tools))`` and iterate.
          3. On first ``tool_use`` message: build ``ToolCall``, gate it
             through ``hooks.on_tool_call``. If DENY, short-circuit.
          4. Invoke the registered tool. Emit ``ToolResult``.
          5. Capture the next ``text`` message as the final answer.
          6. Emit final ``ModelMessage`` + invoke ``hooks.on_final_answer``.
        """
        ts_start = now_timestamp()
        trace_id = uuid4()
        seq = 0

        # ----- 1. user ModelMessage -----
        user_msg = self._emit_user_message(trace_id)
        seq += 1

        # ----- 2. SDK turn -----
        tool_name_used: str | None = None
        tool_output: Any = None
        final_text: str = f"<echo:{self.task_text}>"

        # Pass tool names to the SDK via the ``tools`` kwarg. The SDK is
        # responsible for any prompt-framing (system prompt, etc.). Tests
        # assert this kwarg appears in ``client.calls``.
        sdk_kwargs: dict[str, Any] = {}
        if self.tools:
            sdk_kwargs["tools"] = list(self.tools.keys())

        messages = self._iter_sdk_messages(self.client, self.task_text, **sdk_kwargs)
        sdk_message_count = 0

        for message in messages:
            sdk_message_count += 1
            if sdk_message_count > self.max_messages:
                # Safety: stop consuming runaway SDK streams.
                break

            kind = getattr(message, "kind", None)

            if kind == "tool_use":
                # Resolve the tool name + args from the SDK message.
                tool_name = getattr(message, "name", None)
                if tool_name is None or tool_name not in self.tools:
                    # Unknown tool — skip (no registered handler). Equivalent
                    # to rawloop's no-tools path.
                    continue

                tool_args = dict(getattr(message, "input", {}) or {})
                call_id = uuid4()
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
                    # Short-circuit: emit final answer with empty text,
                    # then return. Same contract as rawloop.
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
                    tool_output = self.tools[tool_name](**tool_args)
                    is_error = False
                    error_msg: str | None = None
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

                # Post-tool hook.
                hooks.on_tool_result(call, result)

                # Don't break — keep consuming messages so we can find the
                # terminal ``text`` message (matches how real Claude SDK
                # streams tool_use then text in one turn).
                continue

            if kind == "text":
                text_value = getattr(message, "text", "") or ""
                # SDK text is the source of truth for the final answer.
                # When a tool was called, the SDK typically composes the
                # answer itself (echo + tool output). When no tool was
                # called, the SDK text is the terminal response.
                final_text = text_value
                # First text message ends the SDK turn — stop iterating.
                break

            # Unknown kind: skip (forward-compat).
            continue

        # ----- 5. final assistant ModelMessage -----
        # Default: if SDK produced no text and no tool was called, fall
        # back to the echo for hermetic parity with rawloop. When a tool
        # was called, the SDK is expected to have provided text.
        if not final_text and tool_name_used is None:
            final_text = f"<echo:{self.task_text}>"

        final_msg = self._emit_final_message(trace_id, seq, final_text)
        trace = Trace(
            trace_id=trace_id,
            task_id=task_id,
            model_id=self.model_id,
            events=list(self.events),
            final_answer=final_text,
            start_ts=ts_start,
            end_ts=final_msg.ts,
        )
        # ----- 6. final-answer hook -----
        hooks.on_final_answer(trace, None)
        return FinalAnswer(trace=trace, text=final_text)


__all__ = ["DEFAULT_MODEL_ID", "ClaudeSdkAdapter"]
