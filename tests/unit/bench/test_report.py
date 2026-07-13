"""Unit tests for the bench report generator.

Pins the column-rename contract from EXECUTION.md Commit 6:
``verified_pct`` is replaced by ``gate_passed``; the truthful metric
``verified_at_truth`` is added (and is ``None`` when no task declares
ground truth).
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.bench.report import (
    _aggregate,
    _fmt_truth,
    _markdown_table,
)
from agentsla.bench.report import (
    main as report_main,
)


def _write_parquet(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _row(**overrides: object) -> dict:
    base = {
        "mode": "naked",
        "task_id": "t",
        "domain": "doc_qa",
        "seed": 0,
        "holdout": False,
        "has_injection": False,
        "success": True,
        "verified": False,
        "verified_at_truth": None,
        "injection_resisted": True,
        "latency_ms": 5.0,
        "text": "x",
    }
    base.update(overrides)
    return base


class TestAggregateSchema:
    def test_gate_passed_present(self) -> None:
        rows = [_row(verified=True), _row(verified=False)]
        agg = _aggregate(rows)
        assert "gate_passed" in agg
        assert agg["gate_passed"] == 0.5

    def test_verified_pct_removed(self) -> None:
        """The old name must not appear in the aggregate output."""
        rows = [_row()]
        agg = _aggregate(rows)
        assert "verified_pct" not in agg

    def test_verified_at_truth_none_when_no_truth(self) -> None:
        rows = [_row(verified=True, verified_at_truth=None)]
        agg = _aggregate(rows)
        assert agg["verified_at_truth"] is None

    def test_verified_at_truth_computed_when_present(self) -> None:
        rows = [
            _row(verified=True, verified_at_truth=True),
            _row(verified=True, verified_at_truth=False),
            _row(verified=True, verified_at_truth=True),
        ]
        agg = _aggregate(rows)
        assert agg["verified_at_truth"] == 2 / 3

    def test_verified_at_truth_excludes_none_rows(self) -> None:
        rows = [
            _row(verified=True, verified_at_truth=True),
            _row(verified=True, verified_at_truth=None),
        ]
        agg = _aggregate(rows)
        # Only the row with explicit truth counts.
        assert agg["verified_at_truth"] == 1.0


class TestFmtTruth:
    def test_none_renders_as_na(self) -> None:
        assert _fmt_truth(None) == "n/a"

    def test_value_renders_as_percent(self) -> None:
        assert _fmt_truth(0.5) == "50%"


class TestMarkdownTable:
    def test_table_includes_gate_passed(self) -> None:
        naked = _aggregate([_row()])
        wrapped = _aggregate([_row(mode="wrapped")])
        md = _markdown_table(naked, wrapped)
        assert "Gate passed" in md
        assert "verified_pct" not in md.lower()

    def test_table_includes_verified_at_truth(self) -> None:
        naked = _aggregate([_row()])
        wrapped = _aggregate([_row(mode="wrapped")])
        md = _markdown_table(naked, wrapped)
        assert "Verified at truth" in md


class TestReportCli:
    def test_main_writes_markdown_to_stdout(self, tmp_path: Path) -> None:
        parquet = tmp_path / "results.parquet"
        rows = [
            _row(mode="naked"),
            _row(mode="wrapped", verified=True),
        ]
        _write_parquet(parquet, rows)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = report_main(["--in", str(parquet)])
        assert rc == 0
        out = buf.getvalue()
        assert "Gate passed" in out
        assert "Verified at truth" in out
        assert "verified_pct" not in out.lower()

    def test_main_writes_to_file_when_out_passed(self, tmp_path: Path) -> None:
        parquet = tmp_path / "results.parquet"
        out = tmp_path / "report.md"
        _write_parquet(parquet, [_row()])
        rc = report_main(["--in", str(parquet), "--out", str(out)])
        assert rc == 0
        text = out.read_text(encoding="utf-8")
        assert "Gate passed" in text
        assert "Verified at truth" in text

    def test_main_returns_2_when_parquet_missing(self, tmp_path: Path, capsys: object) -> None:
        missing = tmp_path / "nope.parquet"
        rc = report_main(["--in", str(missing)])
        assert rc == 2
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "Run `agentsla bench --all`" in captured.err

    def test_honest_gap_banner_appears_when_no_ground_truth(self, tmp_path: Path) -> None:
        """Both modes have verified_at_truth=None → banner surfaces the gap
        AND the fix command. Without this, a reviewer opening REPORT.md sees
        `verified_at_truth: n/a` in every cell but not the why/how.
        """
        parquet = tmp_path / "results.parquet"
        rows = [_row(mode="naked"), _row(mode="wrapped", verified=True)]
        _write_parquet(parquet, rows)
        out_path = tmp_path / "report.md"
        rc = report_main(["--in", str(parquet), "--out", str(out_path)])
        assert rc == 0
        text = out_path.read_text(encoding="utf-8")
        assert "Honest gap" in text, "banner must surface the gap name so CI grep can lock it in"
        assert "verified_at_truth" in text
        assert "ANTHROPIC_API_KEY" in text, "banner must name the fix command's env var"
        assert "bench-real" in text, "banner must name the fix subcommand"
        # The banner must appear ABOVE the headline table — reviewer reads it first.
        assert text.index("Honest gap") < text.index("Headline: naked vs wrapped"), (
            "banner must precede the headline table so the gap is seen before the numbers"
        )

    def test_honest_gap_banner_absent_when_ground_truth_present(self, tmp_path: Path) -> None:
        """At least one row with verified_at_truth=True → banner must NOT appear
        (the column is then honest about what it measured, not a gap).
        """
        parquet = tmp_path / "results.parquet"
        rows = [
            _row(mode="naked", verified=True, verified_at_truth=True),
            _row(mode="wrapped", verified=True, verified_at_truth=False),
        ]
        _write_parquet(parquet, rows)
        out_path = tmp_path / "report.md"
        rc = report_main(["--in", str(parquet), "--out", str(out_path)])
        assert rc == 0
        text = out_path.read_text(encoding="utf-8")
        assert "Honest gap" not in text, "banner must not appear when verified_at_truth is measurable — would falsely claim a gap"
