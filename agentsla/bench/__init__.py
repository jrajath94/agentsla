"""Benchmark harness + report generator.

Phase 5 deliverable. ``agentsla bench --all`` reproduces every README
number from ``results.parquet``; ``agentsla report`` turns that parquet
into the headline table.
"""

from agentsla.bench.harness import BenchAggregate, BenchRow, main as bench_main
from agentsla.bench.report import main as report_main
from agentsla.bench.tasks import BenchTask, holdout_tasks, load_tasks, stats

__all__ = [
    "BenchAggregate",
    "BenchRow",
    "BenchTask",
    "bench_main",
    "holdout_tasks",
    "load_tasks",
    "report_main",
    "stats",
]