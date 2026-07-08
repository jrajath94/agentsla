"""Unified ``agentsla`` CLI — dispatches to subcommands.

Usage:
    python -m agentsla run [--db PATH] [--text TASK]
    python -m agentsla replay TRACE_ID [--db PATH] [--mode strict|tolerant]
"""

from __future__ import annotations

import sys

from agentsla.cli import replay as replay_mod
from agentsla.cli import run as run_mod


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print("usage: agentsla {run,replay} ...", file=sys.stderr)
        return 1
    cmd, *rest = argv
    if cmd == "run":
        return run_mod.main(rest)
    if cmd == "replay":
        return replay_mod.main(rest)
    print(f"unknown subcommand: {cmd!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
