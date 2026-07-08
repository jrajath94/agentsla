"""Pydantic v2 event schema for the AgentSLA trace log.

Every event has a ``kind`` discriminator (one of the five concrete types below)
plus a ``trace_id`` and ``seq`` so the append-only log is strictly ordered.
``ModelMessage`` has ``model_id`` + ``response_id`` as mandatory fields to
defend against model-version drift in replay (PITFALL #1).

The schema is the *contract* the rest of the project rides on:
  - TraceWriter writes these to DuckDB verbatim (TRACE-02).
  - TraceReader reads them back, ordered (TRACE-03).
  - ReplayEngine hashes ``ToolCall.args`` to detect strict-mode drift (TRACE-04).
  - Verifier emits ``Verdict`` events; replays consume them (Phase 3).

Public surface added in plan 01.2 — this stub type-correctly declares the surface
so ``mypy --strict`` is green at scaffolding time. Implementations arrive in
the ``01-PLAN.md`` plan-node 01.2 commit.
"""

from __future__ import annotations

# Real implementations land in plan 01.2; intentionally not implemented yet so
# plan 01.2 has a clean break-the-glass refactor commit.
