"""Heuristic classifier stage — 14 deterministic triggers.

Each trigger inspects a trace (or its derived state) and returns a
:class:`FailureCategory` if it matches, else ``None``. The orchestrator
combines triggers and applies the selection rule.

A trigger is **deterministic** by contract: same input trace produces the
same output category (or None) every time. Triggers do NOT call an LLM.

This module is invoked twice:

  1. During the bench/CLI run (Phase 4 acceptance: ≤20% LLM-judge invocations).
  2. During unit tests (each trigger has a positive and a negative fixture).

Triggers read from :class:`agentsla.core.events.Trace`; they do not mutate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from agentsla.classify.taxonomy import FailureCategory
from agentsla.core.events import ToolCall, ToolResult, Trace, Verdict

# Default context-window size in bytes for ``context_overflow`` trigger.
# Models we target default to 200 KiB raw text; this is the conservative
# upper bound for tool-call-rich traces.
DEFAULT_CONTEXT_WINDOW_BYTES = 200 * 1024

# Retry-loop window: number of consecutive identical tool calls that count as a loop.
RETRY_LOOP_THRESHOLD = 3

# Permission-denied threshold: DENYs of the same tool name before flagging.
PERMISSION_DENIED_THRESHOLD = 2


def _tool_calls(trace: Trace) -> list[ToolCall]:
    """All ToolCall events in seq order."""
    return [e for e in trace.events if isinstance(e, ToolCall)]


def _tool_results(trace: Trace) -> list[ToolResult]:
    return [e for e in trace.events if isinstance(e, ToolResult)]


def _verdict(trace: Trace) -> Verdict | None:
    for e in trace.events:
        if isinstance(e, Verdict):
            return e
    return None


def _args_hash(call: ToolCall) -> str:
    """Stable hash of a ToolCall's (tool, args) for retry-loop detection.

    We deliberately exclude ``call_id`` and ``seq`` so two calls with the
    same logical content produce the same hash. Only the (tool, args) pair
    is what makes a "retry".
    """
    payload = json.dumps(
        {"tool": call.tool, "args": call.args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def trigger_format_violation(trace: Trace, *, declared_schema: dict | None = None) -> FailureCategory | None:
    """declared_schema exists AND final answer fails jsonschema validation.

    Schema is passed in by the orchestrator (the bench harness knows the
    task contract). When ``declared_schema`` is None, skip the trigger.
    """
    if not declared_schema:
        return None
    try:
        import jsonschema  # local import — keep heuristics module dep-light
    except ImportError:  # pragma: no cover — jsonschema is test-only
        return None
    try:
        jsonschema.validate(instance={"answer": trace.final_answer}, schema=declared_schema)
    except jsonschema.ValidationError:
        return FailureCategory.FORMAT_VIOLATION
    return None


def trigger_tool_call_error(trace: Trace, *, allowed_tools: Iterable[str] | None = None) -> FailureCategory | None:
    """A ToolCall whose name is not in policy.allowed_tools."""
    if allowed_tools is None:
        return None
    allowed = set(allowed_tools)
    for call in _tool_calls(trace):
        if call.tool not in allowed:
            return FailureCategory.TOOL_CALL_ERROR
    return None


def trigger_tool_response_misuse(trace: Trace) -> FailureCategory | None:
    """A ToolResult with error set AND a subsequent ToolCall that reuses the
    *exact* failing (tool, args) tuple.

    Distinguishes three agent behaviors after an error:
      * **No followup call** — agent gave up (planning failure, not misuse).
      * **Adapted call** — different tool OR different args — legitimate
        recovery. NOT flagged.
      * **Literal reuse** — same tool AND identical args_hash — the agent
        is blindly reusing a known-failing call. FLAGGED as
        ``tool_response_misuse``.

    The naive proxy "any subsequent call = misuse" produced too many
    false positives on adaptive retries.
    """
    if not _tool_results(trace):
        return None
    if not any(r.error for r in _tool_results(trace)):
        return None
    call_by_id = {ev.call_id: ev for ev in trace.events if isinstance(ev, ToolCall)}
    events = trace.events
    for i, ev in enumerate(events):
        if not isinstance(ev, ToolResult) or not ev.error:
            continue
        failing_call = call_by_id.get(ev.call_id)
        if failing_call is None:
            continue
        for j in range(i + 1, len(events)):
            nxt = events[j]
            if not isinstance(nxt, ToolCall):
                continue
            if nxt.tool == failing_call.tool and _args_hash(nxt) == _args_hash(failing_call):
                return FailureCategory.TOOL_RESPONSE_MISUSE
            # Adapted (different tool or different args) — not misuse.
            break
    return None


def trigger_hallucinated_fact(trace: Trace) -> FailureCategory | None:
    """Verdict.verified=False AND no other stronger category triggered."""
    v = _verdict(trace)
    if v is None or v.verified:
        return None
    # Defer to the orchestrator for precedence vs stronger categories
    # (policy_violation, reasoning_error). Return as a candidate only.
    return FailureCategory.HALLUCINATED_FACT


def trigger_reasoning_error(trace: Trace) -> FailureCategory | None:
    """Detect a numeric contradiction anchored on a multi-word noun phrase.

    The naive "first-word anchor" rule produced false positives on
    numbered-step enumerations ("Step 1: get 100. Step 2: get 50.").
    The reframed rule:

      1. Skip sentences that begin with a step marker (``"1."``, ``"2:"``,
         ``"3)"``) — these are enumerations, not claims about the same
         quantity.
      2. Anchor each sentence on its first two lowercased words (falling
         back to one word when the sentence is shorter).
      3. Fire when two sentences share an anchor AND their first numbers
         differ.

    Examples:
      * "Total is 100. Total is 50." → anchor "total is", values {100, 50}
        → REASONING_ERROR.
      * "Revenue was 100. Profit was 50." → distinct anchors
        ("revenue was" vs "profit was") → no fire.
      * "Step 1: get 100. Step 2: get 50." → step-marker sentences skipped
        → no fire.
    """
    import re

    text = trace.final_answer or ""
    sentences = re.split(r"[.!?]\s*", text)
    nums_re = re.compile(r"-?\d+(?:\.\d+)?")
    word_re = re.compile(r"[a-zA-Z]+")
    step_re = re.compile(r"^\s*\w+\s+\d+[.:)]\s*")
    by_anchor: dict[str, set[float]] = {}
    for sent in sentences:
        if not sent.strip():
            continue
        if step_re.match(sent):
            # Enumeration ("1. …", "2: …") — skip; not a quantity claim.
            continue
        words = word_re.findall(sent)
        nums = nums_re.findall(sent)
        if not nums or not words:
            continue
        anchor = " ".join(w.lower() for w in words[:2])
        by_anchor.setdefault(anchor, set()).add(float(nums[0]))
    for vals in by_anchor.values():
        if len(vals) >= 2:
            return FailureCategory.REASONING_ERROR
    return None


def trigger_planning_error(trace: Trace) -> FailureCategory | None:
    """Trace ends without a Verdict AND ≥1 ToolResult.error was encountered."""
    v = _verdict(trace)
    if v is not None:
        return None
    if any(r.error for r in _tool_results(trace)):
        return FailureCategory.PLANNING_ERROR
    return None


def trigger_context_overflow(trace: Trace, *, threshold_bytes: int = DEFAULT_CONTEXT_WINDOW_BYTES) -> FailureCategory | None:
    """Sum of event payload bytes > model context window."""
    total = 0
    for ev in trace.events:
        total += len(ev.model_dump_json().encode("utf-8"))
    if total > threshold_bytes:
        return FailureCategory.CONTEXT_OVERFLOW
    return None


def trigger_budget_exceeded(trace: Trace, *, budget_exhausted: bool = False, **_: Any) -> FailureCategory | None:
    """A BudgetManager.exhausted event is present.

    The orchestrator passes ``budget_exhausted`` through heuristic_context;
    the trigger matches when the flag is True.
    """
    return FailureCategory.BUDGET_EXCEEDED if budget_exhausted else None


def trigger_permission_denied(
    trace: Trace, *, deny_counts: dict[str, int] | None = None, threshold: int = PERMISSION_DENIED_THRESHOLD, **_: Any
) -> FailureCategory | None:
    """Same DENY decision ≥threshold times for the same tool name."""
    if not deny_counts:
        return None
    for _tool_name, count in deny_counts.items():
        if count >= threshold:
            return FailureCategory.PERMISSION_DENIED
    return None


def trigger_retry_loop(trace: Trace, *, threshold: int = RETRY_LOOP_THRESHOLD) -> FailureCategory | None:
    """≥threshold consecutive ToolCall events with identical (tool_name, args_hash)."""
    calls = _tool_calls(trace)
    if len(calls) < threshold:
        return None
    consecutive = 1
    from itertools import pairwise

    for prev, curr in pairwise(calls):
        if prev.tool == curr.tool and _args_hash(prev) == _args_hash(curr):
            consecutive += 1
            if consecutive >= threshold:
                return FailureCategory.RETRY_LOOP
        else:
            consecutive = 1
    return None


def trigger_policy_violation(trace: Trace, *, egress_hits: list[str] | None = None, **_: Any) -> FailureCategory | None:
    """An event payload matches the egress regex pack hit list."""
    if not egress_hits:
        return None
    return FailureCategory.POLICY_VIOLATION


def trigger_timeout(trace: Trace, *, deadline_s: float = 120.0) -> FailureCategory | None:
    """Trace duration > deadline_s (default 120s)."""
    if trace.end_ts is None or trace.start_ts is None:
        return None
    delta = (trace.end_ts - trace.start_ts).total_seconds()
    if delta > deadline_s:
        return FailureCategory.TIMEOUT
    return None


def trigger_partial_completion(trace: Trace, *, required_fields: Iterable[str] | None = None) -> FailureCategory | None:
    """required_fields not all present in final answer."""
    if not required_fields:
        return None
    answer = (trace.final_answer or "").lower()
    for f in required_fields:
        if f.lower() not in answer:
            return FailureCategory.PARTIAL_COMPLETION
    return None


def trigger_unexpected_tool_failure(trace: Trace) -> FailureCategory | None:
    """A ToolResult.error not classified by other triggers."""
    if not any(r.error for r in _tool_results(trace)):
        return None
    # Heuristic: if tool_response_misuse did NOT trigger AND tool_call_error
    # did NOT trigger, this is an unexpected_tool_failure.
    if trigger_tool_response_misuse(trace) is not None:
        return None
    return FailureCategory.UNEXPECTED_TOOL_FAILURE


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


# All triggers in deterministic order; the orchestrator can iterate this list.
HEURISTIC_TRIGGERS = [
    trigger_policy_violation,
    trigger_format_violation,
    trigger_tool_call_error,
    trigger_tool_response_misuse,
    trigger_hallucinated_fact,
    trigger_reasoning_error,
    trigger_planning_error,
    trigger_context_overflow,
    trigger_budget_exceeded,
    trigger_permission_denied,
    trigger_retry_loop,
    trigger_timeout,
    trigger_partial_completion,
    trigger_unexpected_tool_failure,
]


__all__ = [
    "DEFAULT_CONTEXT_WINDOW_BYTES",
    "HEURISTIC_TRIGGERS",
    "PERMISSION_DENIED_THRESHOLD",
    "RETRY_LOOP_THRESHOLD",
    "trigger_budget_exceeded",
    "trigger_context_overflow",
    "trigger_format_violation",
    "trigger_hallucinated_fact",
    "trigger_partial_completion",
    "trigger_permission_denied",
    "trigger_planning_error",
    "trigger_policy_violation",
    "trigger_reasoning_error",
    "trigger_retry_loop",
    "trigger_timeout",
    "trigger_tool_call_error",
    "trigger_tool_response_misuse",
    "trigger_unexpected_tool_failure",
]
