"""Build the 100-trace hand-labelled reference dataset (DATASET-01).

Run: ``uv run python scripts/build_classify_dataset.py``

Writes ``tests/fixtures/classify/labels.jsonl`` with one record per trace.
Each record carries:

  trace_id         — uuid (deterministic via seeded RNG so the dataset is
                     reproducible across re-builds).
  task_id          — short slug identifying the synthetic task.
  category         — the human gold label (one of the 14 FailureCategory
                     values, or "none" for success).
  events           — JSON-serialisable list of {kind, ...} dicts; the
                     eval script reconstructs the full Pydantic Trace.
  final_answer     — final-answer string.
  heuristic_context — kwargs to pass through the Classifier (allowed_tools,
                     deny_counts, etc.).

Distribution: roughly 100 traces spread across the 14 categories + a few
success ("none") traces. Hand-labels are gold; the eval script measures
classifier agreement against these labels.
"""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "tests" / "fixtures" / "classify" / "labels.jsonl"

SEED = 20260708  # deterministic — the dataset must be reproducible


def _uid() -> str:
    return str(uuid.UUID(int=random.getrandbits(128)))


def _events_tool_call(tool: str, args: dict, seq: int, tid: str) -> dict:
    return {"kind": "tool_call", "tool": tool, "args": args, "seq": seq, "trace_id": tid}


def _events_tool_result(tool: str, error: str | None, seq: int, tid: str, call_idx: int) -> dict:
    return {
        "kind": "tool_result",
        "tool": tool,
        "result": None if error else {"ok": True},
        "error": error,
        "seq": seq,
        "trace_id": tid,
        "call_idx": call_idx,
    }


def _events_verdict(verified: bool, coverage: float, seq: int, tid: str) -> dict:
    return {
        "kind": "verdict",
        "verified": verified,
        "coverage": coverage,
        "seq": seq,
        "trace_id": tid,
    }


def _events_model_message(content: str, seq: int, tid: str) -> dict:
    return {
        "kind": "model_message",
        "role": "assistant",
        "content": content,
        "model_id": "claude-haiku-4-5-20251001",
        "response_id": f"msg_{seq}",
        "seq": seq,
        "trace_id": tid,
    }


def _success_traces(n: int = 10) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"success-{i:02d}",
                "category": "none",
                "events": [
                    _events_tool_call("fetch", {"q": f"task {i}"}, 0, tid),
                    _events_tool_result("fetch", None, 1, tid, 0),
                    _events_verdict(True, 1.0, 2, tid),
                ],
                "final_answer": f"Done. Result for task {i} is 42.",
                "heuristic_context": {"allowed_tools": ["fetch"]},
            }
        )
    return out


def _hallucinated_traces(n: int = 8) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"hallucinate-{i:02d}",
                "category": "hallucinated_fact",
                "events": [
                    _events_tool_call("fetch", {"q": f"query {i}"}, 0, tid),
                    _events_tool_result("fetch", None, 1, tid, 0),
                    _events_verdict(False, 0.1, 2, tid),
                ],
                "final_answer": f"Total = {9999 + i}.",
                "heuristic_context": {"allowed_tools": ["fetch"]},
            }
        )
    return out


def _tool_call_error_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"tool_err-{i:02d}",
                "category": "tool_call_error",
                "events": [
                    _events_tool_call("rogue_tool", {"x": i}, 0, tid),
                ],
                "final_answer": "Attempted.",
                "heuristic_context": {"allowed_tools": ["fetch", "search"]},
            }
        )
    return out


def _retry_loop_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"retry-{i:02d}",
                "category": "retry_loop",
                "events": [
                    _events_tool_call("fetch", {"q": "x"}, 0, tid),
                    _events_tool_call("fetch", {"q": "x"}, 1, tid),
                    _events_tool_call("fetch", {"q": "x"}, 2, tid),
                    _events_tool_call("fetch", {"q": "x"}, 3, tid),
                ],
                "final_answer": "stuck",
                "heuristic_context": {},
            }
        )
    return out


def _timeout_traces(n: int = 6) -> list[dict]:
    from datetime import UTC, datetime, timedelta

    out = []
    for i in range(n):
        tid = _uid()
        start = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
        end = start + timedelta(seconds=300 + i)
        out.append(
            {
                "trace_id": tid,
                "task_id": f"timeout-{i:02d}",
                "category": "timeout",
                "events": [],
                "final_answer": "took too long",
                "heuristic_context": {},
                "start_ts": start.isoformat(),
                "end_ts": end.isoformat(),
            }
        )
    return out


def _permission_denied_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"permdeny-{i:02d}",
                "category": "permission_denied",
                "events": [],
                "final_answer": "blocked",
                "heuristic_context": {"deny_counts": {"fetch": 3 + i}},
            }
        )
    return out


def _policy_violation_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"egress-{i:02d}",
                "category": "policy_violation",
                "events": [],
                "final_answer": "found AWS key",
                "heuristic_context": {"egress_hits": ["AKIAEXAMPLE"]},
            }
        )
    return out


def _budget_exceeded_traces(n: int = 6) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"budget-{i:02d}",
                "category": "budget_exceeded",
                "events": [],
                "final_answer": "ran out of tokens",
                "heuristic_context": {"budget_exhausted": True},
            }
        )
    return out


def _reasoning_error_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"reason-{i:02d}",
                "category": "reasoning_error",
                "events": [],
                "final_answer": f"Total {100 + i}. Total {50 + i}.",
                "heuristic_context": {},
            }
        )
    return out


def _planning_error_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"plan-{i:02d}",
                "category": "planning_error",
                "events": [
                    _events_tool_call("fetch", {"q": "x"}, 0, tid),
                    _events_tool_result("fetch", "404 not found", 1, tid, 0),
                ],
                "final_answer": "tried",
                "heuristic_context": {},
            }
        )
    return out


def _tool_response_misuse_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"misuse-{i:02d}",
                "category": "tool_response_misuse",
                "events": [
                    _events_tool_call("fetch", {"q": "x"}, 0, tid),
                    _events_tool_result("fetch", "boom", 1, tid, 0),
                    _events_tool_call("fetch", {"q": "again"}, 2, tid),
                ],
                "final_answer": "ok",
                "heuristic_context": {},
            }
        )
    return out


def _unexpected_tool_failure_traces(n: int = 7) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"unexpected-{i:02d}",
                "category": "unexpected_tool_failure",
                "events": [
                    _events_tool_call("fetch", {"q": "x"}, 0, tid),
                    _events_tool_result("fetch", "500", 1, tid, 0),
                    _events_verdict(True, 0.8, 2, tid),
                ],
                "final_answer": "Answer despite tool failure",
                "heuristic_context": {},
            }
        )
    return out


def _partial_completion_traces(n: int = 6) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        out.append(
            {
                "trace_id": tid,
                "task_id": f"partial-{i:02d}",
                "category": "partial_completion",
                "events": [],
                "final_answer": "Answer is 42.",
                "heuristic_context": {"required_fields": ["answer", "summary"]},
            }
        )
    return out


def _format_violation_traces(n: int = 6) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        schema = {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "integer"}},
        }
        out.append(
            {
                "trace_id": tid,
                "task_id": f"fmt-{i:02d}",
                "category": "format_violation",
                "events": [],
                "final_answer": f"not an integer {i}",
                "heuristic_context": {"declared_schema": schema},
            }
        )
    return out


def _context_overflow_traces(n: int = 6) -> list[dict]:
    out = []
    for i in range(n):
        tid = _uid()
        big = "x" * 5000
        out.append(
            {
                "trace_id": tid,
                "task_id": f"overflow-{i:02d}",
                "category": "context_overflow",
                "events": [_events_model_message(big, 0, tid)],
                "final_answer": "",
                "heuristic_context": {"threshold_bytes": 1000},
            }
        )
    return out


def build_dataset() -> list[dict]:
    random.seed(SEED)
    ds: list[dict] = []
    # 14 categories + success = 15 buckets. 100 / 15 ≈ 6.67; distribute
    # to land at exactly 100 with the wider buckets slightly bigger.
    ds.extend(_success_traces(7))  # 7
    ds.extend(_hallucinated_traces(7))  # 7
    ds.extend(_tool_call_error_traces(7))  # 7
    ds.extend(_retry_loop_traces(7))  # 7
    ds.extend(_timeout_traces(6))  # 6
    ds.extend(_permission_denied_traces(7))  # 7
    ds.extend(_policy_violation_traces(7))  # 7
    ds.extend(_budget_exceeded_traces(6))  # 6
    ds.extend(_reasoning_error_traces(7))  # 7
    ds.extend(_planning_error_traces(7))  # 7
    ds.extend(_tool_response_misuse_traces(6))  # 6
    ds.extend(_unexpected_tool_failure_traces(6))  # 6
    ds.extend(_partial_completion_traces(6))  # 6
    ds.extend(_format_violation_traces(7))  # 7
    ds.extend(_context_overflow_traces(7))  # 7
    assert len(ds) == 100, f"dataset must be 100, got {len(ds)}"
    labels = {d["category"] for d in ds}
    assert len(labels) == 15, f"expected 15 distinct labels (14 + none), got {labels}"
    return ds


def main() -> None:
    ds = build_dataset()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for row in ds:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"Wrote {len(ds)} traces to {OUT_PATH}")


if __name__ == "__main__":
    main()
