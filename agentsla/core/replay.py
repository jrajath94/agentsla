"""Structural replay engine (TRACE-04, TRACE-05).

This module ships a *structural* replay primitive, not an adapter-driven
one. It validates that a recorded trace is self-consistent and returns
the trace's stored final answer; it does not re-drive the adapter loop
with stubbed tool outputs.

Two modes:

  * ``strict``  — every recorded ``ToolCall`` must hash-equal the value
                 recomputed from its recorded ``args``. Drift raises
                 :class:`ToolCallDriftError` and the report's
                 ``exit_code`` is ``1``. Use case: regression test that
                 the recorded log is replay-safe across reads.

  * ``tolerant`` — drift is recorded in the report but the run does not
                 raise. Use case: triage of a drifted log; the diff list
                 is the artifact, not a hard failure.

For every ``ToolCall`` event in the trace, the engine re-derives
``args_hash`` from ``args`` (canonical JSON, ``sort_keys=True``) and
compares it against the recorded ``args_hash``. Matches and drift are
collected; the report's ``final_answer`` is the stored final answer
byte-for-byte (always equal across replays by construction —
adapter-driven re-execution with stubbed tool results is not shipped).

Honesty constraint: this engine does *not* rerun the agent. A reviewer
who wants adapter-driven replay must either install the replay hook
themselves or treat this report as a hash-validation audit, not as
re-execution evidence.

Public surface (5 classes + 1 function):

  ToolCallDriftError   — raised in strict mode when args_hash mismatch.
  DriftDetail          — one per drift.
  ReplayReport         — aggregate.
  replay(trace_id, db_path, *, mode)  — function form.
  ReplayEngine         — class form (used by the CLI).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agentsla.core.events import (
    ToolCall,
    ToolResult,
    canonical_args_hash,
)
from agentsla.core.trace import TraceReader


class ReplayMode(str, Enum):
    """Strict or tolerant replay mode."""

    STRICT = "strict"
    TOLERANT = "tolerant"


class ToolCallDriftError(Exception):
    """Raised in strict mode when a recorded tool call's args drift on replay.

    The exception carries the drift details so callers can decide whether
    to fail loud (default) or log + continue. Strict mode uses this to
    signal ``exit_code=1`` in the :class:`ReplayReport`.
    """

    def __init__(self, trace_id: UUID, drifts: list[DriftDetail]) -> None:
        self.trace_id = trace_id
        self.drifts = drifts
        super().__init__(f"trace {trace_id} has {len(drifts)} tool-call drift(s); see .drifts for the per-call detail.")


class DriftDetail(BaseModel):
    """Per-call description of a tool-call hash mismatch."""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0, description="Per-trace sequence number.")
    tool: str = Field(description="Tool name.")
    expected_args_hash: str = Field(description="Hash recorded at write-time.")
    actual_args_hash: str = Field(description="Hash recomputed from the recorded args.")
    recorded_args: dict[str, object] = Field(
        default_factory=dict,
        description="The recorded args (the source of `actual_args_hash`).",
    )


class ReplayReport(BaseModel):
    """Aggregate outcome of one replay run.

    ``exit_code`` follows POSIX semantics:
      * ``0`` — replay succeeded (no drift, OR tolerant mode ignored drift).
      * ``1`` — strict-mode drift detected.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: UUID = Field(description="Owning trace's UUID.")
    mode: Literal["strict", "tolerant"] = Field(description="Replay mode that was used.")
    match_count: int = Field(ge=0, description="ToolCalls whose hash matched.")
    drift_count: int = Field(ge=0, description="ToolCalls whose hash did NOT match.")
    exit_code: int = Field(
        ge=0,
        le=1,
        description="0 on pass; 1 on strict-mode drift. Tolerant always reports 0.",
    )
    final_answer: str = Field(description="The trace's stored final answer (Plan 01.5+ replaces this).")
    drift_details: list[DriftDetail] = Field(
        default_factory=list,
        description="Per-drift detail; empty when ``drift_count == 0``.",
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ReplayEngine:
    """Replay engine instance; one per replay run.

    Wraps a :class:`TraceReader` so callers reuse a connection if needed.
    Use the module-level :func:`replay` for the common case (one trace).
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def replay(
        self,
        trace_id: UUID,
        *,
        mode: ReplayMode | Literal["strict", "tolerant"] = ReplayMode.STRICT,
    ) -> ReplayReport:
        """Replay ``trace_id`` and return the report.

        In strict mode, a drift raises :class:`ToolCallDriftError` AND
        returns the failing report (so CLI tooling can emit `exit_code=1`
        without unwinding the exception). In tolerant mode, drifts are
        silently recorded.
        """
        normalized_mode: Literal["strict", "tolerant"] = mode.value if isinstance(mode, ReplayMode) else mode

        with TraceReader(self.db_path) as reader:
            trace = reader.read_trace(trace_id)
            if trace is None:
                # Unknown trace = empty report with exit_code=1 (cannot replay
                # what doesn't exist). Distinct from strict drift.
                return ReplayReport(
                    trace_id=trace_id,
                    mode=normalized_mode,
                    match_count=0,
                    drift_count=0,
                    exit_code=1,
                    final_answer="",
                    drift_details=[],
                )

            match = 0
            drifts: list[DriftDetail] = []
            for ev in trace.events:
                if isinstance(ev, ToolCall):
                    recomputed = canonical_args_hash(ev.args)
                    if recomputed == ev.args_hash:
                        match += 1
                    else:
                        drifts.append(
                            DriftDetail(
                                seq=ev.seq,
                                tool=ev.tool,
                                expected_args_hash=ev.args_hash,
                                actual_args_hash=recomputed,
                                recorded_args=dict(ev.args),
                            )
                        )

        drift_count = len(drifts)
        strict_failed = normalized_mode == "strict" and drift_count > 0
        report = ReplayReport(
            trace_id=trace_id,
            mode=normalized_mode,
            match_count=match,
            drift_count=drift_count,
            exit_code=1 if strict_failed else 0,
            final_answer=trace.final_answer,
            drift_details=drifts,
        )
        if strict_failed:
            raise ToolCallDriftError(trace_id, drifts)
        return report


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def replay(
    trace_id: UUID,
    db_path: Path,
    *,
    mode: ReplayMode | Literal["strict", "tolerant"] = ReplayMode.STRICT,
) -> ReplayReport:
    """Replay ``trace_id`` from ``db_path`` and return the report.

    Function form; equivalent to ``ReplayEngine(db_path).replay(trace_id, mode=mode)``.
    """
    return ReplayEngine(db_path).replay(trace_id, mode=mode)


# Re-export so callers import ToolResult alongside the report without
# reaching into core.events directly.
__all__ = [
    "DriftDetail",
    "ReplayEngine",
    "ReplayMode",
    "ReplayReport",
    "ToolCallDriftError",
    "ToolResult",
    "replay",
]
