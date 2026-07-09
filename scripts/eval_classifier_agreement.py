"""Evaluate the classifier against the 100-trace hand-labelled dataset.

Run: ``uv run python scripts/eval_classifier_agreement.py``

Phase 4 acceptance: agreement ≥ 80%.

The script:

  1. Loads ``tests/fixtures/classify/labels.jsonl``.
  2. Reconstructs a Pydantic :class:`Trace` for each row.
  3. Runs :class:`Classifier` (with the stub judge) over each trace.
  4. Computes per-category agreement + overall agreement.
  5. Emits a markdown table on stdout.

Exits non-zero when agreement < 0.80 so CI can gate on it.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = REPO_ROOT / "tests" / "fixtures" / "classify" / "labels.jsonl"
THRESHOLD = 0.80

sys.path.insert(0, str(REPO_ROOT))

from agentsla.classify import (  # noqa: E402
    Classifier,
    FailureCategory,
    InMemoryLabelSink,
    StubJudge,
    agreement,
)
from agentsla.core.events import (  # noqa: E402
    ModelMessage,
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
)


def _parse_ts(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _rebuild_trace(row: dict[str, Any]) -> Trace:
    """Reconstruct a Trace from a JSONL row."""
    tid = UUID(row["trace_id"])
    events: list[Any] = []
    seq = 0
    for ev_spec in row.get("events", []):
        kind = ev_spec["kind"]
        if kind == "tool_call":
            events.append(
                ToolCall(
                    call_id=UUID(int=hash(ev_spec["tool"]) & 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF),
                    tool=ev_spec["tool"],
                    args=ev_spec.get("args", {}),
                    trace_id=tid,
                    seq=ev_spec.get("seq", seq),
                    args_hash="0" * 64,
                )
            )
        elif kind == "tool_result":
            events.append(
                ToolResult(
                    call_id=UUID(int=hash(ev_spec.get("call_idx", 0)) & 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF),
                    tool=ev_spec["tool"],
                    result=ev_spec.get("result"),
                    is_error=bool(ev_spec.get("error")),
                    error=ev_spec.get("error"),
                    trace_id=tid,
                    seq=ev_spec.get("seq", seq),
                )
            )
        elif kind == "model_message":
            events.append(
                ModelMessage(
                    msg_id=UUID(int=hash(ev_spec["seq"]) & 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF),
                    trace_id=tid,
                    seq=ev_spec.get("seq", seq),
                    role=ev_spec.get("role", "assistant"),
                    content=ev_spec.get("content", ""),
                    model_id=ev_spec.get("model_id", "claude-haiku-4-5-20251001"),
                    response_id=ev_spec.get("response_id", "msg_test"),
                )
            )
        elif kind == "verdict":
            events.append(
                Verdict(
                    verdict_id=UUID(int=hash(ev_spec["seq"]) & 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF),
                    trace_id=tid,
                    seq=ev_spec.get("seq", seq),
                    verifier="numeric",
                    verified=bool(ev_spec.get("verified", True)),
                    coverage=float(ev_spec.get("coverage", 1.0)),
                )
            )
        seq += 1

    start_ts = _parse_ts(row.get("start_ts")) or (events[0].ts if events else datetime.now(UTC))
    end_ts = _parse_ts(row.get("end_ts")) or (events[-1].ts if events else start_ts)
    return Trace(
        trace_id=tid,
        task_id=row.get("task_id", "demo"),
        model_id="claude-haiku-4-5-20251001",
        events=events,
        final_answer=row.get("final_answer", ""),
        start_ts=start_ts,
        end_ts=end_ts,
    )


def main() -> int:
    if not DATASET_PATH.exists():
        print(f"Dataset not found: {DATASET_PATH}", file=sys.stderr)
        print("Run scripts/build_classify_dataset.py first.", file=sys.stderr)
        return 2

    rows = [json.loads(l) for l in DATASET_PATH.read_text().splitlines() if l.strip()]
    print(f"Loaded {len(rows)} hand-labelled traces from {DATASET_PATH}")

    sink = InMemoryLabelSink()
    classifier = Classifier(judge=StubJudge(), sink=sink)

    predicted: list[str] = []
    gold: list[str] = []
    per_category_correct: dict[str, int] = {}
    per_category_total: dict[str, int] = {}

    for row in rows:
        trace = _rebuild_trace(row)
        ctx = row.get("heuristic_context", {})
        # Per-row classifier to inject the row's context.
        row_classifier = Classifier(judge=StubJudge(), sink=sink, heuristic_context=ctx)
        result = row_classifier.classify(trace)
        pred = result.category.value if result.category else "none"
        gold_label = row["category"]

        predicted.append(pred)
        gold.append(gold_label)
        per_category_total[gold_label] = per_category_total.get(gold_label, 0) + 1
        if pred == gold_label:
            per_category_correct[gold_label] = per_category_correct.get(gold_label, 0) + 1

    overall = agreement(predicted, gold)
    print()
    print(f"Overall agreement: {overall:.2%} ({sum(p == g for p, g in zip(predicted, gold))}/{len(gold)})")
    print(f"Threshold: {THRESHOLD:.0%}")
    print()
    print("Per-category agreement:")
    print("| Category                | Correct | Total | Agreement |")
    print("|-------------------------|---------|-------|-----------|")
    for cat in sorted(per_category_total):
        c = per_category_correct.get(cat, 0)
        t = per_category_total[cat]
        pct = c / t if t else 0.0
        print(f"| {cat:<23} | {c:>7} | {t:>5} | {pct:>9.0%} |")
    print()
    # LLM-judge invocation rate
    judge_count = sum(1 for row in sink.rows if row["source"] == "llm_judge")
    print(f"LLM-judge invocations: {judge_count} / {len(rows)} ({judge_count / len(rows):.1%})")

    return 0 if overall >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())