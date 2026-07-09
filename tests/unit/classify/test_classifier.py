"""Classifier orchestrator: heuristic + judge + label emission + agreement."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from agentsla.classify import (
    Classifier,
    FailureCategory,
    InMemoryLabelSink,
    JsonlLabelSink,
    StubJudge,
    agreement,
)
from tests.unit.classify._fixtures import (
    make_tool_call,
    make_trace,
)


def _trace_with_deny_counts(counts: dict[str, int]):
    return make_trace(events=[])


def _classifier_with_denies(counts: dict[str, int]):
    return Classifier(sink=InMemoryLabelSink(), heuristic_context={"deny_counts": counts})


class TestClassifierPipeline:
    def test_heuristic_only(self) -> None:
        sink = InMemoryLabelSink()
        c = Classifier(sink=sink, heuristic_context={"allowed_tools": ["fetch"]})
        tid = uuid4()
        events = [make_tool_call(tid, 0, tool="rogue", args={"q": "x"})]
        trace = make_trace(events=events)
        result = c.classify(trace)
        assert result.category is FailureCategory.TOOL_CALL_ERROR
        assert result.source == "heuristic"
        assert len(sink.rows) == 1

    def test_judge_invoked_when_no_heuristic(self) -> None:
        sink = InMemoryLabelSink()
        c = Classifier(
            judge=StubJudge(),
            sink=sink,
        )
        trace = make_trace(events=[], final_answer="__HALLUCINATE__ Total is 9999.")
        result = c.classify(trace, verification_incorrect=1)
        # Stub judge returns hallucinated_fact for the marker
        assert result.category is FailureCategory.HALLUCINATED_FACT
        assert result.source == "llm_judge"
        assert result.judge_prompt_hash is not None

    def test_on_classify_callback_invoked(self) -> None:
        sink = InMemoryLabelSink()
        seen: list[FailureCategory | None] = []
        c = Classifier(
            sink=sink,
            on_classify=lambda r: seen.append(r.category),
            heuristic_context={"allowed_tools": ["fetch"]},
        )
        tid = uuid4()
        events = [make_tool_call(tid, 0, tool="rogue")]
        c.classify(make_trace(events=events))
        assert seen == [FailureCategory.TOOL_CALL_ERROR]

    def test_permission_denied_via_metadata(self) -> None:
        c = _classifier_with_denies({"fetch": 3})
        result = c.classify(make_trace(events=[]))
        assert result.category is FailureCategory.PERMISSION_DENIED


class TestJsonlSink:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        sink = JsonlLabelSink(tmp_path / "labels.jsonl")
        sink.append({"trace_id": "t1", "category": "hallucinated_fact"})
        sink.append({"trace_id": "t2", "category": "none"})
        content = (tmp_path / "labels.jsonl").read_text()
        lines = [json.loads(line) for line in content.strip().splitlines()]
        assert lines[0]["trace_id"] == "t1"
        assert lines[1]["category"] == "none"


class TestAgreement:
    def test_perfect(self) -> None:
        assert agreement(["a", "b", "c"], ["a", "b", "c"]) == 1.0

    def test_partial(self) -> None:
        assert agreement(["a", "b", "c"], ["a", "b", "x"]) == pytest.approx(2 / 3)

    def test_zero(self) -> None:
        assert agreement(["a"], ["b"]) == 0.0

    def test_empty(self) -> None:
        assert agreement([], []) == 1.0

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            agreement(["a"], ["a", "b"])


class TestEndToEndPipeline:
    def test_pipeline_emits_complete_label_row(self) -> None:
        sink = InMemoryLabelSink()
        c = Classifier(
            sink=sink,
            heuristic_context={"allowed_tools": ["fetch"]},
        )
        tid = uuid4()
        events = [make_tool_call(tid, 0, tool="rogue_tool", args={"q": "x"})]
        trace = make_trace(events=events)
        c.classify(trace)
        row = sink.rows[0]
        assert row["trace_id"] == str(trace.trace_id)
        assert row["category"] == "tool_call_error"
        assert row["source"] == "heuristic"
        assert "labeled_at" in row
        assert "tool_call_error" in row["candidates"]
