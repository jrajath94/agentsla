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

from agentsla.bench import bench_main, real_llm_main, report_main, seeded_main
from agentsla.cli import replay as replay_mod
from agentsla.cli import run as run_mod


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print(
            "usage: agentsla {run,replay,bench,bench-seeded-errors,bench-real,report} ...",
            file=sys.stderr,
        )
        return 1
    cmd, *rest = argv
    if cmd == "run":
        return run_mod.main(rest)
    if cmd == "replay":
        return replay_mod.main(rest)
    if cmd == "bench":
        return bench_main(rest)
    if cmd == "bench-seeded-errors":
        return seeded_main(rest)
    if cmd == "bench-real":
        return real_llm_main(rest)
    if cmd == "report":
        return report_main(rest)
    print(f"unknown subcommand: {cmd!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
