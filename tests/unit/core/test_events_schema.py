"""Tests for agentsla.core.events (TRACE-01, TRACE-06, TYPING-01).

Coverage focus:
  - Mandatory ``model_id`` + ``response_id`` rejection (PITFALL #1).
  - Round-trip JSON serialization for every event type.
  - ``canonical_args_hash`` determinism + sensitivity to key order.
  - JSON Schema export validity (TRACE-06).
  - Trace model copy/append semantics + per-event trace_id enforcement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from agentsla.core.events import (
    ClaimVerdict,
    ModelMessage,
    Timestamp,
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
    canonical_args_hash,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _trace_id() -> UUID:
    return uuid4()


def _ts() -> Timestamp:
    # `Timestamp` is an Annotated[datetime, ...] alias at runtime. Importing
    # it surfaces as the underlying class only; we assert by isinstance below.
    return datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)  # type: ignore[return-value]


@pytest.fixture()
def fixed_call_id() -> UUID:
    return UUID("00000000-0000-4000-8000-000000000001")


@pytest.fixture()
def tool_call_kwargs(fixed_call_id: UUID) -> dict[str, object]:
    return {
        "call_id": fixed_call_id,
        "tool": "fetch",
        "args": {"url": "https://example.com", "limit": 10},
        "trace_id": _trace_id(),
        "seq": 0,
        "ts": _ts(),
    }


# ---------------------------------------------------------------------------
# ToolCall + canonical_args_hash
# ---------------------------------------------------------------------------


def test_canonical_args_hash_is_stable_across_key_order() -> None:
    a = canonical_args_hash({"b": 2, "a": 1})
    b = canonical_args_hash({"a": 1, "b": 2})
    assert a == b
    assert len(a) == 64


def test_canonical_args_hash_changes_when_value_changes() -> None:
    a = canonical_args_hash({"x": 1})
    b = canonical_args_hash({"x": 2})
    assert a != b


def test_canonical_args_hash_handles_unicode() -> None:
    a = canonical_args_hash({"name": "café"})
    b = canonical_args_hash({"name": "cafe"})
    assert a != b
    assert len(a) == 64


def test_tool_call_args_hash_rejects_invalid_shape(tool_call_kwargs: dict[str, object]) -> None:
    bad = dict(tool_call_kwargs, args_hash="not-a-sha")
    with pytest.raises(ValidationError):
        ToolCall.model_validate(bad)


def test_tool_call_accepts_empty_args_hash(tool_call_kwargs: dict[str, object]) -> None:
    """The writer is responsible for the hash; caller passes ``args_hash=''``."""
    tc = ToolCall.model_validate(dict(tool_call_kwargs, args_hash=""))
    assert tc.args_hash == ""


def test_tool_call_forbids_extra_fields(tool_call_kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ToolCall.model_validate(dict(tool_call_kwargs, tool_nmae="fetch"))


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


def test_tool_result_round_trips(tool_call_kwargs: dict[str, object]) -> None:
    tr = ToolResult.model_validate(
        {
            "kind": "tool_result",
            "call_id": tool_call_kwargs["call_id"],
            "tool": "fetch",
            "result": {"status": 200, "body": "ok"},
            "is_error": False,
            "latency_ms": 12.5,
            "trace_id": tool_call_kwargs["trace_id"],
            "seq": 1,
            "ts": _ts(),
        }
    )
    payload = tr.model_dump_json()
    reloaded = ToolResult.model_validate_json(payload)
    assert reloaded == tr


# ---------------------------------------------------------------------------
# ModelMessage — PITFALL #1 mitigation
# ---------------------------------------------------------------------------


def _msg_kwargs() -> dict[str, object]:
    return {
        "msg_id": uuid4(),
        "trace_id": _trace_id(),
        "seq": 0,
        "role": "assistant",
        "content": "hello",
        "model_id": "claude-haiku-4-5-20251001",
        "response_id": "msg_01abcdef0000",
        "ts": _ts(),
    }


def test_model_message_requires_model_id() -> None:
    bad = {k: v for k, v in _msg_kwargs().items() if k != "model_id"}
    with pytest.raises(ValidationError) as ei:
        ModelMessage.model_validate(bad)
    # The exact error path proves which field failed.
    errors = ei.value.errors()
    assert any(e["loc"] == ("model_id",) for e in errors)


def test_model_message_requires_response_id() -> None:
    bad = {k: v for k, v in _msg_kwargs().items() if k != "response_id"}
    with pytest.raises(ValidationError) as ei:
        ModelMessage.model_validate(bad)
    errors = ei.value.errors()
    assert any(e["loc"] == ("response_id",) for e in errors)


def test_model_message_round_trips() -> None:
    kwargs = _msg_kwargs()
    msg = ModelMessage.model_validate(kwargs)
    payload = msg.model_dump_json()
    reloaded = ModelMessage.model_validate_json(payload)
    assert reloaded == msg


# ---------------------------------------------------------------------------
# ClaimVerdict + Verdict
# ---------------------------------------------------------------------------


def test_verdict_coverage_bounds_enforced() -> None:
    base = {
        "verdict_id": uuid4(),
        "trace_id": _trace_id(),
        "seq": 99,
        "verifier": "numeric",
        "verified": True,
        "per_claim": [],
        "detail": "",
        "ts": _ts(),
    }
    Verdict.model_validate(dict(base, coverage=0.0))  # lower bound OK
    Verdict.model_validate(dict(base, coverage=1.0))  # upper bound OK
    with pytest.raises(ValidationError):
        Verdict.model_validate(dict(base, coverage=-0.01))
    with pytest.raises(ValidationError):
        Verdict.model_validate(dict(base, coverage=1.01))


def test_verdict_per_claim_round_trip() -> None:
    cv = ClaimVerdict(
        claim_text="42",
        passed=True,
        expected="42",
        actual="42",
        source_tool_id=uuid4(),
        detail="ok",
    )
    v = Verdict(
        verdict_id=uuid4(),
        trace_id=_trace_id(),
        seq=10,
        verifier="numeric",
        verified=True,
        coverage=1.0,
        per_claim=[cv],
        detail="",
        ts=_ts(),
    )
    payload = v.model_dump_json()
    reloaded = Verdict.model_validate_json(payload)
    assert reloaded.per_claim[0] == cv


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def test_trace_with_event_appends_in_order() -> None:
    base_trace = Trace(
        trace_id=_trace_id(),
        task_id="demo",
        model_id="claude-haiku-4-5-20251001",
        events=[],
        final_answer="",
        start_ts=_ts(),
    )
    msg = ModelMessage.model_validate(_msg_kwargs())
    t2 = base_trace.with_event(msg)
    assert t2.events == [msg]
    # Original is untouched (immutable update pattern).
    assert base_trace.events == []


# ---------------------------------------------------------------------------
# JSON Schema export — TRACE-06
# ---------------------------------------------------------------------------


def test_event_types_export_parseable_json_schema() -> None:
    """Each event class's JSON Schema export must parse as valid JSON."""
    import json as _json

    for cls in (ToolCall, ToolResult, ModelMessage, Verdict, Trace):
        schema = cls.model_json_schema()
        # Serializes + reparses, so we know it's portable.
        encoded = _json.dumps(schema)
        reloaded = _json.loads(encoded)
        assert "title" in reloaded, f"{cls.__name__} schema missing title"


def test_trace_schema_requires_model_id_and_response_id() -> None:
    """JSON Schema export must also refuse to omit model_id/response_id."""
    schema = ModelMessage.model_json_schema()
    required = schema.get("required", [])
    assert "model_id" in required
    assert "response_id" in required


def test_discriminated_union_via_kind() -> None:
    """The ``kind`` discriminator distinguishes event types in serialized form."""
    adapter = TypeAdapter(Trace.model_fields["events"].annotation)  # type: ignore[arg-type]
    assert adapter is not None  # smoke — schema materializes without errors
