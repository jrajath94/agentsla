"""Unit tests for the bench figures renderer.

Pins:
  * each renderer writes a PNG of non-trivial size to ``out_dir``.
  * ``render_figures_section`` emits a markdown block with one ``![]()``
    tag per PNG.
  * missing-input case prints a friendly error and returns exit code 2.

We do NOT snapshot image bytes — matplotlib output is non-deterministic
across versions for things like font hinting. Instead we assert on
file existence + non-trivial size + the markdown contract.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.bench.figures import (
    render_cost_per_task,
    render_figures_section,
    render_gate_passed,
    render_injection_resistance,
    render_latency_cdf,
    render_success_rate,
)


def _row(mode: str, success: bool, verified: bool, has_injection: bool, injection_resisted: bool, domain: str, latency_ms: float) -> dict:
    return {
        "mode": mode,
        "task_id": f"t-{domain}",
        "domain": domain,
        "seed": 0,
        "holdout": False,
        "has_injection": has_injection,
        "success": success,
        "verified": verified,
        "verified_at_truth": None,
        "injection_resisted": injection_resisted,
        "latency_ms": latency_ms,
        "text": "",
    }


def _two_row_fixture() -> list[dict]:
    """Minimal fixture: 4 naked + 4 wrapped rows spanning 2 domains."""
    return [
        _row("naked", True, False, False, True, "financial_ops", 5.0),
        _row("naked", False, False, True, False, "financial_ops", 8.0),
        _row("naked", True, False, False, True, "incident_triage", 6.0),
        _row("naked", True, False, False, True, "incident_triage", 7.0),
        _row("wrapped", True, True, False, True, "financial_ops", 8.0),
        _row("wrapped", True, True, True, True, "financial_ops", 12.0),
        _row("wrapped", True, True, False, True, "incident_triage", 9.0),
        _row("wrapped", False, False, False, True, "incident_triage", 10.0),
    ]


def test_render_success_rate_writes_png(tmp_path: Path) -> None:
    out = render_success_rate(_two_row_fixture(), tmp_path)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_render_gate_passed_writes_png(tmp_path: Path) -> None:
    out = render_gate_passed(_two_row_fixture(), tmp_path)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_render_injection_resistance_writes_png(tmp_path: Path) -> None:
    out = render_injection_resistance(_two_row_fixture(), tmp_path)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_render_latency_cdf_writes_png(tmp_path: Path) -> None:
    out = render_latency_cdf(_two_row_fixture(), tmp_path)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_render_cost_per_task_writes_png(tmp_path: Path) -> None:
    out = render_cost_per_task(_two_row_fixture(), tmp_path)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_render_figures_section_emits_image_tag_per_png(tmp_path: Path) -> None:
    paths = [
        render_success_rate(_two_row_fixture(), tmp_path),
        render_gate_passed(_two_row_fixture(), tmp_path),
    ]
    md = render_figures_section(paths)
    assert "## Figures" in md
    assert "![Success Rate](figures/success_rate.png)" in md
    assert "![Gate Passed](figures/gate_passed.png)" in md


def test_cli_missing_input_returns_2(tmp_path: Path) -> None:
    """The CLI exits 2 when the input parquet is missing."""
    missing = tmp_path / "does-not-exist.parquet"
    result = subprocess.run(  # noqa: S603 — controlled subprocess for CLI smoke
        [sys.executable, "-m", "agentsla.bench.figures", "--in", str(missing), "--out-dir", str(tmp_path / "out")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "not found" in result.stderr


def test_cli_full_pipeline_smoke(tmp_path: Path) -> None:
    """End-to-end: write a tiny parquet, run the CLI, assert PNGs land."""
    in_path = tmp_path / "results.parquet"
    out_dir = tmp_path / "figs"
    pq.write_table(pa.Table.from_pylist(_two_row_fixture()), in_path)
    result = subprocess.run(  # noqa: S603 — controlled subprocess for CLI smoke
        [sys.executable, "-m", "agentsla.bench.figures", "--in", str(in_path), "--out-dir", str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (out_dir / "success_rate.png").exists()
    assert (out_dir / "latency_cdf.png").exists()
