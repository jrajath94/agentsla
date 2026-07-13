"""LLM-judge stage: prompt hash + stub behaviour + should_invoke_judge."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from agentsla.classify.judge import (
    PROMPT_HASH,
    PROMPT_VERSION,
    ClaudeJudge,
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

    def test_summarise_includes_error_when_present(self) -> None:
        """Lines 240-241: event with .error attribute adds `` error='...'``."""
        from unittest.mock import MagicMock

        call = MagicMock()
        call.kind = "tool_call"
        call.tool_name = "fetch"
        call.tool = "fetch"
        call.error = "ValueError: bad input"
        call.verified = None
        s = summarise_events_for_judge([call])
        assert "error=" in s
        assert "ValueError" in s

    def test_summarise_includes_verified_false_flag(self) -> None:
        """Lines 242-243: event with ``.verified == False`` adds ``verified=false``."""
        from unittest.mock import MagicMock

        call = MagicMock()
        call.kind = "tool_call"
        call.tool_name = "fetch"
        call.tool = "fetch"
        call.error = None
        call.verified = False
        s = summarise_events_for_judge([call])
        assert "verified=false" in s

    def test_summarise_skips_verified_true(self) -> None:
        """Verified=True must NOT add the flag (only False triggers the line)."""
        from unittest.mock import MagicMock

        call = MagicMock()
        call.kind = "tool_call"
        call.tool_name = "fetch"
        call.tool = "fetch"
        call.error = None
        call.verified = True
        s = summarise_events_for_judge([call])
        assert "verified=" not in s


class TestParseJudgeResponseEdgeCases:
    def test_parse_malformed_confidence_returns_zero(self) -> None:
        """Lines 197-198: confidence=NaNGarbage → except ValueError → confidence=0.0."""
        r = _parse_judge_response(
            "category=hallucinated_fact confidence=NaNGarbage",
            prompt_hash=PROMPT_HASH,
        )
        assert r.category is FailureCategory.HALLUCINATED_FACT
        assert r.confidence == 0.0

    def test_parse_partial_response_with_only_category(self) -> None:
        """Missing confidence token → confidence stays 0.0."""
        r = _parse_judge_response(
            "category=retry_loop",
            prompt_hash=PROMPT_HASH,
        )
        assert r.category is FailureCategory.RETRY_LOOP
        assert r.confidence == 0.0

    def test_parse_comma_separated_response(self) -> None:
        """Commas between tokens still parse (per line 191's ``replace(\",\", \" \")``)."""
        r = _parse_judge_response(
            "category=tool_call_error,confidence=0.77",
            prompt_hash=PROMPT_HASH,
        )
        assert r.category is FailureCategory.TOOL_CALL_ERROR
        assert r.confidence == pytest.approx(0.77)


class TestClaudeJudge:
    def test_init_defaults(self) -> None:
        """Lines 151-153: default model/temperature/api_key."""
        j = ClaudeJudge()
        assert j.model == "claude-haiku-4-5"
        assert j.temperature == 0.0
        assert j.api_key is None

    def test_init_custom(self) -> None:
        j = ClaudeJudge(model="claude-opus-4-8", temperature=0.5, api_key="sk-test")
        assert j.model == "claude-opus-4-8"
        assert j.temperature == 0.5
        assert j.api_key == "sk-test"

    def test_classify_with_mocked_anthropic(self) -> None:
        """Lines 163-183: full ClaudeJudge.classify path with mocked SDK client.

        Patches ``anthropic.Anthropic`` so the SDK call is hermetic; asserts the
        prompt is well-formed, the response is parsed, and the JudgeResult carries
        PROMPT_HASH + the parsed category.
        """
        fake_anthropic = MagicMock()
        # Build a fake response whose .content is a list of blocks with .text.
        fake_block = MagicMock()
        fake_block.type = "text"
        fake_block.text = "category=hallucinated_fact confidence=0.92"
        fake_response = MagicMock()
        fake_response.content = [fake_block]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response
        fake_anthropic.Anthropic.return_value = fake_client
        fake_anthropic.Anthropic.return_value = fake_client

        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            j = ClaudeJudge(api_key="sk-test")
            result = j.classify(
                trace_id="t1",
                task_id="task1",
                final_answer="the answer is 99",
                event_summary="- tool_call tool=json_echo",
            )

        assert result.category is FailureCategory.HALLUCINATED_FACT
        assert result.confidence == pytest.approx(0.92)
        assert result.prompt_hash == PROMPT_HASH
        # Prompt must include the trace_id + final_answer (sanity for the format).
        call_kwargs = fake_client.messages.create.call_args.kwargs
        assert "messages" in call_kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5"
        prompt_text = call_kwargs["messages"][0]["content"]
        assert "t1" in prompt_text
        assert "the answer is 99" in prompt_text

    def test_classify_without_anthropic_raises_runtime_error(self) -> None:
        """Lines 164-166: anthropic package missing → RuntimeError, not ImportError."""
        # Remove anthropic from sys.modules if present, and patch the import to fail.
        with patch.dict(sys.modules, {"anthropic": None}):
            j = ClaudeJudge(api_key="sk-test")
            with pytest.raises(RuntimeError, match="anthropic"):
                j.classify(
                    trace_id="t1",
                    task_id="task1",
                    final_answer="x",
                    event_summary="",
                )
