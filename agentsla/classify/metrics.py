"""Prometheus metrics for AgentSLA.

Two surfaces:

  1. **Counter** ``agentsla_failures_total{category="<14-cat>"}`` —
     per-category failure counts.
  2. **Gauge** ``agentsla_verify_coverage`` — most recent verification-coverage
     reading (set by the verification gate on every verdict).

Plus one histogram for end-to-end latency (acceptance: bench harness scrapes
``agentsla_classify_latency_seconds`` to produce the latency-overhead column).

The metrics module is **import-safe** even when ``prometheus_client`` is not
installed — the ``agentsla[metrics]`` extra pulls it. The CLI surfaces that
need to scrape /metrics (bench + CLI) install the extra; the core runtime does
not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:  # pragma: no cover — exercised only when extra is absent
    Counter = None  # type: ignore[assignment]
    Gauge = None  # type: ignore[assignment]
    Histogram = None  # type: ignore[assignment]


@dataclass
class MetricsBundle:
    """One per-process bundle. Construct via :func:`build_metrics`."""

    failures_total: Any
    verify_coverage: Any
    classify_latency_seconds: Any
    registry: Any


def build_metrics(registry: Any = None) -> MetricsBundle:
    """Build the bundle. Raises if prometheus_client is unavailable.

    Pass ``registry=CollectorRegistry()`` for tests to avoid duplicate-series
    collisions on the default global registry.
    """
    if Counter is None:  # pragma: no cover
        raise RuntimeError("prometheus_client not installed; install with `uv add prometheus-client`")
    kwargs = {"registry": registry} if registry is not None else {}
    return MetricsBundle(
        failures_total=Counter(
            "agentsla_failures_total",
            "Number of failed traces, partitioned by failure category.",
            labelnames=("category",),
            **kwargs,
        ),
        verify_coverage=Gauge(
            "agentsla_verify_coverage",
            "Most recent verification coverage reading (0..1).",
            **kwargs,
        ),
        classify_latency_seconds=Histogram(
            "agentsla_classify_latency_seconds",
            "End-to-end classifier latency in seconds.",
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            **kwargs,
        ),
        registry=registry,
    )


def on_classify_callback(metrics: MetricsBundle):
    """Return a callback the Classifier can invoke to bump the counter."""

    def _cb(result: Any) -> None:
        cat = getattr(result, "category", None)
        if cat is None:
            return
        # FailureCategory is a str enum. ``str(member)`` returns the
        # Enum-name form ("FailureCategory.HALLUCINATED_FACT") because
        # Enum overrides __str__; use ``.value`` to get the lowercase string.
        label = cat.value if hasattr(cat, "value") else str(cat)
        metrics.failures_total.labels(category=label).inc()

    return _cb


def on_verdict_callback(metrics: MetricsBundle):
    """Return a callback the VerificationGate can invoke to set the gauge."""

    def _cb(coverage: float) -> None:
        metrics.verify_coverage.set(float(coverage))

    return _cb


__all__ = [
    "MetricsBundle",
    "build_metrics",
    "on_classify_callback",
    "on_verdict_callback",
]
