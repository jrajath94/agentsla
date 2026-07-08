"""Adapter base interface — Protocol + ABC.

Two contracts:

  * :class:`RuntimeHooks` (Protocol) — the three observation + decision
    points an adapter must invoke while running an agent:
        on_tool_call(call) -> HookDecision (Phase 2's PolicyGate slots in here).
        on_tool_result(call, result) -> None (Phase 4's classifier + budget hook).
        on_final_answer(trace, verdict) -> None (Phase 3's verifier + Phase 4's
                                          classifier attach here).

  * :class:`AgentAdapter` (ABC) — adapter authors implement ``run(task_id,
    hooks) -> FinalAnswer``. The adapter decides how to:
        - Acquire a model (rawloop embeds an ``EchoModel``; Claude Agent SDK
          would call into the SDK).
        - Drive the tool loop (rawloop = explicit Python loop; LangGraph = a
          StateGraph).
        - Emit :class:`ToolCall` / :class:`ToolResult` /
          :class:`ModelMessage` events into a :class:`TraceWriter` for the
          replay engine to consume.

Phase 1 only needs Phase-1's minimal :class:`HookDecision` (always-ALLOW); the
Phase 2 :class:`PolicyGate` returns richer decisions (ALLOW / DENY / REWRITE).
The base here is intentionally narrow so Phase 2+ can extend without breaking
:class:`AgentAdapter` implementers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentsla.core.events import (
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
)

# ---------------------------------------------------------------------------
# Hook decision (Phase 1 stub — Phase 2's PolicyGate replaces this)
# ---------------------------------------------------------------------------


@dataclass
class HookDecision:
    """Result of a hook callback.

    Phase 1: always ``allow=True``, no rewrite. Phase 2 PolicyGate extends
    this with REWRITE semantics + ``args_hash`` (PITFALL #3 TOCTOU mitigation).
    """

    allow: bool = True
    reason: str = "phase-1 default: allow"
    rewrite_args: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RuntimeHooks — Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class RuntimeHooks(Protocol):
    """The three observable hook points.

    Implementers are free to define a richer concrete class; the Protocol
    shape just covers what :class:`AgentAdapter` calls.

    Phase 1's concrete impl lives in :mod:`agentsla.adapters.noop_hooks`
    (always-allow defaults). Phase 2 introduces :class:`PolicyGate`, which
    satisfies this Protocol with the real decisions.
    """

    def on_tool_call(self, call: ToolCall) -> HookDecision: ...

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None: ...

    def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None: ...


# ---------------------------------------------------------------------------
# FinalAnswer — the value the adapter returns to the operator
# ---------------------------------------------------------------------------


@dataclass
class FinalAnswer:
    """The terminal output of one adapter run.

    Carries the trace (always) and the optional verdict (Phase 3+ populates
    it). The adapter is responsible for writing the :class:`Trace` to the
    store; this object surfaces the in-memory snapshot to callers for
    inspection.
    """

    trace: Trace
    verdict: Verdict | None = None
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            # Default the text to the trace's recorded final answer.
            self.text = self.trace.final_answer


# ---------------------------------------------------------------------------
# AgentAdapter — ABC
# ---------------------------------------------------------------------------


class AgentAdapter(ABC):
    """Base class every concrete adapter inherits from.

    Subclasses must:
      * set the class attribute ``name`` (e.g. ``"rawloop"``, ``"claude-sdk"``).
      * register their model identifier via :attr:`model_id` (mandatory;
        defends PITFALL #1 across adapters).
      * implement :meth:`run`.

    Tools are registered per-instance via :meth:`register_tool`. The adapter
    keeps a dict ``tool_name -> callable``. Phase 1 rawloop is hermetic —
    it uses deterministic-fixture tools (``FetchTool``, ``ClockTool``) so
    replays are byte-identical without depending on the live LLM API.
    """

    #: Human-readable adapter name (used by metrics + bench harness).
    name: str = "abstract"

    #: Model identifier (e.g. ``"claude-haiku-4-5-20251001"``).
    model_id: str = "unknown"

    def __init__(self) -> None:
        # ``str -> callable`` mapping. Keys are tool names; values are
        # zero-arg Python callables (rawloop convention). Phase 2 may extend
        # the signature to accept kwargs.
        self.tools: dict[str, Callable[..., Any]] = {}

    # ----- registration -----

    def register_tool(self, name: str, fn: Callable[..., Any]) -> None:
        """Register a deterministic tool under ``name``.

        Phase 1 enforces this binding at register-time; an attempt to
        re-register is rejected. Phase 2's policy gate can override this at
        run-time via REWRITE.
        """
        if name in self.tools:
            raise ValueError(f"tool {name!r} already registered")
        self.tools[name] = fn

    def has_tool(self, name: str) -> bool:
        return name in self.tools

    # ----- execution -----

    @abstractmethod
    def run(
        self,
        task_id: str,
        *,
        hooks: RuntimeHooks,
        trace_writer: Any | None = None,
    ) -> FinalAnswer:
        """Execute the agent loop for ``task_id``.

        Args:
            task_id: Stable identifier; recorded on every ToolCall as a
                denormalized column for fast filtering.
            hooks: Hook callbacks invoked at the three points.
            trace_writer: Optional :class:`TraceWriter` for persistence.
                When ``None``, the adapter holds events in-memory only
                (useful for the property-based stateful replay test).

        Returns:
            :class:`FinalAnswer` carrying the trace + (when available) verdict.

        Implementations MUST invoke ``hooks.on_tool_call`` before executing
        any tool and ``hooks.on_tool_result`` after. They MUST invoke
        ``hooks.on_final_answer`` exactly once on success.
        """
        raise NotImplementedError


__all__ = [
    "AgentAdapter",
    "FinalAnswer",
    "HookDecision",
    "RuntimeHooks",
]
