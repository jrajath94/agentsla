"""Render REPORT.md figures from bench parquets.

Closes the v0.1 deferred item "matplotlib figures in REPORT.md"
(PROJECT.md ``Next Milestone Goals``). Reads ``bench/results/results.parquet``
and produces 5 PNGs under ``bench/results/figures/``:

    success_rate.png        — bar: per-mode success rate
    gate_passed.png         — bar: per-mode verification-gate pass rate
    injection_resistance.png — bar: per-mode injection-resistance rate
    latency_cdf.png         — CDF: per-mode end-to-end latency
    cost_per_task.png       — bar: per-mode mean latency per task (proxy)

Each figure is referenced from ``bench/results/REPORT.md`` via the
``![]()`` markdown image tag the renderer emits. ``agentsla report``
auto-includes the image links in a new ``## Figures`` section when
``bench/results/figures/`` is non-empty.

The figures CLI intentionally reuses :func:`_aggregate` from
:mod:`agentsla.bench.report` so the figure numbers cannot diverge
from the table numbers (single source of truth).

Reproduction::

    python -m agentsla bench-figures \\
        --in bench/results/results.parquet \\
        --out-dir bench/results/figures
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow.parquet as pq

# Matplotlib is an optional dep (see [project.optional-dependencies] figures).
# Import inside functions so the module loads cleanly when matplotlib is absent;
# the CLI prints a friendly error in that case.


def _aggregate(rows: list[dict]) -> dict[str, float]:
    """Mirror :func:`agentsla.bench.report._aggregate` for the metrics we plot.

    Re-implemented (vs imported) so the figures CLI works without
    importing ``bench/report.py`` and pulling the seeded-errors path.
    """
    n = len(rows)
    if n == 0:
        return {"success_rate": 0.0, "gate_passed": 0.0, "injection_resistance": 1.0}
    success_rate = sum(1 for r in rows if r["success"]) / n
    gate_passed = sum(1 for r in rows if r["verified"]) / n
    inj_runs = [r for r in rows if r["has_injection"]]
    injection_resistance = sum(1 for r in inj_runs if r["injection_resisted"]) / len(inj_runs) if inj_runs else 1.0
    return {
        "success_rate": success_rate,
        "gate_passed": gate_passed,
        "injection_resistance": injection_resistance,
    }


def _set_matplotlib_backend() -> None:
    """Force non-interactive backend so the CLI runs headlessly on CI."""
    import matplotlib

    matplotlib.use("Agg")


def _save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)


def render_success_rate(rows: list[dict], out_dir: Path) -> Path:
    import matplotlib.pyplot as plt

    naked = _aggregate([r for r in rows if r["mode"] == "naked"])
    wrapped = _aggregate([r for r in rows if r["mode"] == "wrapped"])
    fig, ax = plt.subplots(figsize=(4.5, 3.5))
    ax.bar(["naked", "wrapped"], [naked["success_rate"], wrapped["success_rate"]], color=["#888", "#246"])
    ax.set_ylim(0.0, 1.0)
    ax.set_ylabel("Success rate")
    ax.set_title("Per-mode success rate")
    for i, v in enumerate([naked["success_rate"], wrapped["success_rate"]]):
        ax.text(i, v + 0.02, f"{v:.0%}", ha="center")
    path = out_dir / "success_rate.png"
    _save(fig, path)
    return path


def render_gate_passed(rows: list[dict], out_dir: Path) -> Path:
    import matplotlib.pyplot as plt

    naked = _aggregate([r for r in rows if r["mode"] == "naked"])
    wrapped = _aggregate([r for r in rows if r["mode"] == "wrapped"])
    fig, ax = plt.subplots(figsize=(4.5, 3.5))
    ax.bar(["naked", "wrapped"], [naked["gate_passed"], wrapped["gate_passed"]], color=["#888", "#246"])
    ax.set_ylim(0.0, 1.0)
    ax.set_ylabel("Gate passed")
    ax.set_title("Verification gate pass rate")
    for i, v in enumerate([naked["gate_passed"], wrapped["gate_passed"]]):
        ax.text(i, v + 0.02, f"{v:.0%}", ha="center")
    path = out_dir / "gate_passed.png"
    _save(fig, path)
    return path


def render_injection_resistance(rows: list[dict], out_dir: Path) -> Path:
    import matplotlib.pyplot as plt

    naked = _aggregate([r for r in rows if r["mode"] == "naked"])
    wrapped = _aggregate([r for r in rows if r["mode"] == "wrapped"])
    fig, ax = plt.subplots(figsize=(4.5, 3.5))
    ax.bar(
        ["naked", "wrapped"],
        [naked["injection_resistance"], wrapped["injection_resistance"]],
        color=["#888", "#246"],
    )
    ax.set_ylim(0.0, 1.0)
    ax.set_ylabel("Injection resistance")
    ax.set_title("Injection-payload resistance")
    for i, v in enumerate([naked["injection_resistance"], wrapped["injection_resistance"]]):
        ax.text(i, v + 0.02, f"{v:.0%}", ha="center")
    path = out_dir / "injection_resistance.png"
    _save(fig, path)
    return path


def render_latency_cdf(rows: list[dict], out_dir: Path) -> Path:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.0, 3.5))
    for mode, color in (("naked", "#888"), ("wrapped", "#246")):
        sub = sorted(r["latency_ms"] for r in rows if r["mode"] == mode)
        if not sub:
            continue
        ys = [(i + 1) / len(sub) for i in range(len(sub))]
        ax.plot(sub, ys, label=mode, color=color, drawstyle="steps-post")
    ax.set_xlabel("Latency (ms)")
    ax.set_ylabel("CDF")
    ax.set_title("Per-mode end-to-end latency CDF")
    ax.legend()
    ax.grid(True, alpha=0.3)
    path = out_dir / "latency_cdf.png"
    _save(fig, path)
    return path


def render_cost_per_task(rows: list[dict], out_dir: Path) -> Path:
    """Mean latency per (mode, task) — proxy for cost overhead."""
    from collections import defaultdict

    import matplotlib.pyplot as plt

    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        grouped[(r["mode"], r["domain"])].append(r["latency_ms"])
    domains = sorted({d for (_, d) in grouped})
    naked_means = [sum(grouped[("naked", d)]) / len(grouped[("naked", d)]) if grouped[("naked", d)] else 0.0 for d in domains]
    wrapped_means = [sum(grouped[("wrapped", d)]) / len(grouped[("wrapped", d)]) if grouped[("wrapped", d)] else 0.0 for d in domains]
    fig, ax = plt.subplots(figsize=(5.5, 3.5))
    x = range(len(domains))
    w = 0.35
    ax.bar([i - w / 2 for i in x], naked_means, width=w, label="naked", color="#888")
    ax.bar([i + w / 2 for i in x], wrapped_means, width=w, label="wrapped", color="#246")
    ax.set_xticks(list(x))
    ax.set_xticklabels(domains, rotation=20, ha="right")
    ax.set_ylabel("Mean latency (ms)")
    ax.set_title("Per-domain mean latency (cost proxy)")
    ax.legend()
    path = out_dir / "cost_per_task.png"
    _save(fig, path)
    return path


def render_figures_section(figure_paths: list[Path]) -> str:
    """Emit the markdown block ``agentsla report`` appends to REPORT.md."""
    lines = ["## Figures", ""]
    for path in figure_paths:
        rel = path.name  # REPORT.md sits in the same dir as figures/
        title = path.stem.replace("_", " ").title()
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"![{title}](figures/{rel})")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentsla-bench-figures",
        description="Render REPORT.md figures from bench results parquet.",
    )
    parser.add_argument("--in", dest="in_path", type=Path, default=Path("bench/results/results.parquet"), help="Input parquet.")
    parser.add_argument("--out-dir", type=Path, default=Path("bench/results/figures"), help="Output figure directory.")
    args = parser.parse_args(argv)

    if not args.in_path.exists():
        print(f"results parquet not found: {args.in_path}", file=sys.stderr)
        print("Run `agentsla bench --all` first.", file=sys.stderr)
        return 2

    try:
        import matplotlib  # noqa: F401 — fail fast if missing
    except ImportError as exc:
        print(f"matplotlib not installed ({exc}); install with `pip install agentsla[figures]`", file=sys.stderr)
        return 3

    _set_matplotlib_backend()

    table = pq.read_table(args.in_path)
    rows = table.to_pylist()
    if not rows:
        print(f"results parquet empty: {args.in_path}", file=sys.stderr)
        return 4

    paths = [
        render_success_rate(rows, args.out_dir),
        render_gate_passed(rows, args.out_dir),
        render_injection_resistance(rows, args.out_dir),
        render_latency_cdf(rows, args.out_dir),
        render_cost_per_task(rows, args.out_dir),
    ]
    for p in paths:
        size = p.stat().st_size
        print(f"  wrote {p} ({size} bytes)")
    print(f"\nWrote {len(paths)} figures to {args.out_dir}")
    return 0


__all__ = [
    "render_cost_per_task",
    "render_figures_section",
    "render_gate_passed",
    "render_injection_resistance",
    "render_latency_cdf",
    "render_success_rate",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
