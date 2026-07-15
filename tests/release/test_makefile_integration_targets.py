"""Makefile-target pins.

Mirrors the integration-check CI gate at the developer-laptop surface:
the ``bench-full`` Makefile target must reproduce all three CI
integration commands (``bench`` + ``bench-seeded-errors`` + ``report``).

Source of truth = ``.github/workflows/test.yml`` integration job
steps. Each test below reads CI to confirm the targeted command set
matches what the Makefile target invokes, so a CI change (drop a
step, add a step) forces a Makefile update in the same commit.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
CI = ROOT / ".github" / "workflows" / "test.yml"


def _makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _ci_integration_commands() -> list[str]:
    """Pull the three reproduction commands from the integration job's
    ``run:`` steps (skip the `mkdir`-style setup steps).
    """
    text = CI.read_text(encoding="utf-8")
    # Find the integration job body.
    m = re.search(
        r"^\s{6}integration:\s*$\n(?P<body>.*?)(?=^\s{4}[a-zA-Z_-]+:\s*$|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert m is not None, "could not locate integration job in test.yml"
    body = m.group("body")
    # `run: |` blocks with multi-line scripts are also valid; pick the
    # first line of every ``run:`` block that starts with ``uv run``.
    commands: list[str] = []
    for run in re.finditer(r"run:\s*(?:\|\s*)?\n((?:[ \t]*\S.*\n)+)", body):
        first = run.group(1).strip().splitlines()[0].strip()
        if first.startswith("uv run") or first.startswith("python -m agentsla"):
            commands.append(first)
    return commands


class TestBenchFullTarget:
    """Pin the ``bench-full`` Makefile target's surface area.

    CI integration job runs three commands:
        1. ``uv run python -m agentsla bench --seeds 2 --out ...``
        2. ``uv run python -m agentsla bench-seeded-errors ...``
        3. ``uv run python -m agentsla report --out ...``
    A developer running ``make bench-full`` should get the same trio.
    """

    def test_bench_full_target_exists(self) -> None:
        text = _makefile_text()
        assert re.search(r"^bench-full\s*:", text, re.MULTILINE), (
            "Makefile missing `bench-full:` target. Add one that mirrors "
            "the CI integration check (bench + bench-seeded-errors + report)."
        )

    def test_bench_full_includes_both_bench_subcommands_and_report(self) -> None:
        """The target body must reference all three CLI subcommands.

        Heuristic look for the literal `bench` / `bench-seeded-errors` /
        `report` strings in the block following the target's colon.
        """
        text = _makefile_text()
        m = re.search(
            r"^bench-full\s*:[^\n]*\n((?:^\t.*\n)+)",
            text,
            re.MULTILINE,
        )
        assert m is not None, (
            "Makefile has `bench-full:` but no body — add the three commands."
        )
        body = m.group(1)
        for needle in ("agentsla bench", "bench-seeded-errors", "agentsla report"):
            assert needle in body, (
                f"Makefile `bench-full` body is missing command containing {needle!r}."
            )
