"""agentsla.core — schema, trace store, replay engine.

Public surface:
    events    — Pydantic v2 event types (ToolCall, ToolResult, ModelMessage, Verdict, Trace).
    trace     — append-only DuckDB store (TraceWriter, TraceReader), Parquet export.
    replay    — strict + tolerant replay engines (ReplayReport, replay()).

Submodules evolve phase-by-phase per .planning/phases/01-trace-replay-rawloop/01-PLAN.md.
"""

from __future__ import annotations

__all__ = ["events", "replay", "trace", "types"]
