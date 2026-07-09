"""Taxonomy: 14 categories + rank function."""

from __future__ import annotations

import pytest

from agentsla.classify.taxonomy import (
    CATEGORY_ORDER,
    CATEGORY_SEVERITY,
    FailureCategory,
    rank,
)


class TestTaxonomyShape:
    def test_exactly_14_categories(self) -> None:
        assert len(FailureCategory) == 14

    def test_no_duplicate_string_values(self) -> None:
        values = [c.value for c in FailureCategory]
        assert len(values) == len(set(values))

    @pytest.mark.parametrize("cat", list(FailureCategory))
    def test_severity_assigned(self, cat: FailureCategory) -> None:
        assert cat in CATEGORY_SEVERITY
        assert 1 <= CATEGORY_SEVERITY[cat] <= 10

    @pytest.mark.parametrize("cat", list(FailureCategory))
    def test_order_assigned(self, cat: FailureCategory) -> None:
        assert cat in CATEGORY_ORDER
        assert 1 <= CATEGORY_ORDER[cat] <= 14


class TestRank:
    def test_empty_returns_none(self) -> None:
        assert rank([]) is None

    def test_single_returns_single(self) -> None:
        assert rank([FailureCategory.TIMEOUT]) is FailureCategory.TIMEOUT

    def test_picks_highest_severity(self) -> None:
        # hallucinated_fact (sev 9) beats timeout (sev 3)
        winner = rank([FailureCategory.TIMEOUT, FailureCategory.HALLUCINATED_FACT])
        assert winner is FailureCategory.HALLUCINATED_FACT

    def test_tie_breaks_by_order(self) -> None:
        # reasoning_error (sev 8) vs tool_response_misuse (sev 7) — no tie.
        # Construct a real tie with two sev-5 categories: retry_loop vs budget_exceeded.
        winner = rank([FailureCategory.RETRY_LOOP, FailureCategory.BUDGET_EXCEEDED])
        # retry_loop order=7, budget_exceeded order=9 → retry_loop wins on lower order.
        assert winner is FailureCategory.RETRY_LOOP

    def test_policy_violation_beats_format_violation(self) -> None:
        winner = rank([FailureCategory.FORMAT_VIOLATION, FailureCategory.POLICY_VIOLATION])
        assert winner is FailureCategory.POLICY_VIOLATION