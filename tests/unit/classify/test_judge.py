"""LLM-judge stage: prompt hash + stub behaviour + should_invoke_judge."""

from __future__ import annotations

import pytest

from agentsla.classify.judge import (
    PROMPT_HASH,
    PROMPT_VERSION,
    StubJudge,
    _parse_judge_response,
    should_invoke_judge,
    summarise_events_for_judge,
)
from agentsla.classify.taxonomy import FailureCategory


class TestPrompt:
    def test_hash_format(self) -> None:
        assert PROMPT_HASH.startswith("sha256:")
        assert len(PROMPT_HASH) == len("sha256:") + 16

    def test_version_present(self) -> None:
        assert PROMPT_VERSION == "v1"


class TestStubJudge:
    def test_hallucination_marker(self) -> None:
        j = StubJudge()
        r = j.classify(
            trace_id="t1",
            task_id="task1",
            final_answer="__HALLUCINATE__ total is 9999",
            event_summary="",
        )
        assert r.category is FailureCategory.HALLUCINATED_FACT
        assert r.confidence == 0.95
        assert r.prompt_hash == PROMPT_HASH

    def test_loop_marker(self) -> None:
        j = StubJudge()
        r = j.classify(
            trace_id="t1",
            task_id="task1",
            final_answer="__LOOP__ nothing",
            event_summary="",
        )
        assert r.category is FailureCategory.RETRY_LOOP

    def test_clean_returns_none(self) -> None:
        j = StubJudge()
        r = j.classify(
            trace_id="t1",
            task_id="task1",
            final_answer="Just a clean answer",
            event_summary="",
        )
        assert r.category is None
        assert r.confidence == 0.6


class TestParseJudgeResponse:
    def test_parse_valid(self) -> None:
        r = _parse_judge_response(
            "category=hallucinated_fact confidence=0.85",
            prompt_hash=PROMPT_HASH,
        )
        assert r.category is FailureCategory.HALLUCINATED_FACT
        assert r.confidence == pytest.approx(0.85)

    def test_parse_none(self) -> None:
        r = _parse_judge_response("category=none confidence=0.99", prompt_hash=PROMPT_HASH)
        assert r.category is None
        assert r.confidence == pytest.approx(0.99)

    def test_parse_invalid_category_falls_back(self) -> None:
        r = _parse_judge_response("category=bogus confidence=0.5", prompt_hash=PROMPT_HASH)
        assert r.category is None

    def test_parse_garbage_returns_zero(self) -> None:
        r = _parse_judge_response("garbage line", prompt_hash=PROMPT_HASH)
        assert r.category is None
        assert r.confidence == 0.0


class TestShouldInvokeJudge:
    def test_empty_candidates_invoke(self) -> None:
        assert (
            should_invoke_judge(
                heuristic_candidates=[],
                heuristic_confidence=0.0,
                verification_incorrect=0,
            )
            is True
        )

    def test_strong_heuristic_skips_judge(self) -> None:
        assert (
            should_invoke_judge(
                heuristic_candidates=[FailureCategory.HALLUCINATED_FACT],
                heuristic_confidence=1.0,
                verification_incorrect=0,
            )
            is False
        )

    def test_hallucination_with_low_confidence_and_incorrect_invokes(self) -> None:
        assert (
            should_invoke_judge(
                heuristic_candidates=[FailureCategory.HALLUCINATED_FACT],
                heuristic_confidence=0.5,
                verification_incorrect=2,
            )
            is True
        )

    def test_other_low_confidence_skips_judge(self) -> None:
        # Low confidence alone is not enough — must combine with hallucinated_fact.
        assert (
            should_invoke_judge(
                heuristic_candidates=[FailureCategory.TIMEOUT],
                heuristic_confidence=0.5,
                verification_incorrect=2,
            )
            is False
        )


class TestSummariseEvents:
    def test_summarise_tool_calls(self) -> None:
        from tests.unit.classify._fixtures import make_tool_call

        tid = __import__("uuid").uuid4()
        events = [make_tool_call(tid, 0, tool="fetch")]
        s = summarise_events_for_judge(events)
        assert "tool_call" in s
        assert "fetch" in s

    def test_summarise_empty(self) -> None:
        assert summarise_events_for_judge([]) == "(no events)"