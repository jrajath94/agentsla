"""Prometheus metrics: build + on_classify + on_verdict callbacks."""

from __future__ import annotations

import pytest

prometheus_client = pytest.importorskip("prometheus_client")

from prometheus_client import CollectorRegistry  # noqa: E402
from prometheus_client import generate_latest  # noqa: E402

from agentsla.classify import FailureCategory  # noqa: E402
from agentsla.classify.classifier import ClassificationResult  # noqa: E402
from agentsla.classify.metrics import (  # noqa: E402
    MetricsBundle,
    build_metrics,
    on_classify_callback,
    on_verdict_callback,
)


@pytest.fixture()
def metrics() -> MetricsBundle:
    """Fresh registry per test (avoids duplicate-series collisions on REGISTRY)."""
    return build_metrics(registry=CollectorRegistry())


class TestBuildMetrics:
    def test_build_returns_bundle(self, metrics) -> None:
        assert metrics.failures_total is not None
        assert metrics.verify_coverage is not None
        assert metrics.classify_latency_seconds is not None


class TestCallbacks:
    def test_on_classify_increments_counter(self, metrics) -> None:
        cb = on_classify_callback(metrics)
        result = ClassificationResult(
            trace_id="t1",
            category=FailureCategory.HALLUCINATED_FACT,
            confidence=0.9,
            source="heuristic",
        )
        cb(result)
        # Verify via the rendered Prometheus text: the labelled sample appears.
        output = generate_latest(metrics.registry).decode()
        assert 'category="hallucinated_fact"' in output
        assert "agentsla_failures_total{category=" in output

    def test_on_classify_none_skips(self, metrics) -> None:
        cb = on_classify_callback(metrics)
        result = ClassificationResult(
            trace_id="t1",
            category=None,
            confidence=0.0,
            source="none",
        )
        # Must not raise; no labels created.
        cb(result)
        output = generate_latest(metrics.registry).decode()
        assert "agentsla_failures_total{" not in output

    def test_on_verdict_sets_gauge(self, metrics) -> None:
        cb = on_verdict_callback(metrics)
        cb(0.85)
        cb(0.5)
        output = generate_latest(metrics.registry).decode()
        # Most recent value wins: 0.5
        assert "agentsla_verify_coverage 0.5" in output


class TestScrapable:
    def test_scrape_returns_series(self, metrics) -> None:
        cb = on_classify_callback(metrics)
        cb(
            ClassificationResult(
                trace_id="t1",
                category=FailureCategory.RETRY_LOOP,
                confidence=0.9,
                source="heuristic",
            )
        )
        cb(
            ClassificationResult(
                trace_id="t2",
                category=FailureCategory.RETRY_LOOP,
                confidence=0.9,
                source="heuristic",
            )
        )
        on_verdict_callback(metrics)(0.95)

        output = generate_latest(metrics.registry).decode()
        assert "agentsla_failures_total" in output
        assert "agentsla_verify_coverage" in output
        assert 'category="retry_loop"' in output