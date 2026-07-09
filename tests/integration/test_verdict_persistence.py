"""Integration test: bench harness persists Verdict events to the trace store.

Closes the gap that motivated Commit 4 of docs/EXECUTION.md: the WrappedHooks
in :mod:`agentsla.bench.harness` previously bypassed :class:`VerificationGate`
and called :class:`VerificationChain.run` directly. The Verdict event was
never written, so ``TraceReader.iter_events(trace_id)`` returned zero verdicts
for wrapped runs. After this test + the harness change, wrapped runs emit
>=1 Verdict event; naked runs emit zero.

Hermetic: uses the same EchoModel + JsonEchoTool path as ``make bench``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import EchoModel, RawLoopAdapter
from agentsla.bench.harness import WrappedHooks
from agentsla.bench.tasks import load_tasks
from agentsla.core.trace import TraceReader, TraceWriter
from agentsla.tools.deterministic import JsonEchoTool


def _verdict_count(db_path: Path) -> int:
    """Count Verdict rows in the trace store, regardless of trace_id."""
    with TraceReader(db_path) as reader:
        total = 0
        for summary in reader.list_traces():
            for event in reader.iter_events(summary.trace_id):
                if event.kind == "verdict":
                    total += 1
        return total


def test_wrapped_run_persists_at_least_one_verdict_event() -> None:
    """WrappedHooks routes the chain through VerificationGate; >=1 Verdict lands in DuckDB."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db_path = tmp / "trace.duckdb"
        labels_path = tmp / "labels.jsonl"

        tasks = load_tasks(include_injection=False)
        assert tasks, "fixture tasks must not be empty"

        writer = TraceWriter(db_path)
        try:
            hooks = WrappedHooks(writer, label_sink_path=labels_path)
            adapter = RawLoopAdapter(
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                echo_model=EchoModel(),
                task_text=tasks[0].text,
            )
            adapter.run(task_id=tasks[0].task_id, hooks=hooks)
        finally:
            writer.close()

        assert _verdict_count(db_path) >= 1, (
            "WrappedHooks.on_final_answer must run VerificationGate, which appends a "
            "Verdict event. Zero verdict rows means the gate is still bypassed."
        )


def test_naked_run_writes_zero_verdict_events() -> None:
    """NoOpHooks writes nothing; naked runs must not leak Verdict events."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db_path = tmp / "trace.duckdb"

        tasks = load_tasks(include_injection=False)
        assert tasks, "fixture tasks must not be empty"

        writer = TraceWriter(db_path)
        try:
            adapter = RawLoopAdapter(
                tools={"json_echo": JsonEchoTool()},
                trace_writer=writer,
                echo_model=EchoModel(),
                task_text=tasks[0].text,
            )
            adapter.run(task_id=tasks[0].task_id, hooks=NoOpHooks())
        finally:
            writer.close()

        assert _verdict_count(db_path) == 0, (
            "NoOpHooks must not emit Verdict events; naked runs are the no-relay baseline."
        )
