"""Base adapter interface.

Phase 1: Define AgentAdapter ABC and RuntimeHooks contract.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional
from pydantic import BaseModel

from agentsla.core.budget import BudgetManager
from agentsla.core.events import ToolCall, ToolResult, Verdict


@dataclass
class GateDecision:
    """Decision from PolicyGate on a tool call."""
    allow: bool
    reason: str
    rewrite_args: Optional[dict] = None


class RuntimeHooks(BaseModel):
    """Hooks invoked at key agent execution points."""

    on_tool_call: Callable[[ToolCall], GateDecision]
    on_tool_result: Callable[[ToolResult], ToolResult]
    on_final_answer: Callable[[str], Verdict]
    budget: BudgetManager


class AgentAdapter(ABC):
    """Adapter for different agent frameworks (Claude SDK, LangGraph, raw loop, etc.)."""

    @abstractmethod
    def run(self, task_id: str, hooks: RuntimeHooks) -> str:
        """Run agent on task with runtime hooks.

        Args:
            task_id: Task identifier
            hooks: RuntimeHooks for policy, budget, verification

        Returns:
            Final answer string
        """
        pass
