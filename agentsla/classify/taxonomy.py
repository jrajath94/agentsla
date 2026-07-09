"""14-category failure taxonomy — single source of truth.

Source of truth: ``docs/class-taxonomy.md`` (committed before any classify/*.py
import — Phase 4 hard gate). Any change here MUST be reflected in that doc
and committed in the same change set; CI guards the ordering via
``git log --follow docs/class-taxonomy.md``.
"""

from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    """The 14 categories declared in docs/class-taxonomy.md."""

    FORMAT_VIOLATION = "format_violation"
    TOOL_CALL_ERROR = "tool_call_error"
    TOOL_RESPONSE_MISUSE = "tool_response_misuse"
    HALLUCINATED_FACT = "hallucinated_fact"
    REASONING_ERROR = "reasoning_error"
    PLANNING_ERROR = "planning_error"
    CONTEXT_OVERFLOW = "context_overflow"
    BUDGET_EXCEEDED = "budget_exceeded"
    PERMISSION_DENIED = "permission_denied"
    RETRY_LOOP = "retry_loop"
    POLICY_VIOLATION = "policy_violation"
    TIMEOUT = "timeout"
    PARTIAL_COMPLETION = "partial_completion"
    UNEXPECTED_TOOL_FAILURE = "unexpected_tool_failure"


# Severity score (1-10) for tie-breaking when multiple categories fit.
# Higher severity wins on collision; ties broken by CATEGORY_ORDER.
CATEGORY_SEVERITY: dict[FailureCategory, int] = {
    FailureCategory.FORMAT_VIOLATION: 4,
    FailureCategory.TOOL_CALL_ERROR: 6,
    FailureCategory.TOOL_RESPONSE_MISUSE: 7,
    FailureCategory.HALLUCINATED_FACT: 9,
    FailureCategory.REASONING_ERROR: 8,
    FailureCategory.PLANNING_ERROR: 5,
    FailureCategory.CONTEXT_OVERFLOW: 6,
    FailureCategory.BUDGET_EXCEEDED: 5,
    FailureCategory.PERMISSION_DENIED: 4,
    FailureCategory.RETRY_LOOP: 5,
    FailureCategory.POLICY_VIOLATION: 9,
    FailureCategory.TIMEOUT: 3,
    FailureCategory.PARTIAL_COMPLETION: 4,
    FailureCategory.UNEXPECTED_TOOL_FAILURE: 3,
}


# Lower number = higher precedence when severity ties.
CATEGORY_ORDER: dict[FailureCategory, int] = {
    FailureCategory.HALLUCINATED_FACT: 1,
    FailureCategory.POLICY_VIOLATION: 2,
    FailureCategory.REASONING_ERROR: 3,
    FailureCategory.TOOL_RESPONSE_MISUSE: 4,
    FailureCategory.TOOL_CALL_ERROR: 5,
    FailureCategory.CONTEXT_OVERFLOW: 6,
    FailureCategory.RETRY_LOOP: 7,
    FailureCategory.PLANNING_ERROR: 8,
    FailureCategory.BUDGET_EXCEEDED: 9,
    FailureCategory.PARTIAL_COMPLETION: 10,
    FailureCategory.PERMISSION_DENIED: 11,
    FailureCategory.FORMAT_VIOLATION: 12,
    FailureCategory.TIMEOUT: 13,
    FailureCategory.UNEXPECTED_TOOL_FAILURE: 14,
}


def rank(candidates: list[FailureCategory]) -> FailureCategory | None:
    """Pick the winning category from a set of candidates.

    Rule (docs/class-taxonomy.md §Selection Rule):
      1. Pick highest-severity.
      2. Tie: pick lowest CATEGORY_ORDER number.
    Returns None when ``candidates`` is empty.
    """
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda c: (-CATEGORY_SEVERITY[c], CATEGORY_ORDER[c]),
    )[0]


__all__ = [
    "CATEGORY_ORDER",
    "CATEGORY_SEVERITY",
    "FailureCategory",
    "rank",
]
