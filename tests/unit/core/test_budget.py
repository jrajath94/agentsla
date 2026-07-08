"""BudgetManager — threshold + degradation level tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agentsla.core.budget import (
    BudgetExceededError,
    BudgetManager,
    BudgetSpec,
    DegradationLevel,
)
from agentsla.core.events import ToolResult, now_timestamp
from agentsla.core.types import new_call_id, new_trace_id


def _result(latency_ms: float = 0.0) -> ToolResult:
    return ToolResult(
        call_id=new_call_id(),
        tool="x",
        result=None,
        is_error=False,
        error=None,
        latency_ms=latency_ms,
        trace_id=new_trace_id(),
        seq=0,
        ts=now_timestamp(),
    )


def test_init_defaults() -> None:
    m = BudgetManager()
    assert m.spec.max_tokens == 50_000
    assert m.spec.max_calls == 50


def test_call_count_limit_raises() -> None:
    spec = BudgetSpec(max_calls=2)
    m = BudgetManager(spec)
    tid = "trace-1"
    m.record_call(tid)
    m.record_call(tid)
    with pytest.raises(BudgetExceededError) as exc:
        m.record_call(tid)
    assert exc.value.metric == "calls"
    assert exc.value.level == DegradationLevel.EMERGENCY


def test_tokens_limit_raises() -> None:
    spec = BudgetSpec(max_tokens=100, max_cost_usd=1.00)
    m = BudgetManager(spec)
    tid = "trace-1"
    m.record_tool_result(tid, _result(), tokens_used=99)  # at ceiling
    with pytest.raises(BudgetExceededError) as exc:
        m.record_tool_result(tid, _result(), tokens_used=2)
    assert exc.value.metric == "tokens"


def test_cost_limit_raises() -> None:
    spec = BudgetSpec(max_cost_usd=0.10)
    m = BudgetManager(spec)
    tid = "trace-1"
    with pytest.raises(BudgetExceededError) as exc:
        m.record_tool_result(tid, _result(), cost_usd=0.50)
    assert exc.value.metric == "cost_usd"


def test_degradation_levels_transition() -> None:
    spec = BudgetSpec(max_tokens=100, max_cost_usd=1.00)
    m = BudgetManager(spec)
    tid = "trace-1"
    # 0 / 100 -> FULL
    assert m.level(tid) == DegradationLevel.FULL
    # 60 / 100 -> REDUCED
    m.record_tool_result(tid, _result(), tokens_used=60)
    assert m.level(tid) == DegradationLevel.REDUCED
    # 80 / 100 -> MINIMAL
    m.record_tool_result(tid, _result(), tokens_used=20)
    assert m.level(tid) == DegradationLevel.MINIMAL
    # 95 / 100 -> EMERGENCY
    m.record_tool_result(tid, _result(), tokens_used=15)
    assert m.level(tid) == DegradationLevel.EMERGENCY


def test_snapshot_returns_aggregates() -> None:
    m = BudgetManager()
    tid = "trace-1"
    m.record_call(tid)
    m.record_tool_result(tid, _result(), tokens_used=100, cost_usd=0.01)
    s = m.snapshot(tid)
    assert s["calls"] == 1
    assert s["tokens"] == 100
    assert s["cost_usd"] == 0.01
    assert s["level"] == "full"


def test_wall_time_triggers_when_latency_reported() -> None:
    """Wall-time guard uses reported latency_ms combined with run start."""
    spec = BudgetSpec(max_wall_time=timedelta(milliseconds=10))
    m = BudgetManager(spec)
    tid = "trace-1"
    m.start()
    # The recorded start + 50 ms latency exceeds the 10 ms ceiling.
    with pytest.raises(BudgetExceededError) as exc:
        m.record_tool_result(tid, _result(latency_ms=50.0))
    assert exc.value.metric == "wall_time_ms"


def test_iter_breaches_yields_in_order() -> None:
    from agentsla.core.budget import iter_breaches

    seq = list(iter_breaches(BudgetSpec()))
    assert seq == [
        DegradationLevel.REDUCED,
        DegradationLevel.MINIMAL,
        DegradationLevel.EMERGENCY,
    ]
