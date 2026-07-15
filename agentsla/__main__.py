"""Unified ``agentsla`` CLI — dispatches to subcommands.

Usage:
    python -m agentsla run [--db PATH] [--text TASK]
    python -m agentsla replay TRACE_ID [--db PATH] [--mode strict|tolerant]
    python -m agentsla bench [--all] [--seeds N] [--out PATH]
    python -m agentsla bench-seeded-errors [--strategies 0,10,50,100] [--trials N]
    python -m agentsla bench-real [--model M] [--tasks-per-domain N] [--out PATH]
    python -m agentsla report [--in PATH] [--out PATH]
"""

from __future__ import annotations

import sys

from agentsla.cli import metrics as metrics_mod
from agentsla.cli import replay as replay_mod
from agentsla.cli import run as run_mod


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print(
            "usage: agentsla {run,replay,metrics,bench,bench-seeded-errors,bench-real,report} ...",
            file=sys.stderr,
        )
        return 1
    cmd, *rest = argv
    if cmd == "run":
        return run_mod.main(rest)
    if cmd == "replay":
        return replay_mod.main(rest)
    if cmd == "metrics":
        return metrics_mod.main(rest)
    if cmd == "bench":
        from agentsla.bench import bench_main

        return bench_main(rest)
    if cmd == "bench-seeded-errors":
        from agentsla.bench import seeded_main

        return seeded_main(rest)
    if cmd == "bench-real":
        from agentsla.bench import real_llm_main

        return real_llm_main(rest)
    if cmd == "report":
        from agentsla.bench import report_main

        return report_main(rest)
    print(f"unknown subcommand: {cmd!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
