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
    _render_real_llm_section,
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


def _real_llm_row(**overrides: object) -> dict:
    base = {
        "mode": "naked",
        "task_id": "real-finops-001",
        "domain": "financial_ops",
        "model_id": "claude-haiku-4-5-20251001",
        "seed": 0,
        "success": True,
        "gate_passed": False,
        "verified_at_truth": True,
        "sensitivity": None,
        "specificity": None,
        "latency_ms": 1500.0,
        "text": "42",
        "note": "",
    }
    base.update(overrides)
    return base


class TestRenderRealLlmSection:
    """Tests for the auto-included Real-LLM bench section in REPORT.md.

    Closes PRD-v2 §7 honest gap #2 ("README 'verifier caught X%' headline
    depends on real_llm numbers"). The Real-LLM bench is the only path that
    produces ``verified_at_truth`` values, so the section must surface the
    naked-vs-wrapped comparison alongside the hermetic headline.
    """

    def test_section_renders_heading(self, tmp_path: Path) -> None:
        """Section header must contain "Real-LLM" so CI grep + readers can locate it."""
        parquet = tmp_path / "real_llm.parquet"
        rows = [_real_llm_row(), _real_llm_row(mode="wrapped", gate_passed=True)]
        _write_parquet(parquet, rows)
        section = _render_real_llm_section(parquet)
        assert "## Real-LLM bench" in section

    def test_section_includes_naked_vs_wrapped_table(self, tmp_path: Path) -> None:
        """Table header must be the same shape as the hermetic headline."""
        parquet = tmp_path / "real_llm.parquet"
        rows = [
            _real_llm_row(mode="naked"),
            _real_llm_row(mode="wrapped", gate_passed=True, verified_at_truth=True),
        ]
        _write_parquet(parquet, rows)
        section = _render_real_llm_section(parquet)
        # Columns mirror the hermetic headline + a "N rows" column so the
        # reviewer sees sample size alongside the rate.
        assert "| Mode | Success | Gate passed | Verified@truth | N rows | p95 (ms) |" in section
        assert "| naked" in section
        assert "| wrapped" in section

    def test_section_aggregates_truth_per_mode(self, tmp_path: Path) -> None:
        """verified_at_truth counts only rows where the field is non-None."""
        parquet = tmp_path / "real_llm.parquet"
        rows = [
            _real_llm_row(mode="wrapped", verified_at_truth=True, gate_passed=True),
            _real_llm_row(mode="wrapped", verified_at_truth=False, gate_passed=True),
            _real_llm_row(mode="wrapped", verified_at_truth=None, gate_passed=True),
            _real_llm_row(mode="naked", verified_at_truth=True),
        ]
        _write_parquet(parquet, rows)
        section = _render_real_llm_section(parquet)
        # Wrapped: 1 truth-true of 2 measured = 50%.
        assert "50%" in section
        # Naked: 1 truth-true of 1 measured = 100%.
        assert "100%" in section

    def test_section_aggregates_p95_per_mode(self, tmp_path: Path) -> None:
        """p95 latency column must reflect the actual latencies, not zero.
        Mirrors the hermetic :func:`_aggregate` index formula
        ``latencies[int(0.95 * (n - 1))]`` so the two tables compute
        p95 the same way and can be compared side by side.
        """
        parquet = tmp_path / "real_llm.parquet"
        rows = [
            _real_llm_row(mode="wrapped", latency_ms=1000.0),
            _real_llm_row(mode="wrapped", latency_ms=2000.0),
            _real_llm_row(mode="wrapped", latency_ms=3000.0),
        ]
        _write_parquet(parquet, rows)
        section = _render_real_llm_section(parquet)
        # n=3, p95 index = int(0.95 * 2) = 1 → latencies[1] = 2000.
        assert "2000" in section

    def test_section_names_model_id(self, tmp_path: Path) -> None:
        """Reviewer must see which model produced the numbers — the headline
        is model-specific (Haiku vs Sonnet have very different latencies).
        """
        parquet = tmp_path / "real_llm.parquet"
        rows = [_real_llm_row(model_id="MiniMax-M3")]
        _write_parquet(parquet, rows)
        section = _render_real_llm_section(parquet)
        assert "MiniMax-M3" in section

    def test_section_emits_unmeasured_banner_when_all_rows_are_not_yet_measured(self, tmp_path: Path) -> None:
        """If every row's note starts with [NOT YET MEASURED], the section
        must say so — not pretend the columns are populated.
        """
        parquet = tmp_path / "real_llm.parquet"
        rows = [
            _real_llm_row(mode="naked", note="[NOT YET MEASURED] rate limit"),
            _real_llm_row(mode="wrapped", note="[NOT YET MEASURED] rate limit"),
        ]
        _write_parquet(parquet, rows)
        section = _render_real_llm_section(parquet)
        assert "NOT YET MEASURED" in section, "section must surface the honest-gap marker"


class TestReportAutoIncludesRealLlmSection:
    """Integration check: main() auto-includes the Real-LLM section when
    ``real_llm.parquet`` is adjacent to ``results.parquet`` — same contract as
    ``parity.parquet`` and ``seeded_errors.parquet``.
    """

    def test_main_appends_section_when_real_llm_parquet_present(self, tmp_path: Path) -> None:
        results = tmp_path / "results.parquet"
        real_llm = tmp_path / "real_llm.parquet"
        # Hermetic results: zero ground truth → banner must appear.
        _write_parquet(results, [_row(), _row(mode="wrapped", verified=True)])
        # Real-LLM parquet: populated with measured rows.
        _write_parquet(
            real_llm,
            [
                _real_llm_row(mode="wrapped", verified_at_truth=True, gate_passed=True),
                _real_llm_row(mode="wrapped", verified_at_truth=False, gate_passed=True),
            ],
        )
        out = tmp_path / "report.md"
        rc = report_main(["--in", str(results), "--out", str(out)])
        assert rc == 0
        text = out.read_text(encoding="utf-8")
        assert "## Real-LLM bench" in text
        # The Real-LLM section must come AFTER the hermetic headline so the
        # reader walks top-to-bottom: hermetic → parity → real-LLM.
        assert text.index("Headline: naked vs wrapped") < text.index("## Real-LLM bench")

    def test_main_omits_section_when_real_llm_parquet_absent(self, tmp_path: Path) -> None:
        results = tmp_path / "results.parquet"
        _write_parquet(results, [_row(), _row(mode="wrapped", verified=True)])
        out = tmp_path / "report.md"
        rc = report_main(["--in", str(results), "--out", str(out)])
        assert rc == 0
        text = out.read_text(encoding="utf-8")
        assert "## Real-LLM bench" not in text
        # Honest-gap banner still appears (no real-LLM data → still a gap).
        assert "Honest gap" in text

    def test_top_banner_suppressed_when_real_llm_has_measured_truth(self, tmp_path: Path) -> None:
        """Pins the contract documented in README.md:
        ``the honest-gap banner at the top of REPORT.md is suppressed
        once measured rows land in real_llm.parquet``.

        The hermetic EchoModel bench never measures ``verified_at_truth``
        (the column is structurally None); but the Real-LLM bench path
        CAN measure it. When the latter has measured rows, the top-of-
        file "verified_at_truth not measured" banner must NOT appear —
        claiming a gap that the next section on the same page closes is
        a drift the README explicitly calls out as forbidden.

        This test guards against two failure modes:

          * Code reverts banner condition to "always show when hermetic
            has no truth" (a regression that would re-introduce the
            contradiction this test was written to remove).
          * Code stops reading real_llm.parquet entirely (a refactor
            that would orphan the auto-include section, leaving the
            banner claiming a gap that the report can't fill).
        """
        results = tmp_path / "results.parquet"
        real_llm = tmp_path / "real_llm.parquet"
        # Hermetic results: zero ground truth → historically the banner
        # condition would trip here.
        _write_parquet(results, [_row(), _row(mode="wrapped", verified=True)])
        # Real-LLM: BOTH modes have measured verified_at_truth.
        _write_parquet(
            real_llm,
            [
                _real_llm_row(mode="naked", verified_at_truth=True, gate_passed=False),
                _real_llm_row(mode="wrapped", verified_at_truth=True, gate_passed=True),
            ],
        )
        out = tmp_path / "report.md"
        rc = report_main(["--in", str(results), "--out", str(out)])
        assert rc == 0
        text = out.read_text(encoding="utf-8")
        # Real-LLM section still auto-appended.
        assert "## Real-LLM bench" in text
        # Top-of-file "verified_at_truth not measured" banner must NOT
        # appear — the gap is closed by the section immediately below.
        # NOTE: A scoped "every row is [NOT YET MEASURED]" marker is fine
        # to appear in the Real-LLM section when those rows ARE
        # unmeasured; this test's measured rows must prevent that too.
        assert "not measured" not in text.lower(), (
            "banner claiming a gap contradicts the measured Real-LLM section on the same page — README explicitly forbids this"
        )

    def test_top_banner_appears_when_real_llm_parquet_absent_but_hermetic_lacks_truth(self, tmp_path: Path) -> None:
        """Symmetry check: when real_llm.parquet is absent, the top
        banner IS the only honest signal — it must still appear.

        Pairs with :func:`test_top_banner_suppressed_when_real_llm_has_measured_truth`
        to lock in both sides of the suppression condition.
        """
        results = tmp_path / "results.parquet"
        _write_parquet(results, [_row(), _row(mode="wrapped", verified=True)])
        # No real_llm.parquet on disk.
        out = tmp_path / "report.md"
        rc = report_main(["--in", str(results), "--out", str(out)])
        assert rc == 0
        text = out.read_text(encoding="utf-8")
        assert "Honest gap" in text
        assert "verified_at_truth" in text

    def test_top_banner_appears_when_real_llm_all_unmeasured(self, tmp_path: Path) -> None:
        """When real_llm.parquet exists but every row's note is
        ``[NOT YET MEASURED]``, the gap is NOT closed — the top banner
        must still surface. This pins the second half of the suppression
        condition: presence of the file alone is insufficient; measured
        rows are required.
        """
        results = tmp_path / "results.parquet"
        real_llm = tmp_path / "real_llm.parquet"
        _write_parquet(results, [_row(), _row(mode="wrapped", verified=True)])
        _write_parquet(
            real_llm,
            [
                _real_llm_row(mode="naked", note="[NOT YET MEASURED] rate limit"),
                _real_llm_row(mode="wrapped", note="[NOT YET MEASURED] rate limit"),
            ],
        )
        out = tmp_path / "report.md"
        rc = report_main(["--in", str(results), "--out", str(out)])
        assert rc == 0
        text = out.read_text(encoding="utf-8")
        assert "Honest gap" in text
        # Real-LLM section is present (file exists) but renders the
        # scoped "every row is [NOT YET MEASURED]" marker instead of a
        # numeric table.
        assert "## Real-LLM bench" in text
        assert "NOT YET MEASURED" in text
