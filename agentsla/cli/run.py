"""CLI smoke entrypoint — runs RawLoopAdapter on the demo task and prints the trace id.

Mirrors the documented acceptance path:

    agentsla run demo
    agentsla replay <trace_id>

The adapter is hermetic (EchoModel + JsonEchoTool) so the run is reproducible
without a network/LLM endpoint. The trace id is printed so the bench harness
(Phase 5) and reviewers can replay exactly the same trace.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import JsonEchoTool


def _default_db() -> Path:
    return Path.cwd() / ".agentsla" / "traces.duckdb"


def run_demo(*, db_path: Path, task_text: str = "hello-agentsla") -> str:
    """Run the demo, persist events, return the trace id (hex)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    writer = TraceWriter(db_path)
    try:
        adapter = RawLoopAdapter(
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text=task_text,
        )
        result = adapter.run(task_id="demo", hooks=NoOpHooks())
    finally:
        writer.close()
    return str(result.trace.trace_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentsla-run", description="Run the AgentSLA demo.")
    parser.add_argument("--db", type=Path, default=_default_db(), help="DuckDB path for trace store.")
    parser.add_argument("--text", default="hello-agentsla", help="Task text passed to the EchoModel.")
    args = parser.parse_args(argv)
    trace_id = run_demo(db_path=args.db, task_text=args.text)
    payload = {"trace_id": trace_id, "db": str(args.db)}
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
