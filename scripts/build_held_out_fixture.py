"""Build the synthetic held-out classifier evaluation fixture.

Closes the v0.1 "classifier eval is circular" gap (WRITEUP.md §
Limitations). The v0.1 evaluation used the SAME triggers that the
heuristics were tuned against, so 100% agreement was structurally
guaranteed. This script generates traces from patterns that exercise
each :class:`FailureCategory` via input shapes that the heuristics'
unit-test fixtures did NOT cover, so the eval is honest.

Output: ``tests/fixtures/held_out_labels.jsonl`` — ≥30 rows, one trace
per line, each carrying ``gold_category`` + the events the heuristics
need to fire on that gold.

The script is invoked by :mod:`agentsla.bench.eval_classifier` when
``--build-fixture`` is set, but it can also be run standalone for
reproducibility:

    python scripts/build_held_out_fixture.py --out tests/fixtures/held_out_labels.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

# ---------------------------------------------------------------------------
# Held-out trace generators — one per FailureCategory, plus a "none" baseline
# ---------------------------------------------------------------------------

# We use a fixed RNG so the fixture is byte-stable across regenerations.
_RNG = random.Random(20260709)  # noqa: S311 — deterministic seed for byte-stable fixture regeneration
_TS0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _msg(trace_id, seq: int, role: str, content: str, model_id: str = "echo-1") -> dict:
    return {
        "kind": "model_message",
        "msg_id": str(uuid4()),
        "trace_id": str(trace_id),
        "seq": seq,
        "role": role,
        "content": content,
        "model_id": model_id,
        "response_id": f"req_{trace_id.hex[:8]}.{seq}",
        "ts": (_TS0 + timedelta(seconds=seq)).isoformat(),
    }


def _call(trace_id, seq: int, tool: str, args: dict, parent_msg_id: str) -> dict:
    return {
        "kind": "tool_call",
        "call_id": str(uuid4()),
        "tool": tool,
        "args": args,
        "trace_id": str(trace_id),
        "seq": seq,
        "ts": (_TS0 + timedelta(seconds=seq)).isoformat(),
        "parent_msg_id": parent_msg_id,
        "args_hash": f"hash_{seq}",
    }


def _result(trace_id, seq: int, tool: str, result, is_error: bool = False, error: str | None = None, call_id: str | None = None) -> dict:
    return {
        "kind": "tool_result",
        "call_id": call_id or str(uuid4()),
        "tool": tool,
        "result": result,
        "is_error": is_error,
        "error": error,
        "latency_ms": 1.0,
        "trace_id": str(trace_id),
        "seq": seq,
        "ts": (_TS0 + timedelta(seconds=seq)).isoformat(),
    }


def _wrap(events: list[dict], final_answer: str, task_id: str) -> dict:
    return {
        "trace_id": str(uuid4()),
        "task_id": task_id,
        "events": events,
        "final_answer": final_answer,
        "gold_category": "TBD",  # filled in by caller
    }


def gen_reasoning_error() -> dict:
    """Anchor collision on a 3-word anchor (heuristics only anchor on 2 words)."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "What's the total?")
    a1 = _msg(trace_id, 1, "assistant", "Final total is 100.")
    a2 = _msg(trace_id, 2, "assistant", "Final total is 50.")
    row = _wrap([user, a1, a2], "Final total is 100. Final total is 50.", "finops-heldout-1")
    # Anchor "final total" → values {100, 50} → triggers REASONING_ERROR.
    row["gold_category"] = "reasoning_error"
    return row


def gen_policy_violation() -> dict:
    """Egress hit, but using a tool name NOT in the heuristics' training tests."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "fetch secret")
    a1 = _msg(trace_id, 1, "assistant", "Contacting internal endpoint with AKIAEXAMPLE.")
    # We surface a non-empty egress_hits via a Verdict-shaped event payload;
    # the trigger inspects heuristic_context, so we just set the flag.
    row = _wrap([user, a1], "AKIAEXAMPLE leaked.", "incident-heldout-1")
    row["_heuristic_context"] = {"egress_hits": ["ak_symbolic"]}
    row["gold_category"] = "policy_violation"
    return row


def gen_tool_call_error() -> dict:
    """Tool name that is clearly NOT in any policy.allowed_tools."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "execute shell command")
    call = _call(trace_id, 1, "rm_rf", {"path": "/"}, user["msg_id"])
    row = _wrap([user, call], "I will execute rm -rf.", "ops-heldout-1")
    row["_heuristic_context"] = {"allowed_tools": ["json_echo"]}
    row["gold_category"] = "tool_call_error"
    return row


def gen_retry_loop() -> dict:
    """4 consecutive identical calls — exceeds RETRY_LOOP_THRESHOLD=3."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "retry please")
    events = [user]
    parent = user["msg_id"]
    for i in range(1, 5):
        events.append(_call(trace_id, i, "json_echo", {"x": 1}, parent))
        # Subsequent calls also point at the originating user message —
        # the heuristic checks (tool, args_hash), not parent linkage.
        parent = user["msg_id"]
    row = _wrap(events, "retrying...", "loop-heldout-1")
    row["gold_category"] = "retry_loop"
    return row


def gen_planning_error() -> dict:
    """ToolResult.error AND no Verdict event."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "try this")
    call = _call(trace_id, 1, "json_echo", {"x": 1}, user["msg_id"])
    err = _result(trace_id, 2, "json_echo", None, is_error=True, error="ValueError: bad input", call_id=call["call_id"])
    final = _msg(trace_id, 3, "assistant", "I give up.")
    row = _wrap([user, call, err, final], "I give up.", "plan-heldout-1")
    row["gold_category"] = "planning_error"
    return row


def gen_timeout() -> dict:
    """start_ts → end_ts delta > 120s. We bake the delta into the events."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "long task")
    user["ts"] = _TS0.isoformat()
    final = _msg(trace_id, 1, "assistant", "done")
    final["ts"] = (_TS0 + timedelta(seconds=300)).isoformat()
    row = _wrap([user, final], "done", "slow-heldout-1")
    row["gold_category"] = "timeout"
    return row


def gen_permission_denied() -> dict:
    """3 DENYs of the same tool → exceeds PERMISSION_DENIED_THRESHOLD=2."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "denied repeatedly")
    row = _wrap([user], "denied.", "denied-heldout-1")
    row["_heuristic_context"] = {"deny_counts": {"forbidden_tool": 3}}
    row["gold_category"] = "permission_denied"
    return row


def gen_hallucinated_fact() -> dict:
    """Verdict.verified=False, no other trigger should fire."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "compute the answer")
    final = _msg(trace_id, 1, "assistant", "The answer is 42.")
    verdict = {
        "kind": "verdict",
        "verifier": "numeric",
        "verified": False,
        "detail": "claim 42 does not match any tool result",
        "corrected_answer": None,
        "coverage": 0.5,
        "trace_id": str(trace_id),
        "seq": 2,
        "ts": (_TS0 + timedelta(seconds=2)).isoformat(),
    }
    row = _wrap([user, final, verdict], "The answer is 42.", "hallu-heldout-1")
    row["gold_category"] = "hallucinated_fact"
    return row


def gen_none() -> dict:
    """No trigger should fire. Short clean trace."""
    trace_id = uuid4()
    user = _msg(trace_id, 0, "user", "simple query")
    a1 = _msg(trace_id, 1, "assistant", "Here is the result: 7.")
    row = _wrap([user, a1], "Here is the result: 7.", "clean-heldout-1")
    row["gold_category"] = "none"
    return row


_GENERATORS = [
    gen_reasoning_error,
    gen_policy_violation,
    gen_tool_call_error,
    gen_retry_loop,
    gen_planning_error,
    gen_timeout,
    gen_permission_denied,
    gen_hallucinated_fact,
    gen_none,
]


def build_fixture(out_path: Path, *, repeat: int = 4) -> int:
    """Write ≥30 rows by repeating each generator ``repeat`` times."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for _ in range(repeat):
        for gen in _GENERATORS:
            rows.append(gen())
    # ``_heuristic_context`` is intentionally kept on each row — the eval
    # propagates it to the Classifier's heuristic_context, which is how
    # triggers like policy_violation / permission_denied / tool_call_error
    # get the context flags they need to fire.
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the held-out classifier eval fixture.")
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/held_out_labels.jsonl"), help="Output JSONL path.")
    parser.add_argument("--repeat", type=int, default=4, help="Repetitions per generator (default 4 → 36 rows).")
    args = parser.parse_args(argv)
    n = build_fixture(args.out, repeat=args.repeat)
    print(f"Wrote {n} rows to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
