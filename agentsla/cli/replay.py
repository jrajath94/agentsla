"""CLI replay entrypoint — verifies a recorded trace's args match expectations.

Calls :func:`agentsla.core.replay.replay` against the database path and reports
the result. Exit codes:

    0 — replay passed (strict or tolerant).
    1 — strict-mode drift detected.
    2 — trace id not found.
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
        help="strict (raise on drift) or tolerant (ignore).",
    )
    args = parser.parse_args(argv)
    if not args.db.exists():
        # Missing trace store — treat as "unknown trace" without opening a
        # read-only connection (DuckDB refuses read-only on non-existent files).
        print(json.dumps({"trace_id": args.trace_id, "found": False}, indent=2))
        return 2
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
