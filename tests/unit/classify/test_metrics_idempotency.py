"""Idempotency pin for ``build_metrics()``.

Failure mode this guards against: prometheus_client raises
``ValueError: Duplicated timeseries in CollectorRegistry: {...}`` when
the same metric name is registered twice on the same registry. The
bench harness calls ``build_metrics()`` at module top to register a
process-level singleton; pytest-cov + sibling test files in
``tests/unit/bench/`` can re-execute the module top, which would
otherwise blow up the second time and cascade into 13+ downstream test
failures. The contract from the metric side: ``build_metrics()`` is
idempotent under repeated calls with the same registry argument.
"""

from __future__ import annotations

from agentsla.classify.metrics import build_metrics


def test_build_metrics_idempotent_under_same_default_registry() -> None:
    """Repeated calls return the same bundle without raising."""
    a = build_metrics()
    b = build_metrics()
    assert a is b, "build_metrics() must return the same bundle for the default global registry"
    assert a.failures_total is b.failures_total
    assert a.verify_coverage is b.verify_coverage
    assert a.classify_latency_seconds is b.classify_latency_seconds


def test_build_metrics_uses_distinct_registry_per_collector_call() -> None:
    """Caller-supplied registry keeps isolates the call from the global pool."""
    try:
        from prometheus_client import CollectorRegistry
    except ImportError:  # pragma: no cover — exercised only when extra absent
        import pytest

        pytest.skip("prometheus_client not installed (install with `uv add prometheus-client`)")
    fresh = CollectorRegistry()
    bundle = build_metrics(registry=fresh)
    assert bundle.registry is fresh
    # Two separate CollectorRegistry instances → two separate bundles.
    other_fresh = CollectorRegistry()
    other_bundle = build_metrics(registry=other_fresh)
    assert bundle is not other_bundle, "each CollectorRegistry call gets its own bundle"
    assert bundle.failures_total is not other_bundle.failures_total
