"""Generate the README headline table from ``results.parquet``.

Reads the parquet written by :mod:`agentsla.bench.harness`, aggregates
per-mode metrics, and emits a markdown table on stdout (or to a file).

The numbers in this output are the **single source of truth** for the
README. Re-running ``agentsla bench --all && agentsla report`` must
produce a table byte-identical to the one rendered in the README.

Metric semantics:

  * ``success_rate`` — fraction of runs whose final answer contained the
    task's ``expected_substring``.
  * ``gate_passed`` — fraction of runs where the verification gate
    approved the answer. Renamed from ``verified_pct`` because the
    gate's identity-source resolver is self-certifying; the metric is
    honest only as "the gate ran without complaining", not "the claims
    are true." For the truthful metric, see ``verified_at_truth``.
  * ``verified_at_truth`` — fraction of gate-passed runs whose final
    answer also contains the task's ``ground_truth`` substring. ``None``
    when no task in the corpus declares a ground truth.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq


def _aggregate(rows: list[dict]) -> dict[str, float | int | None]:
    n = len(rows)
    if n == 0:
        return {
            "success_rate": 0.0,
            "gate_passed": 0.0,
            "verified_at_truth": None,
            "injection_resistance": 1.0,
            "p95_latency_ms": 0.0,
            "mean_latency_ms": 0.0,
            "n": 0,
        }
    success_rate = sum(1 for r in rows if r["success"]) / n
    gate_passed = sum(1 for r in rows if r["verified"]) / n
    truth_rows = [r for r in rows if r.get("verified_at_truth") is not None]
    if truth_rows:
        verified_at_truth = sum(1 for r in truth_rows if r["verified_at_truth"]) / len(truth_rows)
    else:
        verified_at_truth = None
    inj_runs = [r for r in rows if r["has_injection"]]
    injection_resistance = sum(1 for r in inj_runs if r["injection_resisted"]) / len(inj_runs) if inj_runs else 1.0
    latencies = sorted(r["latency_ms"] for r in rows)
    p95 = latencies[int(0.95 * (n - 1))]
    mean = sum(latencies) / n
    return {
        "success_rate": success_rate,
        "gate_passed": gate_passed,
        "verified_at_truth": verified_at_truth,
        "injection_resistance": injection_resistance,
        "p95_latency_ms": p95,
        "mean_latency_ms": mean,
        "n": n,
    }


def _fmt_truth(v: float | None) -> str:
    return f"{v:.0%}" if v is not None else "n/a"


def _markdown_table(naked: dict, wrapped: dict) -> str:
    overhead_pct = (wrapped["p95_latency_ms"] - naked["p95_latency_ms"]) / naked["p95_latency_ms"] if naked["p95_latency_ms"] else 0.0
    overhead_abs = wrapped["p95_latency_ms"] - naked["p95_latency_ms"]
    lines = [
        "| Metric | Naked | Wrapped | Delta |",
        "|--------|------:|--------:|------:|",
        f"| Success rate | {naked['success_rate']:.0%} | {wrapped['success_rate']:.0%} | {(wrapped['success_rate'] - naked['success_rate']):+.0%} |",
        f"| Gate passed | {naked['gate_passed']:.0%} | {wrapped['gate_passed']:.0%} | {(wrapped['gate_passed'] - naked['gate_passed']):+.0%} |",
        f"| Verified at truth | {_fmt_truth(naked['verified_at_truth'])} | {_fmt_truth(wrapped['verified_at_truth'])} | — |",
        f"| Injection resistance | {naked['injection_resistance']:.0%} | {wrapped['injection_resistance']:.0%} | "
        f"{(wrapped['injection_resistance'] - naked['injection_resistance']):+.0%} |",
        f"| p95 latency (ms) | {naked['p95_latency_ms']:.2f} | {wrapped['p95_latency_ms']:.2f} | {overhead_abs:+.2f} ({overhead_pct:+.1%}) |",
        f"| Mean latency (ms) | {naked['mean_latency_ms']:.2f} | {wrapped['mean_latency_ms']:.2f} | "
        f"{(wrapped['mean_latency_ms'] - naked['mean_latency_ms']):+.2f} |",
        f"| N runs | {int(naked['n'])} | {int(wrapped['n'])} | — |",
    ]
    return "\n".join(lines)


def _real_llm_has_measured_truth(real_llm_path: Path) -> bool:
    """True if ``real_llm.parquet`` exists with at least one measured ``verified_at_truth``.

    Used to gate the top-of-file ``verified_at_truth not measured``
    banner. The banner claims a gap; the gap is closed only when the
    Real-LLM bench path has actually emitted rows where
    ``verified_at_truth`` is not ``None`` AND the row's ``note`` does
    not start with ``[NOT YET MEASURED]``.

    Presence of the file alone is insufficient: the path can exist
    with all rows tagged ``[NOT YET MEASURED]`` (e.g. the model was
    unreachable on the day of the run). In that state the honest move
    is to keep the banner.

    Pins the README contract: ``The honest-gap banner at the top of
    REPORT.md is suppressed once measured rows land in real_llm.parquet``.
    """
    if not real_llm_path.exists():
        return False
    table = pq.read_table(real_llm_path)
    for r in table.to_pylist():
        note = str(r.get("note") or "")
        if note.startswith("[NOT YET MEASURED]"):
            continue
        if r.get("verified_at_truth") is not None:
            return True
    return False


def _render_real_llm_section(real_llm_path: Path) -> str:
    """Render the Real-LLM bench section from ``real_llm.parquet``.

    Closes PRD-v2 §7 honest gap #2: ``verified_at_truth`` is only measurable
    when a real model is exercised against tasks with declared ground truth.
    The hermetic EchoModel bench can never populate that column, so the
    Real-LLM section is the only place a reviewer sees a real
    "verifier-caught" number. The function is intentionally read-only — the
    parquet is the source of truth, written by ``bench/real_llm.py``.

    The section renders four artifacts:

      1. Header with model_id so the numbers are not model-ambiguous.
      2. Naked-vs-wrapped comparison table (same shape as the hermetic
         headline so reviewers can pattern-match).
      3. Honest-gap marker when every row is ``[NOT YET MEASURED]``.

    Returns a markdown block (no trailing newline). Caller is responsible
    for prefixing ``\\n\\n`` between blocks.
    """
    table = pq.read_table(real_llm_path)
    rows = table.to_pylist()
    if not rows:
        return ""

    # Aggregate per-mode (mirrors _aggregate for hermetic, but works against
    # the real-LLM schema which lacks ``verified`` / ``injection_resisted``).
    per_mode: dict[str, list[dict[str, object]]] = {"naked": [], "wrapped": []}
    for r in rows:
        per_mode.setdefault(str(r["mode"]), []).append(r)

    # Pick the model_id from the first row — schema guarantees uniformity.
    model_id = str(rows[0].get("model_id") or "unknown")

    all_unmeasured = all(str(r.get("note", "")).startswith("[NOT YET MEASURED]") for r in rows)
    lines: list[str] = [
        "## Real-LLM bench",
        "",
        f"_Generated from `{real_llm_path}`. Model: `{model_id}`. This is the only path that produces "
        "measured `verified_at_truth` numbers — the hermetic EchoModel bench cannot._",
        "",
    ]

    if all_unmeasured:
        lines.append(
            "> **Honest gap — every row is `[NOT YET MEASURED]`.** "
            "The harness path is real (CLI + tests + parquet schema), but the live model "
            "was never exercised against the bench (e.g. ANTHROPIC_API_KEY was unset, or "
            "the remote run was rate-limited). Re-run `agentsla bench-real` to populate."
        )
        return "\n".join(lines) + "\n"

    lines.append("| Mode | Success | Gate passed | Verified@truth | N rows | p95 (ms) |")
    lines.append("|------|--------:|------------:|---------------:|-------:|---------:|")
    for mode in ("naked", "wrapped"):
        subset = per_mode.get(mode, [])
        n = len(subset)
        if n == 0:
            lines.append(f"| {mode} | n/a | n/a | n/a | 0 | n/a |")
            continue
        n_success = sum(1 for r in subset if r["success"])
        n_gate = sum(1 for r in subset if r["gate_passed"])
        truth_rows = [r for r in subset if r.get("verified_at_truth") is not None]
        truth_pct = (sum(1 for r in truth_rows if r["verified_at_truth"]) / len(truth_rows)) if truth_rows else None
        latencies = sorted(float(r["latency_ms"]) for r in subset)
        p95 = latencies[int(0.95 * (n - 1))] if n > 1 else latencies[0]
        lines.append(f"| {mode} | {n_success / n:.0%} | {n_gate / n:.0%} | {_fmt_truth(truth_pct)} | {n} | {p95:.0f} |")
    return "\n".join(lines) + "\n"


def _render_seeded_section(seeded_path: Path) -> str:
    """Render the seeded-errors section from ``seeded_errors.parquet``.

    Kept as a helper so the optional auto-include stays one block.
    """
    from agentsla.bench.seeded_errors import (
        _summarize,
        render_seeded_errors_section,
    )

    seeded_table = pq.read_table(seeded_path)
    seeded_rows = seeded_table.to_pylist()

    @dataclass
    class _LightRow:
        strategy_pct: float
        status: str
        latency_ms: float

    light = [
        _LightRow(
            strategy_pct=float(r["strategy_pct"]),
            status=str(r["status"]),
            latency_ms=float(r["latency_ms"]),
        )
        for r in seeded_rows
    ]
    unique = sorted({r.strategy_pct for r in light})
    summaries = [_summarize(p, light) for p in unique]
    return render_seeded_errors_section(summaries, source_parquet=seeded_path)


def _render_parity_section(parity_path: Path) -> str:
    """Render the cross-adapter parity section from ``parity.parquet``."""
    from agentsla.bench.parity import _aggregate_parity, render_parity_section

    parity_table = pq.read_table(parity_path)
    parity_rows = parity_table.to_pylist()

    @dataclass
    class _LightRow:
        adapter: str
        task_id: str
        seed: int
        success: bool
        n_events: int
        n_allow: int
        n_deny: int
        latency_ms: float

    light = [
        _LightRow(
            adapter=str(r["adapter"]),
            task_id=str(r["task_id"]),
            seed=int(r["seed"]),
            success=bool(r["success"]),
            n_events=int(r["n_events"]),
            n_allow=int(r["n_allow"]),
            n_deny=int(r["n_deny"]),
            latency_ms=float(r["latency_ms"]),
        )
        for r in parity_rows
    ]
    agg = _aggregate_parity(light)
    return render_parity_section(agg, source_parquet=parity_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentsla-report", description="Generate bench report from parquet.")
    parser.add_argument("--in", dest="in_path", type=Path, default=Path("bench/results/results.parquet"), help="Input parquet.")
    parser.add_argument("--out", type=Path, default=None, help="Output markdown file (default: stdout).")
    args = parser.parse_args(argv)

    if not args.in_path.exists():
        print(f"results parquet not found: {args.in_path}", file=sys.stderr)
        print("Run `agentsla bench --all` first.", file=sys.stderr)
        return 2

    table = pq.read_table(args.in_path)
    rows = table.to_pylist()

    naked_rows = [r for r in rows if r["mode"] == "naked"]
    wrapped_rows = [r for r in rows if r["mode"] == "wrapped"]
    naked = _aggregate(naked_rows)
    wrapped = _aggregate(wrapped_rows)

    md = "# AgentSLA bench report\n\n"
    md += f"_Generated from `{args.in_path}`._\n\n"
    # Honest-gap callout: when no row in the parquet declares a ground_truth
    # substring (the EchoModel bench never does), verified_at_truth is None
    # for every aggregate. Surface this so a reviewer who opens the headline
    # table sees the gap AND the fix, not just "n/a". PRD-v1 § 2.1 F3:
    # "v1 must answer yes, with measured numbers — or surface the honest gap."
    #
    # Suppression condition: the Real-LLM bench path can independently
    # populate ``verified_at_truth`` against ground-truth-declaring tasks.
    # When ``real_llm.parquet`` exists with at least one measured row, the
    # gap is closed by the auto-included Real-LLM section below — the
    # top banner would falsely claim a gap on the same page that the
    # next section closes. This is the README's pinned contract.
    real_llm_path = args.in_path.parent / "real_llm.parquet"
    gap_still_open = naked["verified_at_truth"] is None and wrapped["verified_at_truth"] is None
    if gap_still_open and not _real_llm_has_measured_truth(real_llm_path):
        md += (
            "> **Honest gap — `verified_at_truth` not measured.**\n"
            "> The hermetic `EchoModel` self-certifies but does not declare\n"
            "> task ground truths, so no run can be checked against truth.\n"
            "> To populate this column, run the frugal smoke bench (3 paid\n"
            "> prompts; preview with --dry-plan first, see\n"
            "> docs/GPU_API_COST_OPTIMIZATION.md before escalating):\n"
            "> ```\n"
            "> ANTHROPIC_API_KEY=sk-... \\\n"
            ">   python -m agentsla bench-real \\\n"
            ">     --model claude-haiku-4-5-20251001 \\\n"
            ">     --tasks-per-domain 1 --seeds 1 \\\n"
            ">     --out bench/results/real_llm.parquet\n"
            "> ```\n"
            "> The harness path, tests, and CLI are real (see\n"
            "> `agentsla/bench/real_llm.py` + `tests/unit/bench/test_real_llm.py`);\n"
            "> only the live numbers are missing.\n\n"
        )
    md += "## Headline: naked vs wrapped\n\n"
    md += _markdown_table(naked, wrapped) + "\n\n"
    md += "## Per-domain breakdown\n\n"
    md += "| Domain | Mode | Success | Gate passed | Verified@truth | Inj resist | p95 (ms) |\n"
    md += "|--------|------|--------:|------------:|---------------:|-----------:|---------:|\n"
    for domain in ("financial_ops", "incident_triage", "doc_qa"):
        for mode in ("naked", "wrapped"):
            subset = [r for r in rows if r["mode"] == mode and r["domain"] == domain]
            agg = _aggregate(subset)
            md += (
                f"| {domain} | {mode} | {agg['success_rate']:.0%} | {agg['gate_passed']:.0%} | "
                f"{_fmt_truth(agg['verified_at_truth'])} | {agg['injection_resistance']:.0%} | "
                f"{agg['p95_latency_ms']:.2f} |\n"
            )
    md += "\n## Holdout subset (excluded from dev tuning)\n\n"
    md += "| Mode | N | Success | Gate passed | Verified@truth | p95 (ms) |\n"
    md += "|------|--:|--------:|------------:|---------------:|---------:|\n"
    for mode in ("naked", "wrapped"):
        subset = [r for r in rows if r["mode"] == mode and r["holdout"]]
        agg = _aggregate(subset)
        md += (
            f"| {mode} | {int(agg['n'])} | {agg['success_rate']:.0%} | {agg['gate_passed']:.0%} | "
            f"{_fmt_truth(agg['verified_at_truth'])} | {agg['p95_latency_ms']:.2f} |\n"
        )

    # Optional: append seeded-error section if the parquet exists alongside
    # the main results parquet. Keeps ``report`` deterministic — no re-run of
    # the experiment; we read the parquet written by ``agentsla bench-seeded-errors``.
    seeded_path = args.in_path.parent / "seeded_errors.parquet"
    if seeded_path.exists():
        md += "\n" + _render_seeded_section(seeded_path)

    # Optional: append cross-adapter parity section if ``parity.parquet`` exists.
    # Source of truth for parity = ``bench/parity.py`` (CLI ``agentsla bench-parity``).
    parity_path = args.in_path.parent / "parity.parquet"
    if parity_path.exists():
        md += "\n" + _render_parity_section(parity_path)

    # Optional: append figures section if ``figures/`` directory contains PNGs.
    # Source of truth for figures = ``bench/figures.py`` (CLI ``agentsla bench-figures``).
    figures_dir = args.in_path.parent / "figures"
    if figures_dir.is_dir():
        from agentsla.bench.figures import render_figures_section

        pngs = sorted(figures_dir.glob("*.png"))
        if pngs:
            md += "\n" + render_figures_section(pngs)

    # Optional: append held-out classifier eval section if
    # ``eval_classifier.md`` exists adjacent. Source of truth for the eval
    # = ``bench/eval_classifier.py`` (CLI ``agentsla eval-classifier``).
    eval_path = args.in_path.parent / "eval_classifier.md"
    if eval_path.exists():
        md += "\n" + eval_path.read_text(encoding="utf-8")

    # Optional: append Real-LLM bench section if ``real_llm.parquet`` exists
    # adjacent. Source of truth = ``bench/real_llm.py``. This is the only path
    # that produces a measured ``verified_at_truth`` number; the hermetic
    # bench can never populate it. Closes PRD-v2 §7 honest gap #2.
    # ``real_llm_path`` already bound above (gap-suppression check).
    if real_llm_path.exists():
        md += "\n\n" + _render_real_llm_section(real_llm_path).rstrip("\n")

    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"Wrote report to {args.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
