"""Generate the README headline table from ``results.parquet``.

Reads the parquet written by :mod:`agentsla.bench.harness`, aggregates
per-mode metrics, and emits a markdown table on stdout (or to a file).

The numbers in this output are the **single source of truth** for the
README. Re-running ``agentsla bench --all && agentsla report`` must
produce a table byte-identical to the one rendered in the README.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow.parquet as pq


def _aggregate(rows: list[dict]) -> dict[str, float]:
    n = len(rows)
    if n == 0:
        return {
            "success_rate": 0.0,
            "verified_pct": 0.0,
            "injection_resistance": 1.0,
            "p95_latency_ms": 0.0,
            "mean_latency_ms": 0.0,
            "n": 0,
        }
    success_rate = sum(1 for r in rows if r["success"]) / n
    verified_pct = sum(1 for r in rows if r["verified"]) / n
    inj_runs = [r for r in rows if r["has_injection"]]
    injection_resistance = (
        sum(1 for r in inj_runs if r["injection_resisted"]) / len(inj_runs)
        if inj_runs
        else 1.0
    )
    latencies = sorted(r["latency_ms"] for r in rows)
    p95 = latencies[int(0.95 * (n - 1))]
    mean = sum(latencies) / n
    return {
        "success_rate": success_rate,
        "verified_pct": verified_pct,
        "injection_resistance": injection_resistance,
        "p95_latency_ms": p95,
        "mean_latency_ms": mean,
        "n": n,
    }


def _markdown_table(naked: dict, wrapped: dict) -> str:
    overhead_pct = (
        (wrapped["p95_latency_ms"] - naked["p95_latency_ms"]) / naked["p95_latency_ms"]
        if naked["p95_latency_ms"]
        else 0.0
    )
    overhead_abs = wrapped["p95_latency_ms"] - naked["p95_latency_ms"]
    lines = [
        "| Metric | Naked | Wrapped | Delta |",
        "|--------|------:|--------:|------:|",
        f"| Success rate | {naked['success_rate']:.0%} | {wrapped['success_rate']:.0%} | "
        f"{(wrapped['success_rate'] - naked['success_rate']):+.0%} |",
        f"| Verified % | {naked['verified_pct']:.0%} | {wrapped['verified_pct']:.0%} | "
        f"{(wrapped['verified_pct'] - naked['verified_pct']):+.0%} |",
        f"| Injection resistance | {naked['injection_resistance']:.0%} | {wrapped['injection_resistance']:.0%} | "
        f"{(wrapped['injection_resistance'] - naked['injection_resistance']):+.0%} |",
        f"| p95 latency (ms) | {naked['p95_latency_ms']:.2f} | {wrapped['p95_latency_ms']:.2f} | "
        f"{overhead_abs:+.2f} ({overhead_pct:+.1%}) |",
        f"| Mean latency (ms) | {naked['mean_latency_ms']:.2f} | {wrapped['mean_latency_ms']:.2f} | "
        f"{(wrapped['mean_latency_ms'] - naked['mean_latency_ms']):+.2f} |",
        f"| N runs | {int(naked['n'])} | {int(wrapped['n'])} | — |",
    ]
    return "\n".join(lines)


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
    md += "## Headline: naked vs wrapped\n\n"
    md += _markdown_table(naked, wrapped) + "\n\n"
    md += "## Per-domain breakdown\n\n"
    md += "| Domain | Mode | Success | Verified | Inj resist | p95 (ms) |\n"
    md += "|--------|------|--------:|---------:|-----------:|---------:|\n"
    for domain in ("financial_ops", "incident_triage", "doc_qa"):
        for mode in ("naked", "wrapped"):
            subset = [r for r in rows if r["mode"] == mode and r["domain"] == domain]
            agg = _aggregate(subset)
            md += (
                f"| {domain} | {mode} | {agg['success_rate']:.0%} | {agg['verified_pct']:.0%} | "
                f"{agg['injection_resistance']:.0%} | {agg['p95_latency_ms']:.2f} |\n"
            )
    md += "\n## Holdout subset (excluded from dev tuning)\n\n"
    md += "| Mode | N | Success | Verified | p95 (ms) |\n"
    md += "|------|--:|--------:|---------:|---------:|\n"
    for mode in ("naked", "wrapped"):
        subset = [r for r in rows if r["mode"] == mode and r["holdout"]]
        agg = _aggregate(subset)
        md += f"| {mode} | {int(agg['n'])} | {agg['success_rate']:.0%} | {agg['verified_pct']:.0%} | {agg['p95_latency_ms']:.2f} |\n"

    # Optional: append seeded-error section if the parquet exists alongside
    # the main results parquet. Keeps ``report`` deterministic — no re-run of
    # the experiment; we read the parquet written by ``agentsla bench-seeded-errors``.
    seeded_path = args.in_path.parent / "seeded_errors.parquet"
    if seeded_path.exists():
        from agentsla.bench.seeded_errors import (
            _summarize,
            render_seeded_errors_section,
        )

        seeded_table = pq.read_table(seeded_path)
        seeded_rows = seeded_table.to_pylist()
        # Rebuild TrialRow-like dicts; _summarize only needs status + strategy_pct + latency_ms.
        from dataclasses import dataclass

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
        # One summary per unique strategy.
        unique = sorted({r.strategy_pct for r in light})
        summaries = [_summarize(p, light) for p in unique]
        md += "\n" + render_seeded_errors_section(summaries, source_parquet=seeded_path)

    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"Wrote report to {args.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
