"""Tests for agentsla.core.types (shared primitives).

Coverage focuses on:
  - NewType / uuid4 plumbing (positive path only — types.py is mostly re-exports).
  - ``utcnow()`` returns a timezone-aware datetime.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from agentsla.core import types
from agentsla.core.types import new_call_id, new_trace_id, utcnow


def test_utcnow_is_timezone_aware() -> None:
    ts = utcnow()
    assert isinstance(ts, datetime)
    assert ts.tzinfo is not None
    assert ts.utcoffset() is not None


def test_utcnow_returns_recent_time() -> None:
    a = utcnow()
    b = utcnow()
    # b >= a (monotonic within one second resolution; if equal, that's also fine)
    assert (b - a).total_seconds() >= -1e-3


def test_new_trace_id_returns_uuid() -> None:
    tid = new_trace_id()
    assert isinstance(tid, UUID)
    # Newtype wraps UUID at runtime; value is still a UUID.
    assert tid.version == 4


def test_new_call_id_returns_uuid() -> None:
    cid = new_call_id()
    assert isinstance(cid, UUID)
    assert cid.version == 4


def test_trace_ids_are_unique() -> None:
    ids = {new_trace_id() for _ in range(100)}
    assert len(ids) == 100


def test_types_module_reexports_newtypes() -> None:
    assert hasattr(types, "TraceID")
    assert hasattr(types, "EventSeq")
    assert hasattr(types, "CallID")
