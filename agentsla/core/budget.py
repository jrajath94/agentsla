"""Token and cost budget accounting.

Phase 2: Implement BudgetManager with degradation hooks.
"""

from dataclasses import dataclass
from typing import Optional, Callable


@dataclass
class BudgetManager:
    """Track token/cost/latency spend and trigger degradation."""

    max_tokens: Optional[int] = None
    max_cost: Optional[float] = None
    max_latency_ms: Optional[int] = None
    degradation_hook: Optional[Callable] = None

    def spend_tokens(self, n: int) -> bool:
        """Record token spend. Return False if budget exceeded."""
        pass

    def spend_cost(self, amount: float) -> bool:
        """Record cost. Return False if budget exceeded."""
        pass

    def check_latency(self, latency_ms: int) -> bool:
        """Check latency vs budget. Return False if exceeded."""
        pass
