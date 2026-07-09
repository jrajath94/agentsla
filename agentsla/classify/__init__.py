"""Failure classifier — 14-category taxonomy + Prometheus + Grafana.

Phase 4 deliverable. See ``docs/class-taxonomy.md`` for the source-of-truth
taxonomy table (committed before any code in this package).

Re-exports the public surface.
"""

from agentsla.classify.classifier import (
    ClassificationResult,
    Classifier,
    HeuristicContext,
    InMemoryLabelSink,
    JsonlLabelSink,
    LabelSink,
    agreement,
)
from agentsla.classify.heuristics import HEURISTIC_TRIGGERS
from agentsla.classify.judge import (
    ClaudeJudge,
    Judge,
    JudgeResult,
    PROMPT_HASH,
    PROMPT_VERSION,
    StubJudge,
    should_invoke_judge,
    summarise_events_for_judge,
)
from agentsla.classify.metrics import (
    MetricsBundle,
    build_metrics,
    on_classify_callback,
    on_verdict_callback,
)
from agentsla.classify.taxonomy import (
    CATEGORY_ORDER,
    CATEGORY_SEVERITY,
    FailureCategory,
    rank,
)

__all__ = [
    # Taxonomy
    "CATEGORY_ORDER",
    "CATEGORY_SEVERITY",
    "FailureCategory",
    "rank",
    # Heuristics
    "HEURISTIC_TRIGGERS",
    # Judge
    "ClaudeJudge",
    "Judge",
    "JudgeResult",
    "PROMPT_HASH",
    "PROMPT_VERSION",
    "StubJudge",
    "should_invoke_judge",
    "summarise_events_for_judge",
    # Classifier
    "ClassificationResult",
    "Classifier",
    "HeuristicContext",
    "InMemoryLabelSink",
    "JsonlLabelSink",
    "LabelSink",
    "agreement",
    # Metrics
    "MetricsBundle",
    "build_metrics",
    "on_classify_callback",
    "on_verdict_callback",
]