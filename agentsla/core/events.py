"""Event schema: ToolCall, ToolResult, ModelMessage, Verdict.

Phase 1: Define these pydantic models as contracts for trace serialization.
"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """Single tool invocation in an agent trace."""
    call_id: str
    tool: str
    args: dict[str, Any]
    ts: datetime
    parent_msg_id: str


class ToolResult(BaseModel):
    """Result of a tool call."""
    call_id: str
    result: Any
    ts: datetime
    error: Optional[str] = None


class ModelMessage(BaseModel):
    """LLM model message (input or output)."""
    msg_id: str
    role: str  # "user", "assistant", "system"
    content: str
    ts: datetime


class Verdict(BaseModel):
    """Verification verdict for final answer."""
    verified: bool
    verifier: str  # e.g. "numeric", "grounding", "schema_conform"
    detail: str
    corrected_answer: Optional[str] = None
    coverage: float = Field(ge=0.0, le=1.0)  # Fraction of claims checked


class Trace(BaseModel):
    """Complete trace of a single agent run."""
    trace_id: str
    task_id: str
    messages: list[ModelMessage]
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    final_answer: str
    verdict: Optional[Verdict] = None
    start_ts: datetime
    end_ts: datetime
