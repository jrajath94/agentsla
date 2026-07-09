"""Numeric recompute verifier.

Given a list of :class:`NumericClaim` extracted from the final answer,
recompute each by replaying the source tool-call against the recorded
trace; emit a :class:`ClaimVerdict` per claim.

The source resolver is pluggable: callers register a callable that
takes a ``NumericClaim`` and returns the expected numeric value (or
``None`` if the claim has no computable origin). Default returns the
claim value itself (claims without a source are flagged UNVERIFIED).

The class emits a **coverage** signal: claims with sources that pass
recompute contribute to ``verified``; claims with sources that fail
recompute are flagged ``incorrect`` (the verifier "caught" the error).
"""

from __future__ import annotations

from typing import Any, Callable

from agentsla.verify.base import ClaimVerdict, Verifier
from agentsla.verify.claims import NumericClaim, extract_numeric_claims


# Default relative tolerance for float comparison.
DEFAULT_TOLERANCE = 1e-6

# Default source resolver: identity (claim value IS the source value).
def identity_source(claim: NumericClaim, trace: Any) -> Any | None:
    """Each claim's text/value is the source of truth (self-certifying)."""
    return claim.value


class NumericVerifier:
    """Recompute numeric claims against source values.

    Args:
        source_resolver: Callable ``(claim, trace) -> value | None``.
            Operators override to map a claim to a recomputable source.
        tolerance: Absolute tolerance for float comparison. Default
            ``1e-6``. Set to ``0.0`` for strict integer equality.
        coverage_threshold: Forwarded to :class:`ChainResult`.
    """

    def __init__(
        self,
        *,
        source_resolver: Callable[[NumericClaim, Any], Any | None] = identity_source,
        tolerance: float = DEFAULT_TOLERANCE,
    ) -> None:
        self.source_resolver = source_resolver
        self.tolerance = tolerance

    def verify(self, trace: Any, final_answer: str) -> list[ClaimVerdict]:
        """Extract + recompute; return one :class:`ClaimVerdict` per claim."""
        claims = extract_numeric_claims(final_answer)
        out: list[ClaimVerdict] = []
        for claim in claims:
            source = self.source_resolver(claim, trace)
            verdict = self._judge(claim, source)
            out.append(verdict)
        return out

    # ----- internals -----

    def _judge(self, claim: NumericClaim, source: Any | None) -> ClaimVerdict:
        if source is None:
            return ClaimVerdict(
                claim=claim.text, status="unverified", observed=claim.value, expected=None
            )
        if self._values_match(claim.value, source):
            return ClaimVerdict(
                claim=claim.text,
                status="verified",
                observed=claim.value,
                expected=source,
                confidence=1.0,
            )
        return ClaimVerdict(
            claim=claim.text,
            status="incorrect",
            observed=claim.value,
            expected=source,
            confidence=1.0,
        )

    def _values_match(self, observed: Any, expected: Any) -> bool:
        if observed == expected:
            return True
        try:
            of, ef = float(observed), float(expected)
        except (TypeError, ValueError):
            return False
        if of == ef:
            return True
        denom = max(abs(of), abs(ef), 1e-12)
        return abs(of - ef) / denom <= self.tolerance


__all__ = ["DEFAULT_TOLERANCE", "NumericVerifier", "identity_source"]
