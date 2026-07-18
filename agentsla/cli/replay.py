"""CLI replay entrypoint — structural replay by default, execution replay via --execute.

Default (structural): calls :func:`agentsla.core.replay.replay` — recorded
tool-call hash re-validation + the stored final answer.

``--execute`` (execution): calls
:func:`agentsla.adapters.replay_exec.replay_execution` — re-drives the
adapter loop with recorded tool results stubbed in and compares the
re-produced final answer byte-for-byte. Only rawloop-recorded traces
(deterministic model) are eligible; live-model traces exit 2.

Exit codes (both modes):

    0 — replay passed.
    1 — divergence (structural drift, or execution-replay mismatch).
    2 — trace id not found / trace not execution-replayable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentsla.core.replay import ReplayMode, replay


def _default_db() -> Path:
    return Path.cwd() / ".agentsla" / "traces.duckdb"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentsla-replay", description="Replay a recorded trace.")
    parser.add_argument("trace_id", help="UUID of the trace to replay.")
    parser.add_argument("--db", type=Path, default=_default_db(), help="DuckDB path for trace store.")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in ReplayMode],
        default=ReplayMode.STRICT.value,
        help="strict (raise on drift) or tolerant (ignore). Structural mode only.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Execution replay: re-drive the adapter loop with recorded tool "
            "results stubbed in; compare the final answer byte-for-byte. "
            "Rawloop-recorded (deterministic-model) traces only."
        ),
    )
    args = parser.parse_args(argv)
    if not args.db.exists():
        # Missing trace store — treat as "unknown trace" without opening a
        # read-only connection (DuckDB refuses read-only on non-existent files).
        print(json.dumps({"trace_id": args.trace_id, "found": False}, indent=2))
        return 2
    if args.execute:
        from agentsla.adapters.replay_exec import replay_execution

        exec_report = replay_execution(args.trace_id, args.db)
        print(json.dumps(exec_report.to_json_dict(), indent=2))
        return exec_report.exit_code
    try:
        report = replay(args.trace_id, args.db, mode=ReplayMode(args.mode))
    except KeyError:
        # Trace id was not found in the log. Distinguish from a drift failure.
        print(json.dumps({"trace_id": args.trace_id, "found": False}, indent=2))
        return 2
    print(json.dumps(report.model_dump(mode="json"), indent=2))
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
