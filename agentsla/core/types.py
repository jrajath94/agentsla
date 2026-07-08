"""Shared primitive types for AgentSLA core.

These types are tiny on purpose — they exist to:
  1. make cross-module signatures unambiguous (UUID vs str, timestamp precision);
  2. give mypy something concrete to chase, instead of `Any`.
  3. keep the event-log invariants (e.g. microsecond timestamps) in one place.

No behaviour lives here, only structural definitions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NewType
from uuid import UUID, uuid4

# AgentSLA identifies every trace + every event with a UUID4. We use UUID objects
# (not bare strings) to make accidental mixing with arbitrary str fields a type
# error. The newtype wrapper is documentation more than constraint.
TraceID = NewType("TraceID", UUID)
EventSeq = NewType("EventSeq", int)
CallID = NewType("CallID", UUID)


def utcnow() -> datetime:
    """Timezone-aware UTC ``now()`` — single source of truth for trace timestamps.

    Using a function (not a module-level constant) keeps the test seam obvious:
    monkeypatching `agentsla.core.types.utcnow` makes a trace deterministic without
    touching deeper code.
    """
    return datetime.now(tz=UTC)


def new_trace_id() -> TraceID:
    """Fresh random ``TraceID``. Centralized so tests can monkeypatch if needed."""
    return TraceID(uuid4())


def new_call_id() -> CallID:
    """Fresh random ``CallID``."""
    return CallID(uuid4())


__all__ = [
    "CallID",
    "EventSeq",
    "TraceID",
    "new_call_id",
    "new_trace_id",
    "utcnow",
]
