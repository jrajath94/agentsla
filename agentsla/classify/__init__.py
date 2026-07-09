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
    PROMPT_HASH,
    PROMPT_VERSION,
    ClaudeJudge,
    Judge,
    JudgeResult,
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
    "CATEGORY_ORDER",
    "CATEGORY_SEVERITY",
    "HEURISTIC_TRIGGERS",
    "PROMPT_HASH",
    "PROMPT_VERSION",
    "ClassificationResult",
    "Classifier",
    "ClaudeJudge",
    "FailureCategory",
    "HeuristicContext",
    "InMemoryLabelSink",
    "JsonlLabelSink",
    "Judge",
    "JudgeResult",
    "LabelSink",
    "MetricsBundle",
    "StubJudge",
    "agreement",
    "build_metrics",
    "on_classify_callback",
    "on_verdict_callback",
    "rank",
    "should_invoke_judge",
    "summarise_events_for_judge",
]
