"""Verification chain — composes verifiers into one :class:`ChainResult`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentsla.verify.base import ClaimVerdict, Verifier


@dataclass
class ChainResult:
    """Composite result of running multiple verifiers."""

    verifiers: list[str]
    claims: list[ClaimVerdict] = field(default_factory=list)
    coverage_threshold: float = 1.0

    @property
    def total(self) -> int:
        return len(self.claims)

    @property
    def verified(self) -> int:
        return sum(1 for c in self.claims if c.status == "verified")

    @property
    def incorrect(self) -> int:
        return sum(1 for c in self.claims if c.status == "incorrect")

    @property
    def coverage(self) -> float:
        if not self.claims:
            return 1.0
        return self.verified / self.total

    @property
    def passed(self) -> bool:
        if not self.claims:
            return True
        return self.incorrect == 0 and self.coverage >= self.coverage_threshold


class VerificationChain:
    """Compose verifiers + source resolver into one final ChainResult."""

    def __init__(
        self,
        verifiers: list[Verifier],
        *,
        coverage_threshold: float = 1.0,
    ) -> None:
        self.verifiers = verifiers
        self.coverage_threshold = coverage_threshold

    def run(self, trace: Any, final_answer: str) -> ChainResult:
        claims: list[ClaimVerdict] = []
        names: list[str] = []
        for v in self.verifiers:
            names.append(type(v).__name__)
            claims.extend(v.verify(trace, final_answer))
        return ChainResult(
            verifiers=names,
            claims=claims,
            coverage_threshold=self.coverage_threshold,
        )


__all__ = ["ChainResult", "VerificationChain"]
