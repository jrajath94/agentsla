"""Classifier orchestrator — heuristic stage, optional LLM-judge stage,
label emission, Prometheus counter increment.

Pipeline (per docs/class-taxonomy.md §Heuristic Triggers + §LLM-Judge Stage):

    1. Run all 14 heuristic triggers against the trace.
    2. Apply ``taxonomy.rank`` to pick the winning category from candidates.
    3. If the orchestrator decides a judge is needed (≤20% of traces),
       invoke the judge and use its output instead.
    4. Increment the Prometheus counter with the chosen category.
    5. Append a JSONL label row to the labels sink (passed in by the bench
       harness — the orchestrator does not own file I/O).

The classifier is in-band inside :func:`on_final_answer` of the adapter; the
adapter passes the trace + verdict (None or populated) and the orchestrator
returns a :class:`ClassificationResult`.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agentsla.classify.heuristics import HEURISTIC_TRIGGERS
from agentsla.classify.judge import (
    Judge,
    JudgeResult,
    StubJudge,
    should_invoke_judge,
    summarise_events_for_judge,
)
from agentsla.classify.taxonomy import FailureCategory, rank
from agentsla.core.events import Trace


@dataclass
class ClassificationResult:
    """One classifier invocation."""

    trace_id: str
    category: FailureCategory | None
    confidence: float
    source: str  # "heuristic" | "llm_judge" | "none"
    candidates: list[FailureCategory] = field(default_factory=list)
    judge_prompt_hash: str | None = None
    labeled_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class LabelSink(Protocol):
    """Pluggable sink for label rows.

    Default impl :class:`JsonlLabelSink` writes to a file. Tests use
    :class:`InMemoryLabelSink` to avoid disk I/O.
    """

    def append(self, row: dict[str, Any]) -> None: ...


class InMemoryLabelSink:
    """Capture labels in a list (for tests)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append(self, row: dict[str, Any]) -> None:
        self.rows.append(row)


class JsonlLabelSink:
    """Append labels to a JSONL file (production default)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


HeuristicContext = dict[str, Any]


class Classifier:
    """Orchestrates heuristic + LLM-judge stages and emits labels."""

    def __init__(
        self,
        *,
        judge: Judge | None = None,
        sink: LabelSink | None,
        on_classify: Callable[[ClassificationResult], None] | None = None,
        heuristic_context: HeuristicContext | None = None,
    ) -> None:
        self.judge = judge or StubJudge()
        self.sink = sink
        self.on_classify = on_classify
        self.heuristic_context = heuristic_context or {}

    def classify(
        self,
        trace: Trace,
        *,
        verification_incorrect: int = 0,
    ) -> ClassificationResult:
        """Run heuristic stage, optionally invoke judge, emit label."""
        # --- 1. Heuristic stage ---
        candidates: list[FailureCategory] = []
        for trigger in HEURISTIC_TRIGGERS:
            try:
                # Filter kwargs to those the trigger's signature actually
                # accepts. This lets one Classifier carry a wide context
                # dict (e.g. ``{"allowed_tools": ..., "deny_counts": ...,
                # "threshold_bytes": ...}``) without each trigger having
                # to declare ``**_``.
                sig = inspect.signature(trigger)
                kwargs = {k: v for k, v in self.heuristic_context.items() if k in sig.parameters}
                result = trigger(trace, **kwargs)
            except TypeError:
                # Trigger does not accept the current context — skip silently.
                continue
            if result is not None:
                candidates.append(result)

        winning = rank(candidates)
        heuristic_confidence = 1.0 if winning is not None else 0.0

        # --- 2. Judge stage ---
        judge_result: JudgeResult | None = None
        source = "heuristic"
        if should_invoke_judge(
            heuristic_candidates=candidates,
            heuristic_confidence=heuristic_confidence,
            verification_incorrect=verification_incorrect,
        ):
            judge_result = self.judge.classify(
                trace_id=str(trace.trace_id),
                task_id=trace.task_id,
                final_answer=trace.final_answer,
                event_summary=summarise_events_for_judge(list(trace.events)),
            )
            source = "llm_judge"
            if judge_result.category is not None:
                winning = judge_result.category
                heuristic_confidence = judge_result.confidence

        # --- 3. Build result ---
        result = ClassificationResult(
            trace_id=str(trace.trace_id),
            category=winning,
            confidence=heuristic_confidence,
            source=source,
            candidates=candidates,
            judge_prompt_hash=judge_result.prompt_hash if judge_result else None,
        )

        # --- 4. Emit label ---
        if self.sink is not None:
            self.sink.append(_to_label_row(result))
        # --- 5. Side-effects (metrics etc.) ---
        if self.on_classify is not None:
            self.on_classify(result)

        return result


def _to_label_row(result: ClassificationResult) -> dict[str, Any]:
    return {
        "trace_id": result.trace_id,
        "category": result.category.value if result.category else "none",
        "confidence": result.confidence,
        "source": result.source,
        "judge_prompt_hash": result.judge_prompt_hash,
        "labeled_at": result.labeled_at,
        "candidates": [c.value for c in result.candidates],
    }


def agreement(predicted: list[str], gold: list[str]) -> float:
    """Fraction of traces where predicted == gold. Phase 4 acceptance: ≥0.80."""
    if len(predicted) != len(gold):
        raise ValueError("length mismatch")
    if not predicted:
        return 1.0
    return sum(1 for p, g in zip(predicted, gold, strict=True) if p == g) / len(predicted)


__all__ = [
    "ClassificationResult",
    "Classifier",
    "HeuristicContext",
    "InMemoryLabelSink",
    "JsonlLabelSink",
    "LabelSink",
    "agreement",
]
