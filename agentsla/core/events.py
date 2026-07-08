"""Pydantic v2 event schema for the AgentSLA trace log.

Every event has a ``kind`` discriminator (one of the four concrete types below)
plus a ``trace_id`` and ``seq`` so the append-only log is strictly ordered.
``ModelMessage`` has ``model_id`` + ``response_id`` as mandatory fields to
defend against model-version drift in replay (PITFALL #1 — replay non-determinism
from model-version drift). The ``ToolCall.args_hash`` is the canonical-JSON
sha256 used by the strict replay engine to detect argument drift.

Schema design choices (TRACE-01 + TRACE-06):

    1. Polymorphic via Pydantic v2 ``Discriminator``; ``kind`` is the field
       used as the union tag. Single source of truth for storage + transport.
    2. ``args_hash`` is computed lazily by a small helper (canonical JSON via
       ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` + utf-8 +
       sha256). It is NEVER accepted from a caller — the writer computes the
       hash before persisting, so storage never holds a stale hash.
    3. ``Timestamp`` is ``Annotated[datetime, AfterValidator(...)]`` with a
       tz-aware requirement. Subclassing ``datetime`` was attempted first but
       breaks Pydantic v2's JSON Schema generation (PlainValidatorFunctionSchema
       has no schema representation); an Annotated alias sidesteps that and
       keeps ISO-8601 round-trips lossless (PITFALL #13 — Parquet TZ).
    4. Coverage is a float in [0.0, 1.0]. The Phase-3 verifier populates it;
       Phase 1-2 traces leave it at the trivial 1.0 sentinel.
    5. All models validate on assignment (``validate_assignment=True``) so
       ad-hoc edits fail loud rather than silently corrupting an in-flight
       trace.

Public surface:

    Timestamp          — Annotated datetime alias (tz-aware only).
    ToolCall           — proposed tool invocation (call_id, tool, args, args_hash).
    ToolResult         — result of a tool call (linked via ``call_id``).
    ModelMessage       — LLM I/O message with mandatory model_id + response_id.
    ClaimVerdict       — single claim check result (Phase 3 emits; defined
                         here so ``Verdict.per_claim`` has a stable shape
                         from day one — VERIFY-05).
    Verdict            — overall verification outcome (coverage + per_claim).
    Trace              — ordered list of events for one agent run.
    Event              — discriminated union of the four event types.
    canonical_args_hash — pure helper for computing ``ToolCall.args_hash``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


# Strict ASCII identifier — model IDs and tool names must match. Anthropic
# model IDs are versioned `claude-X-Y-Z-YYYYMMDD` style; tool names are usually
# `[a-z0-9_]+`. Be liberal on input, strict on output.
TypeIdStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


def _require_tz_aware(value: datetime) -> datetime:
    """Reject naive datetimes; round-trip behavior is otherwise ambiguous.

    Stored as ISO-8601 string (microsecond precision, UTC offset), which
    preserves fidelity through DuckDB JSON columns and Parquet round-trips
    (PITFALL #13 — TZ round-trip).
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("Timestamp must be timezone-aware (UTC preferred).")
    return value


# ``Timestamp`` is ``datetime`` with a tz-aware constraint. Modeling it as an
# Annotated alias (rather than a subclass) dodges Pydantic v2's JSON Schema
# generation issues with PlainValidatorFunctionSchema.
Timestamp = Annotated[datetime, AfterValidator(_require_tz_aware)]


def now_timestamp() -> datetime:
    """Source of truth for the ``ts`` default factory.

    Returns a tz-aware UTC ``datetime``. Tests that need deterministic
    timestamps should pass ``ts=`` explicitly.
    """
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Hash helpers (canonical JSON + sha256)
# ---------------------------------------------------------------------------


def canonical_args_hash(args: dict[str, Any]) -> str:
    """Compute the canonical-JSON sha256 of a tool-call's args.

    Canonical form:
      * sort_keys=True so {"a":1,"b":2} and {"b":2,"a":1} hash equal.
      * separators=(",", ":") so spacing doesn't change the hash.
      * ensure_ascii=False — UTF-8 bytes round-trip cleanly through DuckDB JSON.

    Returns a 64-char lowercase hex digest.

    Used by ``ToolCall.args_hash`` (writer-computed, never caller-supplied)
    and by the strict replay engine to detect argument drift (TRACE-04).
    """
    import hashlib

    payload = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core events
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Shared Pydantic configuration for every event class.

    - extra='forbid' so a typo (`tool_nmae`) fails loud instead of silently
      persisting an unrecognized column that the reader cannot round-trip.
    - validate_assignment=True so a runtime update through assignment fails
      loud rather than writing an invalid value to a Trace.
    - str_strip_whitespace=True so trailing-newline mistakes don't sneak in.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class ToolCall(_StrictModel):
    """Single proposed tool invocation within an agent trace.

    ``args_hash`` is computed by the writer from ``args``. Callers should pass
    only ``args``; the writer computes the hash and rejects if it disagrees
    with what was provided (anti-tamper, defends the trace store from caller
    drift). See :func:`canonical_args_hash`.
    """

    kind: Literal["tool_call"] = "tool_call"
    call_id: UUID = Field(description="Unique identifier of the tool call.")
    tool: TypeIdStr = Field(description="Tool name registered with the adapter.")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments passed to the tool; arbitrary JSON-serializable structure.",
    )
    trace_id: UUID = Field(description="Owning trace's UUID.")
    seq: int = Field(ge=0, description="Per-trace sequence number; strictly increasing.")
    ts: Timestamp = Field(default_factory=now_timestamp, description="Wall-clock time emitted.")
    parent_msg_id: UUID | None = Field(
        default=None,
        description=(
            "ModelMessage.response_id that triggered this call, when applicable. "
            "None when the call originates from a control-plane rule."
        ),
    )
    args_hash: str = Field(
        default="",
        description=(
            "Canonical-JSON sha256 of `args`; populated by the writer. Empty "
            "value accepted at validation time (writer overwrites)."
        ),
    )

    @field_validator("args_hash")
    @classmethod
    def _check_hash_shape(cls, value: str) -> str:
        """Allow empty (writer fills it); otherwise enforce 64-char lowercase hex."""
        if value == "":
            return value
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("args_hash must be a 64-char lowercase hex digest (or empty).")
        return value


class ToolResult(_StrictModel):
    """Result of a tool call.

    Linked to its ToolCall via ``call_id`` (matches ``ToolCall.call_id``).
    The sequence of call → result within a trace is preserved by ordering on
    ``ts``; the writer also emits a separate ``seq`` for positional stability.
    """

    kind: Literal["tool_result"] = "tool_result"
    call_id: UUID = Field(description="Corresponds to ToolCall.call_id.")
    tool: TypeIdStr = Field(description="Tool name (denormalized for fast filtering).")
    result: Any = Field(
        default=None,
        description="Tool result; arbitrary JSON-serializable value (or text).",
    )
    is_error: bool = Field(default=False, description="True when the tool raised.")
    error: str | None = Field(
        default=None,
        description="Human-readable error message when ``is_error`` is True.",
    )
    latency_ms: float = Field(default=0.0, ge=0.0, description="Wall-clock latency.")
    trace_id: UUID = Field(description="Owning trace's UUID.")
    seq: int = Field(ge=0, description="Per-trace sequence number; strictly increasing.")
    ts: Timestamp = Field(default_factory=now_timestamp, description="Wall-clock time emitted.")


class ModelMessage(_StrictModel):
    """LLM input/output message with mandatory model identity.

    ``model_id`` and ``response_id`` are REQUIRED (PITFALL #1 mitigation). A
    trace missing either field fails validation at write-time, which means
    strict replay can never silently produce a divergent answer because the
    underlying model itself changed.
    """

    kind: Literal["model_message"] = "model_message"
    msg_id: UUID = Field(description="Unique ID of this message.")
    trace_id: UUID = Field(description="Owning trace's UUID.")
    seq: int = Field(ge=0, description="Per-trace sequence number.")
    role: Literal["system", "user", "assistant", "tool"] = Field(description="Chat role.")
    content: str = Field(description="Message content (text-only for Phase 1).")
    model_id: TypeIdStr = Field(
        description=(
            "Mandatory model identifier (e.g. 'claude-haiku-4-5-20251001'). "
            "Replays REQUIRE this to be present so a model-version change is "
            "detected, not silently absorbed."
        ),
    )
    response_id: TypeIdStr = Field(
        description=(
            "Mandatory upstream response identifier (e.g. Anthropic "
            "'msg_01abc...'). Same rationale as ``model_id``."
        ),
    )
    ts: Timestamp = Field(default_factory=now_timestamp, description="Wall-clock time emitted.")


class ClaimVerdict(_StrictModel):
    """Per-claim verification result.

    Defined here so :class:`Verdict.per_claim` has a stable shape from day one
    (VERIFY-05: coverage as first-class metric). The Phase-3 numeric /
    grounding / schema verifiers populate it; Phase-1 traces leave
    ``per_claim`` empty (``coverage == 1.0`` is the trivial-everything-checked
    sentinel).
    """

    claim_text: str = Field(description="Verbatim claim that was checked.")
    passed: bool = Field(description="True if the claim passed the check.")
    expected: str | None = Field(default=None, description="Expected value, if recomputed.")
    actual: str | None = Field(default=None, description="Observed value, if recomputed.")
    source_tool_id: UUID | None = Field(
        default=None,
        description="UUID of the ToolResult that supplied the ground truth.",
    )
    detail: str = Field(default="", description="Human-readable explanation.")


class Verdict(_StrictModel):
    """Overall verification outcome for a trace's final answer.

    ``coverage`` is in [0.0, 1.0]; ``per_claim`` carries the per-claim breakdown.
    Phase-1 traces have ``coverage=1.0, per_claim=[]`` by convention (no claims
    extracted yet; the verifier integration lands in Phase 3).
    """

    kind: Literal["verdict"] = "verdict"
    verdict_id: UUID = Field(description="Unique identifier of this verdict.")
    trace_id: UUID = Field(description="Owning trace's UUID.")
    seq: int = Field(ge=0, description="Per-trace sequence number.")
    verifier: Literal["numeric", "grounding", "schema", "composite"] = Field(
        description="Verifier that produced this verdict.",
    )
    verified: bool = Field(description="Overall verification pass/fail.")
    coverage: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of claims that were checked.",
    )
    per_claim: list[ClaimVerdict] = Field(
        default_factory=list,
        description="Per-claim breakdown.",
    )
    corrected_answer: str | None = Field(
        default=None,
        description="Optional auto-corrected answer; populated only when auto_correct is opted in.",
    )
    detail: str = Field(default="", description="Human-readable summary.")
    ts: Timestamp = Field(default_factory=now_timestamp, description="Wall-clock time emitted.")
    corrects: UUID | None = Field(
        default=None,
        description=(
            "If this verdict corrects a prior one, the prior verdict's UUID. "
            "Corrections are new events, never in-place mutations (PITFALL #5)."
        ),
    )


# ---------------------------------------------------------------------------
# Trace = ordered list of events, plus discriminated-union Event alias.
# ---------------------------------------------------------------------------


# Concrete union members, listed in priority order. Used by ``Discriminator``
# via the ``kind`` field.
Event = Annotated[
    ToolCall | ToolResult | ModelMessage | Verdict,
    Field(discriminator="kind"),
]


class Trace(_StrictModel):
    """One complete agent run, exposed as a list of ordered events.

    The trace's own UUID is ``trace_id``. Phase-1 storage persists events in
    a DuckDB table with (trace_id, seq) as the ordering key; this class
    reconstructs the in-memory representation.

    ``model_id`` is duplicated at the trace level so replays can fail-fast
    with a clear error if the executing model disagrees with the recorded
    model (defense in depth alongside ``ModelMessage.model_id``).
    """

    trace_id: UUID = Field(description="Owning trace's UUID.")
    task_id: str = Field(description="Identifier of the task this trace executed.")
    model_id: TypeIdStr = Field(
        description=(
            "The model the trace was recorded against. Mismatched replays fail loud (PITFALL #1)."
        ),
    )
    events: list[Event] = Field(
        default_factory=list,
        description="Chronologically ordered events.",
    )
    final_answer: str = Field(
        default="",
        description="The trace's terminal answer text, or empty until completion.",
    )
    start_ts: Timestamp = Field(default_factory=now_timestamp)
    end_ts: Timestamp | None = Field(default=None)

    def with_event(self, event: ToolCall | ToolResult | ModelMessage | Verdict) -> Self:
        """Return a new Trace with ``event`` appended (records-as-values style)."""
        return self.model_copy(update={"events": [*self.events, event]})
