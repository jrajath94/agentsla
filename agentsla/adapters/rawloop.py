"""Reference tool-loop implementation.

Phase 1: Implement a simple agent loop that demonstrates trace recording and hook invocation.
"""

from .base import AgentAdapter, RuntimeHooks
from agentsla.core.trace import TraceWriter
from agentsla.core.events import Trace, ToolCall, ModelMessage
from datetime import datetime


class RawLoopAdapter(AgentAdapter):
    """Minimal agent loop for testing and reference.

    Demonstrates:
    - Hook invocation points
    - Trace recording
    - Deterministic behavior for replay
    """

    def __init__(self, trace_writer: TraceWriter):
        """Initialize.

        Args:
            trace_writer: TraceWriter instance for recording execution
        """
        self.trace_writer = trace_writer

    def run(self, task_id: str, hooks: RuntimeHooks) -> str:
        """Execute agent loop.

        Minimal implementation: system prompt → user message → tool call loop → final answer.
        """
        # Phase 1 implementation: stubbed
        # Will implement actual loop in Phase 1 work order
        return ""
