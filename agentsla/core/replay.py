"""Deterministic replay engine.

Phase 1: Stub recorded tool results, allow deterministic re-runs.
"""

from dataclasses import dataclass
from typing import Literal
from .trace import TraceReader


@dataclass
class ReplayReport:
    """Results of a replay run."""
    trace_id: str
    mode: str
    passed: bool
    divergence: str = ""
    final_answer: str = ""


def replay(trace_id: str, mode: Literal["strict", "tolerant"]) -> ReplayReport:
    """Replay a recorded trace.

    Args:
        trace_id: ID of trace to replay
        mode: "strict" = any tool arg drift fails; "tolerant" = stub results regardless

    Returns:
        ReplayReport with pass/fail and any divergences
    """
    # Implementation in Phase 1
    pass
