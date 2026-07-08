"""Integration smoke: ``agentsla run`` + 5x ``agentsla replay`` -> byte-identical answer."""

from __future__ import annotations

from pathlib import Path

from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.core.replay import ReplayEngine, ReplayMode
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import JsonEchoTool


def test_5_replay_byte_identical(tmp_path: Path) -> None:
    """Record one rawloop trace, replay it 5 times, final answer must match."""
    db = tmp_path / "traces.duckdb"
    writer = TraceWriter(db)
    try:
        adapter = RawLoopAdapter(
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text="agentsla-replay-check",
        )
        out = adapter.run(task_id="smoke", hooks=NoOpHooks())
        trace_id = out.trace.trace_id
    finally:
        writer.close()

    # Replay 5 times against the recorded store. Each replay must report
    # exit_code=0 and an identical final answer.
    engine = ReplayEngine(db)
    answers: list[str] = []
    for _ in range(5):
        report = engine.replay(str(trace_id), mode=ReplayMode.STRICT)
        assert report.exit_code == 0
        answers.append(report.final_answer)
    assert len(set(answers)) == 1
