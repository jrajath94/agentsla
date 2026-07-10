"""Cross-adapter parity bench — RawLoopAdapter vs LangGraphAdapter on identical tasks.

Closes the v0.1 deferred item "cross-adapter parity bench" (STATE.md §
Deferred Items; feedback.md Addendum #2). Runs both adapters with the
same :class:`WrappedHooks` instance so policy + verification + classifier
fire identically; writes ``bench/results/parity.parquet``; ``agentsla report``
auto-includes a parity section when the parquet is present alongside
``results.parquet``.

Schema (``parity.parquet``):

    adapter           — "rawloop" | "langgraph"
    task_id           — str
    domain            — str
    seed              — int
    success           — bool (final answer contained expected substring)
    n_events          — int  (event-stream length, used for parity contract)
    n_allow           — int  (gate ALLOW count for the run)
    n_deny            — int  (gate DENY count for the run)
    latency_ms        — float

The headline assertion is: for the same (task_id, seed), both adapters
agree on ``success`` AND on ``n_events``. Event-kind sequence is checked
by the existing :mod:`tests.integration.test_cross_adapter_parity`
suite; this harness surfaces the parity evidence as a deliverable.

Reproduction::

    python -m agentsla bench-parity \\
        --out bench/results/parity.parquet \\
        --db .agentsla/parity.duckdb \\
        --seeds 3
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from agentsla.adapters.base import RuntimeHooks
from agentsla.adapters.langgraph import LangGraphAdapter
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.bench.harness import WrappedHooks
from agentsla.bench.tasks import BenchTask, load_tasks, stats
from agentsla.core.trace import TraceWriter
from agentsla.tools.deterministic import JsonEchoTool

_ADAPTERS: dict[str, type[RawLoopAdapter] | type[LangGraphAdapter]] = {
    "rawloop": RawLoopAdapter,
    "langgraph": LangGraphAdapter,
}


@dataclass
class ParityRow:
    adapter: str
    task_id: str
    domain: str
    seed: int
    success: bool
    n_events: int
    n_allow: int
    n_deny: int
    latency_ms: float


def _run_one(task: BenchTask, *, adapter_name: str, seed: int, db_path: Path) -> ParityRow:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    writer = TraceWriter(db_path)
    try:
        adapter_cls = _ADAPTERS[adapter_name]
        adapter = adapter_cls(
            tools={"json_echo": JsonEchoTool()},
            trace_writer=writer,
            task_text=task.text,
        )
        hooks: RuntimeHooks = WrappedHooks(writer)
        t0 = time.perf_counter()
        result = adapter.run(task_id=task.task_id, hooks=hooks)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        final = result.text or ""
        success = task.expected_substring in final
        n_events = len(result.trace.events)
        decisions = [a["decision"] for a in hooks.gate.audit]  # type: ignore[attr-defined]
        n_allow = decisions.count("allow")
        n_deny = decisions.count("deny")
        return ParityRow(
            adapter=adapter_name,
            task_id=task.task_id,
            domain=task.domain,
            seed=seed,
            success=success,
            n_events=n_events,
            n_allow=n_allow,
            n_deny=n_deny,
            latency_ms=latency_ms,
        )
    finally:
        writer.close()


def _aggregate_parity(rows: list[ParityRow]) -> dict[str, float | int]:
    """Per-adapter aggregate + parity agreement (success + n_events)."""
    by_adapter: dict[str, list[ParityRow]] = {"rawloop": [], "langgraph": []}
    for r in rows:
        by_adapter[r.adapter].append(r)

    out: dict[str, float | int] = {}
    for name, group in by_adapter.items():
        out[f"{name}_n"] = len(group)
        out[f"{name}_success"] = sum(1 for r in group if r.success)
        out[f"{name}_mean_events"] = sum(r.n_events for r in group) / len(group) if group else 0.0
        out[f"{name}_mean_latency_ms"] = sum(r.latency_ms for r in group) / len(group) if group else 0.0

    # Parity contract: pair rows by (task_id, seed); both adapters must agree
    # on `success` AND on `n_events` for the pair to count as "in parity".
    pairs: dict[tuple[str, int], dict[str, ParityRow]] = {}
    for r in rows:
        key = (r.task_id, r.seed)
        pairs.setdefault(key, {})[r.adapter] = r

    paired = [p for p in pairs.values() if "rawloop" in p and "langgraph" in p]
    if not paired:
        out["paired_n"] = 0
        out["success_agreement"] = 1.0
        out["events_agreement"] = 1.0
        return out

    success_match = sum(1 for p in paired if p["rawloop"].success == p["langgraph"].success)
    events_match = sum(1 for p in paired if p["rawloop"].n_events == p["langgraph"].n_events)
    out["paired_n"] = len(paired)
    out["success_agreement"] = success_match / len(paired)
    out["events_agreement"] = events_match / len(paired)
    return out


def render_parity_section(agg: dict[str, float | int], source_parquet: Path) -> str:
    """Render the parity section that ``agentsla report`` appends to REPORT.md."""
    lines = [
        "## Cross-adapter parity (rawloop vs langgraph)",
        "",
        f"_Generated from `{source_parquet}`._",
        "",
        "| Adapter | N | Successes | Mean events/run | Mean latency (ms) |",
        "|---------|--:|----------:|----------------:|------------------:|",
    ]
    for adapter in ("rawloop", "langgraph"):
        lines.append(
            f"| {adapter} | {int(agg[f'{adapter}_n'])} | "
            f"{int(agg[f'{adapter}_success'])} | "
            f"{agg[f'{adapter}_mean_events']:.2f} | "
            f"{agg[f'{adapter}_mean_latency_ms']:.2f} |"
        )
    lines.extend(
        [
            "",
            f"**Paired runs:** {int(agg['paired_n'])}",
            f"**Success agreement:** {agg['success_agreement']:.0%}",
            f"**Event-count agreement:** {agg['events_agreement']:.0%}",
            "",
            "Event-kind sequence equality is enforced by the unit suite "
            "(`tests/integration/test_cross_adapter_parity.py`); this "
            "section surfaces the parity evidence at the bench scale.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentsla-bench-parity",
        description="Run cross-adapter parity bench (rawloop vs langgraph).",
    )
    parser.add_argument("--out", type=Path, default=Path("bench/results/parity.parquet"), help="Output parquet path.")
    parser.add_argument("--db", type=Path, default=Path(".agentsla/parity.duckdb"), help="Trace-store DuckDB path.")
    parser.add_argument("--seeds", type=int, default=3, help="Number of seeds per (adapter, task).")
    parser.add_argument("--include-injection", action="store_true", default=False, help="Include injection variants (default False).")
    args = parser.parse_args(argv)

    tasks = load_tasks(include_injection=args.include_injection)
    s = stats(tasks)
    print(f"Loaded {s['total']} tasks ({s['base']} base + {s['injection']} injection).")

    rows: list[ParityRow] = []
    for adapter_name in _ADAPTERS:
        for seed in range(args.seeds):
            for task in tasks:
                row = _run_one(task, adapter_name=adapter_name, seed=seed, db_path=args.db)
                rows.append(row)
                print(
                    f"  {adapter_name:>9} seed={seed} {task.task_id:<25} "
                    f"success={int(row.success)} events={row.n_events} "
                    f"allow={row.n_allow} deny={row.n_deny} lat={row.latency_ms:6.2f}ms"
                )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([asdict(r) for r in rows])
    pq.write_table(table, args.out)
    print(f"\nWrote {len(rows)} rows to {args.out}")

    agg = _aggregate_parity(rows)
    print("\nAggregate parity:")
    for adapter in ("rawloop", "langgraph"):
        print(
            f"  {adapter:>9}: n={agg[f'{adapter}_n']} "
            f"successes={agg[f'{adapter}_success']} "
            f"mean_events={agg[f'{adapter}_mean_events']:.2f} "
            f"mean_latency_ms={agg[f'{adapter}_mean_latency_ms']:.2f}"
        )
    print(f"  paired_n={agg['paired_n']} success_agreement={agg['success_agreement']:.0%} events_agreement={agg['events_agreement']:.0%}")
    return 0


__all__ = ["ParityRow", "_aggregate_parity", "render_parity_section"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
