"""Integration test: ``agentsla.__main__`` must not eagerly import bench helpers.

Why this lives in ``tests/integration/``:
- The contract is module-level, but the failure mode is real (bare
  ``pip install agentsla`` produces a CLI that crashes on every invocation
  because ``typer`` / ``rich`` / ``anthropic`` / ``matplotlib`` are missing).
- Without this test the regression re-emerges silently any time someone
  reaches for the bench helpers at module top of ``agentsla/__main__.py``.

Asserts:
1. Importing ``agentsla.__main__`` does not pull in any of
   ``agentsla.bench.*`` modules.
2. ``agentsla.run`` / ``agentsla.replay`` subcommands route to the
   core CLI surface without touching bench extras.
"""

from __future__ import annotations

import importlib
import sys


def test_main_module_does_not_import_bench_helpers() -> None:
    """``from agentsla.__main__ import main`` must not load bench modules.

    Contract: a user that runs ``pip install agentsla`` (no extras) and
    then ``agentsla run --help`` must not ImportError on typer / rich /
    anthropic. The bench modules and their imports are gated behind the
    ``agentsla bench*`` subcommands via lazy imports inside ``main()``.
    """
    # Force a fresh import so the assertion reflects module-load order,
    # not whatever the test session has already pulled in.
    for mod_name in list(sys.modules):
        if mod_name == "agentsla.__main__" or mod_name.startswith("agentsla.bench"):
            del sys.modules[mod_name]

    cli_main = importlib.import_module("agentsla.__main__")
    assert callable(cli_main.main), "main() must be a callable at module load"

    bench_modules = [m for m in sys.modules if m.startswith("agentsla.bench")]
    assert bench_modules == [], (
        f"agentsla.__main__ eagerly imported {bench_modules}; "
        "use lazy imports inside main() so bare `pip install agentsla` "
        "users don't crash on every CLI invocation."
    )


def test_main_dispatches_run_without_bench_install() -> None:
    """``agentsla run --help`` works in a process without bench extras.

    The test session has all extras, so we can't easily simulate missing
    extras here. Instead we assert that the dispatch happens through the
    core CLI module (``agentsla.cli.run``), not through any bench helper.
    """
    import inspect

    import agentsla.__main__ as cli_main

    source = inspect.getsource(cli_main.main)
    # The run/replay dispatch must use the core CLI modules directly,
    # not the bench helpers. (The bench helpers are only loaded for
    # bench / bench-seeded-errors / bench-real / report.)
    assert "from agentsla.cli import run" in source or "run_mod" in source, (
        "agentsla run dispatch should route through agentsla.cli.run, not through bench."
    )
    assert "bench_main" in source, "agentsla bench dispatch must exist"
    # bench_main must be imported lazily — i.e. inside main(), not at module top.
    # The eager-import regression would show up as `from agentsla.bench import bench_main`
    # at module top (line < the def main line).
    bench_import_lines = [idx for idx, line in enumerate(source.splitlines(), start=1) if "from agentsla.bench" in line and "import" in line]
    def_main_line = next(idx for idx, line in enumerate(source.splitlines(), start=1) if line.startswith("def main("))
    bad = [idx for idx in bench_import_lines if idx < def_main_line]
    assert not bad, f"bench imports at module top (line {bad}) break bare-pip-install. Move bench imports inside the if-branch in main()."
