"""Concrete no-op RuntimeHooks used as a Phase-1 default.

Phase 2's :class:`PolicyGate` slots in here for real decisions. The
no-op hooks always ALLOW and never rewrite; they exist so :class:`RawLoopAdapter`
can be exercised without depending on the policy module (kept out of core
intentionally — Phase 2 concern).
"""

from __future__ import annotations

from agentsla.adapters.base import HookDecision
from agentsla.core.events import ToolCall, ToolResult, Trace, Verdict


class NoOpHooks:
    """Default RuntimeHooks impl: ALLOW everything, no rewrite, no side effects."""

    def on_tool_call(self, call: ToolCall) -> HookDecision:
        return HookDecision(allow=True, reason="phase-1 noop: allow")

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        return None

    def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
        return None


__all__ = ["NoOpHooks"]
