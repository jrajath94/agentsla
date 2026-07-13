"""Integration smoke test for the bench CLI surface (PRD-v1 F10).

Runs ``python -m agentsla bench --seeds 1`` against a hermetic
EchoModel + JsonEchoTool path, then asserts:

  1. The parquet file is created and non-empty.
  2. The parquet schema carries the v0.1+ headline columns
     (``gate_passed``, ``verified_at_truth``) and the wrapped/naked
     modes both appear.
  3. ``python -m agentsla report`` regenerates ``REPORT.md`` and the
     headline section is present.

This is the pytest counterpart to the CI ``integration`` job
(which also runs the bench + report and grep's the headline). The
pytest runs against a tmp dir so it never collides with committed
artifacts in ``bench/results/``.

Hermetic: no API key, no network. Same path as ``make bench``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq


def _run_cli(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``python -m agentsla <args>`` from ``cwd``. Return the result."""
    return subprocess.run(  # noqa: S603 — controlled subprocess for CLI smoke
        [sys.executable, "-m", "agentsla", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def test_bench_writes_parquet_with_headline_columns(tmp_path: Path) -> None:
    """``python -m agentsla bench --seeds 1 --out <p>`` writes a parquet
    with the v0.1+ headline columns and both modes (naked + wrapped)."""
    parquet_path = tmp_path / "results.parquet"
    db_path = tmp_path / "trace.duckdb"

    result = _run_cli(
        [
            "bench",
            "--seeds",
            "1",
            "--out",
            str(parquet_path),
            "--db",
            str(db_path),
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, f"bench CLI must exit 0; got {result.returncode}. stderr={result.stderr!r}"
    assert parquet_path.exists(), f"parquet must exist at {parquet_path}"
    assert parquet_path.stat().st_size > 0, "parquet must be non-empty"

    table = pq.read_table(parquet_path)
    column_names = set(table.column_names)
    # v0.1+ schema — see agentsla/bench/harness.py for the row dataclass.
    # Required for honest headline: mode split, domain breakdown, verified
    # boolean (the gate's pass/fail), and the v0.1-added verified_at_truth.
    required = {"mode", "task_id", "domain", "verified", "verified_at_truth"}
    missing = required - column_names
    assert not missing, f"parquet schema missing required columns: {sorted(missing)}"

    modes = set(table.column("mode").to_pylist())
    assert modes == {"naked", "wrapped"} or {"naked", "wrapped"}.issubset(modes), (
        f"parquet must contain both naked + wrapped rows; got modes={modes!r}"
    )


def test_report_cli_regenerates_report_with_headline(tmp_path: Path) -> None:
    """``python -m agentsla report`` writes REPORT.md with the headline section.

    Pre-condition: parquet from bench exists. We use the tmp_path from the
    previous test by re-running bench first.
    """
    parquet_path = tmp_path / "results.parquet"
    db_path = tmp_path / "trace.duckdb"
    bench_result = _run_cli(
        [
            "bench",
            "--seeds",
            "1",
            "--out",
            str(parquet_path),
            "--db",
            str(db_path),
        ],
        cwd=tmp_path,
    )
    assert bench_result.returncode == 0, f"bench must succeed first; got {bench_result.stderr!r}"

    report_path = tmp_path / "REPORT.md"
    report_result = _run_cli(
        ["report", "--in", str(parquet_path), "--out", str(report_path)],
        cwd=tmp_path,
    )
    assert report_result.returncode == 0, f"report CLI must exit 0; got {report_result.returncode}. stderr={report_result.stderr!r}"
    assert report_path.exists(), f"REPORT.md must exist at {report_path}"
    report_text = report_path.read_text(encoding="utf-8")
    assert report_text, "REPORT.md must be non-empty"
    # The headline section is the contract for downstream readers.
    assert re.search(r"Headline:.*naked vs wrapped", report_text, re.DOTALL), (
        "REPORT.md must contain a 'Headline: naked vs wrapped' section so the bench surface is honest about what was measured."
    )


def test_bench_smoke_full_pipeline_returns_zero(tmp_path: Path) -> None:
    """The full bench → report pipeline (seeds=1) completes without errors.

    Catches the class of regressions where bench writes zero rows (e.g. a
    fixtures-file move that breaks tasks.py) and report silently emits an
    empty table. The CI integration job does the same at seeds=2; this
    pytest is the local equivalent at seeds=1.
    """
    parquet_path = tmp_path / "results.parquet"
    db_path = tmp_path / "trace.duckdb"
    report_path = tmp_path / "REPORT.md"

    bench_result = _run_cli(
        [
            "bench",
            "--seeds",
            "1",
            "--out",
            str(parquet_path),
            "--db",
            str(db_path),
        ],
        cwd=tmp_path,
    )
    assert bench_result.returncode == 0, bench_result.stderr
    table = pq.read_table(parquet_path)
    assert table.num_rows > 0, (
        f"bench must produce at least one row at seeds=1; got {table.num_rows}. An empty parquet means the task fixtures or harness silently broke."
    )

    report_result = _run_cli(
        ["report", "--in", str(parquet_path), "--out", str(report_path)],
        cwd=tmp_path,
    )
    assert report_result.returncode == 0, report_result.stderr
    report_text = report_path.read_text(encoding="utf-8")
    # At minimum, the report must mention naked and wrapped somewhere —
    # otherwise the table is empty / wrong fixture.
    assert "naked" in report_text and "wrapped" in report_text, "REPORT.md must mention both naked and wrapped modes so the comparison is grounded."
