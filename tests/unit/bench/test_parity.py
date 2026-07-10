"""Unit tests for the cross-adapter parity bench.

Pins:
  * ``_aggregate_parity`` computes paired agreement on success + n_events.
  * ``render_parity_section`` emits the markdown contract ``agentsla report``
    expects (header + table + agreement line).
"""

from __future__ import annotations

from agentsla.bench.parity import (
    ParityRow,
    _aggregate_parity,
    render_parity_section,
)


def _row(adapter: str, task_id: str, seed: int, *, success: bool, n_events: int, latency_ms: float = 5.0) -> ParityRow:
    return ParityRow(
        adapter=adapter,
        task_id=task_id,
        domain="financial_ops",
        seed=seed,
        success=success,
        n_events=n_events,
        n_allow=1,
        n_deny=0,
        latency_ms=latency_ms,
    )


def test_aggregate_parity_empty_rows_is_safe() -> None:
    agg = _aggregate_parity([])
    assert agg["paired_n"] == 0
    assert agg["success_agreement"] == 1.0
    assert agg["events_agreement"] == 1.0


def test_aggregate_parity_full_agreement() -> None:
    rows = [
        _row("rawloop", "t1", 0, success=True, n_events=4),
        _row("langgraph", "t1", 0, success=True, n_events=4),
        _row("rawloop", "t2", 0, success=False, n_events=2),
        _row("langgraph", "t2", 0, success=False, n_events=2),
    ]
    agg = _aggregate_parity(rows)
    assert agg["paired_n"] == 2
    assert agg["success_agreement"] == 1.0
    assert agg["events_agreement"] == 1.0
    assert agg["rawloop_success"] == 1  # 1 of 2 succeeded
    assert agg["langgraph_success"] == 1


def test_aggregate_parity_detects_success_divergence() -> None:
    rows = [
        _row("rawloop", "t1", 0, success=True, n_events=4),
        _row("langgraph", "t1", 0, success=False, n_events=4),  # disagrees
        _row("rawloop", "t2", 0, success=True, n_events=4),
        _row("langgraph", "t2", 0, success=True, n_events=4),
    ]
    agg = _aggregate_parity(rows)
    assert agg["success_agreement"] == 0.5
    # n_events still agrees
    assert agg["events_agreement"] == 1.0


def test_aggregate_parity_detects_event_count_divergence() -> None:
    rows = [
        _row("rawloop", "t1", 0, success=True, n_events=4),
        _row("langgraph", "t1", 0, success=True, n_events=5),  # 1 extra event
    ]
    agg = _aggregate_parity(rows)
    assert agg["events_agreement"] == 0.0
    assert agg["success_agreement"] == 1.0


def test_render_parity_section_emits_header_table_and_agreement() -> None:
    rows = [
        _row("rawloop", "t1", 0, success=True, n_events=4),
        _row("langgraph", "t1", 0, success=True, n_events=4),
    ]
    agg = _aggregate_parity(rows)
    md = render_parity_section(agg, source_parquet=__file__)  # path used as label
    assert "## Cross-adapter parity (rawloop vs langgraph)" in md
    assert "| rawloop |" in md
    assert "| langgraph |" in md
    assert "**Success agreement:** 100%" in md
    assert "**Event-count agreement:** 100%" in md
    assert "**Paired runs:** 1" in md
