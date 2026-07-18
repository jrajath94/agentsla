"""BudgetedHooks — wires the BudgetManager into the runtime hook contract.

Before this module, :class:`~agentsla.core.budget.BudgetManager` existed
but nothing in the adapter path called it — budget enforcement was a
library, not a runtime behavior. ``BudgetedHooks`` closes that gap by
implementing :class:`~agentsla.adapters.base.RuntimeHooks` and composing
with any inner hooks (typically the :class:`~agentsla.policy.PolicyGate`):

    hooks = BudgetedHooks(budget=BudgetManager(BudgetSpec(max_calls=10)),
                          inner=policy_gate)
    adapter.run(task_id, hooks=hooks)

Decision order on ``on_tool_call``:

  1. Inner hooks decide first (policy DENY wins outright — budget must
     never *allow* what policy denied).
  2. If the budget has already breached on a prior event, DENY with the
     breach reason. This is the graceful-degradation contract: a breach
     converts every subsequent tool call into a policy-style DENY, so the
     adapter short-circuits to its degraded answer instead of crashing.
  3. Otherwise ``record_call``; a fresh :class:`BudgetExceededError` is
     caught and converted to DENY (never propagated into the adapter).

``on_tool_result`` records spend (tokens / cost via the optional
``cost_model`` estimator; hermetic runs default to zero) and captures any
breach for step 2 above — a post-execution breach cannot un-run the tool,
so it degrades the *next* call rather than raising mid-loop.

The observable degradation surface:

  * ``breaches`` — every :class:`BudgetExceededError` captured, in order.
  * ``denied_calls`` — count of tool calls denied for budget reasons.
  * ``level(trace_id)`` — current :class:`DegradationLevel` passthrough.
"""

from __future__ import annotations

from collections.abc import Callable

from agentsla.adapters.base import HookDecision, RuntimeHooks
from agentsla.core.budget import BudgetExceededError, BudgetManager, DegradationLevel
from agentsla.core.events import ToolCall, ToolResult, Trace, Verdict

#: Estimator: (call, result) -> (tokens_used, cost_usd). Hermetic default: zero.
CostModel = Callable[[ToolCall, ToolResult], tuple[int, float]]


def _zero_cost(_call: ToolCall, _result: ToolResult) -> tuple[int, float]:
    return (0, 0.0)


class BudgetedHooks:
    """RuntimeHooks impl that enforces a budget around inner hooks."""

    def __init__(
        self,
        budget: BudgetManager,
        *,
        inner: RuntimeHooks | None = None,
        cost_model: CostModel = _zero_cost,
    ) -> None:
        self.budget = budget
        self.inner = inner
        self.cost_model = cost_model
        self.breaches: list[BudgetExceededError] = []
        self.denied_calls: int = 0

    # ----- RuntimeHooks -----

    def on_tool_call(self, call: ToolCall) -> HookDecision:
        if self.inner is not None:
            decision = self.inner.on_tool_call(call)
            if not decision.allow:
                return decision
        else:
            decision = HookDecision(allow=True, reason="budget hooks: no inner policy")

        if self.breaches:
            self.denied_calls += 1
            first = self.breaches[0]
            return HookDecision(
                allow=False,
                reason=f"budget breached earlier ({first.metric} {first.observed:.4f} > {first.ceiling:.4f}); degrading",
                extra={"degradation_level": self.budget.level(str(call.trace_id)).value},
            )

        try:
            self.budget.record_call(str(call.trace_id))
        except BudgetExceededError as exc:
            self.breaches.append(exc)
            self.denied_calls += 1
            return HookDecision(
                allow=False,
                reason=f"budget breach: {exc.metric} {exc.observed:.4f} > {exc.ceiling:.4f}",
                extra={"degradation_level": exc.level.value},
            )
        return decision

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        if self.inner is not None:
            self.inner.on_tool_result(call, result)
        tokens, cost = self.cost_model(call, result)
        try:
            self.budget.record_tool_result(str(call.trace_id), result, tokens_used=tokens, cost_usd=cost)
        except BudgetExceededError as exc:
            # Post-execution breach: the tool already ran; degrade the NEXT
            # call instead of raising into the adapter loop.
            self.breaches.append(exc)

    def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
        if self.inner is not None:
            self.inner.on_final_answer(trace, verdict)

    # ----- observability -----

    def level(self, trace_id: str) -> DegradationLevel:
        return self.budget.level(trace_id)


__all__ = ["BudgetedHooks", "CostModel"]
