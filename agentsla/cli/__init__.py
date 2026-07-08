"""Command-line entry point for AgentSLA.

Subcommands land as they are built:
  - `agentsla run <task>`        (plan 01.5 — rawloop smoke)
  - `agentsla replay <trace_id>` (plan 01.4 — strict + tolerant)
  - `agentsla bench [--all]`     (plan 5)
  - `agentsla report`            (plan 5)

Typer is pulled in via the optional [bench] extra — Phase 1 CLI uses
``python -m agentsla run`` so the core install does not depend on typer.
"""
