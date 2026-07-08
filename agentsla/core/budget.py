"""BudgetManager (BUDGET-01, BUDGET-02).

Tracks per-trace spend against an explicit budget document and emits
:class:`BudgetExceededError` when a threshold is breached. The gate
does NOT crash the loop — caller decides whether to fall through to
the next :class:`DegradationLevel` or abort.

Why a discrete enum vs a soft "warning" mode: the SPEC defines SLO
levels (FULL / REDUCED / MINIMAL / EMERGENCY) as separate behaviors;
we'd rather raise a typed exception that maps cleanly to a level than
encode degradation in ad-hoc return values.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from agentsla.core.events import ToolResult


class DegradationLevel(str, Enum):
    """Discrete operating levels under budget pressure."""

    FULL = "full"
    REDUCED = "reduced"
    MINIMAL = "minimal"
    EMERGENCY = "emergency"


class BudgetExceededError(Exception):
    """Raised when the budget threshold is breached.

    Carries the breach details (level, metric, observed vs. ceiling)
    so callers can map to a degradation strategy without re-parsing
    the manager state.
    """

    def __init__(
        self,
        *,
        metric: str,
        observed: float,
        ceiling: float,
        level: DegradationLevel,
    ) -> None:
        super().__init__(
            f"budget breach: {metric} observed={observed:.4f} ceiling={ceiling:.4f} level={level.value}"
        )
        self.metric = metric
        self.observed = observed
        self.ceiling = ceiling
        self.level = level


@dataclass
class BudgetSpec:
    """Declarative budget document."""

    max_tokens: int = 50_000
    max_cost_usd: float = 1.00
    max_calls: int = 50
    max_wall_time: timedelta = field(default_factory=lambda: timedelta(seconds=120))

    def levels(self) -> dict[DegradationLevel, dict[str, float]]:
        """Predefined degradation thresholds (% of ceiling at which level kicks in)."""
        return {
            DegradationLevel.REDUCED: {"tokens": 0.50, "cost": 0.50},
            DegradationLevel.MINIMAL: {"tokens": 0.75, "cost": 0.75},
            DegradationLevel.EMERGENCY: {"tokens": 0.90, "cost": 0.90},
        }


class BudgetManager:
    """Per-trace budget enforcer.

    Two integration points:
      * :meth:`record_call` — called once per tool call (pre-execution).
      * :meth:`record_tool_result` — called once per tool result
        (post-execution). Carries token/cost deltas supplied by the
        adapter (in our hermetic rawloop these are zero).

    Both raise :class:`BudgetExceededError` when a threshold fires.
    """

    def __init__(self, spec: BudgetSpec | None = None) -> None:
        self.spec = spec or BudgetSpec()
        self._started: datetime | None = None
        self._tokens: defaultdict[str, int] = defaultdict(int)
        self._cost: defaultdict[str, float] = defaultdict(float)
        self._calls: defaultdict[str, int] = defaultdict(int)

    def start(self, started_at: datetime | None = None) -> None:
        self._started = started_at or datetime.now(tz=UTC)

    def record_call(self, trace_id: str) -> None:
        if self._started is None:
            self.start()
        self._calls[trace_id] += 1
        self._check_call_count(trace_id)

    def record_tool_result(
        self,
        trace_id: str,
        result: ToolResult,
        *,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._tokens[trace_id] += tokens_used
        self._cost[trace_id] += cost_usd
        if result.latency_ms > 0:
            self._check_wall_time(trace_id, result.latency_ms)
        self._check_tokens(trace_id)
        self._check_cost(trace_id)

    def level(self, trace_id: str) -> DegradationLevel:
        """Compute the current degradation level from observations."""
        cur_tok = self._tokens[trace_id]
        cur_cost = self._cost[trace_id]
        tok_frac = cur_tok / max(1, self.spec.max_tokens)
        cost_frac = cur_cost / max(1e-9, self.spec.max_cost_usd)
        worst = max(tok_frac, cost_frac)
        if worst < 0.50:
            return DegradationLevel.FULL
        if worst < 0.75:
            return DegradationLevel.REDUCED
        if worst < 0.90:
            return DegradationLevel.MINIMAL
        return DegradationLevel.EMERGENCY

    def snapshot(self, trace_id: str) -> dict[str, Any]:
        return {
            "tokens": self._tokens[trace_id],
            "cost_usd": self._cost[trace_id],
            "calls": self._calls[trace_id],
            "level": self.level(trace_id).value,
        }

    def _check_call_count(self, trace_id: str) -> None:
        ceiling = self.spec.max_calls
        observed = self._calls[trace_id]
        if observed > ceiling:
            raise BudgetExceededError(
                metric="calls",
                observed=observed,
                ceiling=ceiling,
                level=DegradationLevel.EMERGENCY,
            )

    def _check_tokens(self, trace_id: str) -> None:
        ceiling = self.spec.max_tokens
        observed = self._tokens[trace_id]
        if observed > ceiling:
            raise BudgetExceededError(
                metric="tokens",
                observed=observed,
                ceiling=ceiling,
                level=self.level(trace_id),
            )

    def _check_cost(self, trace_id: str) -> None:
        ceiling = self.spec.max_cost_usd
        observed = self._cost[trace_id]
        if observed > ceiling:
            raise BudgetExceededError(
                metric="cost_usd",
                observed=observed,
                ceiling=ceiling,
                level=self.level(trace_id),
            )

    def _check_wall_time(self, trace_id: str, latency_ms: float) -> None:
        ceiling = self.spec.max_wall_time.total_seconds() * 1000.0
        if self._started is not None:
            elapsed = (
                datetime.now(tz=UTC) - self._started
            ).total_seconds() * 1000.0
            # Use the larger of (elapsed wall, reported tool latency) so
            # downstream tools can't underreport and bypass the ceiling.
            observed = max(elapsed, latency_ms)
        else:
            observed = latency_ms
        if observed > ceiling:
            raise BudgetExceededError(
                metric="wall_time_ms",
                observed=observed,
                ceiling=ceiling,
                level=self.level(trace_id),
            )


def iter_breaches(spec: BudgetSpec) -> Iterable[DegradationLevel]:
    """Yield degradation levels in ascending severity order."""
    yield DegradationLevel.REDUCED
    yield DegradationLevel.MINIMAL
    yield DegradationLevel.EMERGENCY


__all__ = [
    "BudgetExceededError",
    "BudgetManager",
    "BudgetSpec",
    "DegradationLevel",
    "iter_breaches",
]
