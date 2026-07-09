"""VerificationGate — bridge between :class:`VerificationChain` and
:class:`agentsla.core.trace.TraceWriter`.

Phase 3 deliverable: runs the chain, builds a :class:`Verdict` event with
proper trace_id/seq, appends it to the trace, and returns the
:class:`ChainResult` to the caller.

The gate is *not* a :class:`RuntimeHooks` — it runs after the adapter
finishes, because it needs the final answer text. Hook signature
``on_final_answer(trace, verdict)`` accepts an optional verdict; the
adapter calls :meth:`run` here to produce it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from agentsla.core.events import ClaimVerdict as EventClaimVerdict
from agentsla.core.events import Verdict
from agentsla.core.trace import TraceWriter
from agentsla.verify import ChainResult, VerificationChain


@dataclass
class GateResult:
    """Return value of :meth:`VerificationGate.run`."""

    verdict: Verdict
    chain: ChainResult


class VerificationGate:
    """Run a :class:`VerificationChain` and emit a :class:`Verdict` event.

    Args:
        chain: Composed verifier chain (numeric, grounding, schema, …).
        writer: Open :class:`TraceWriter`. Caller owns lifecycle.
        verifier: Event-shape ``verifier`` tag (one of the literals).
    """

    def __init__(
        self,
        chain: VerificationChain,
        writer: TraceWriter,
        *,
        verifier: str = "composite",
    ) -> None:
        self.chain = chain
        self.writer = writer
        self.verifier = verifier

    def run(self, trace_id: UUID, final_answer: str) -> GateResult:
        """Run chain; build Verdict; append to writer; return both."""
        result = self.chain.run(trace_id, final_answer)
        next_seq = self.writer.next_seq(trace_id)
        verdict = self._build_verdict(trace_id, next_seq, result)
        self.writer.append(verdict)
        return GateResult(verdict=verdict, chain=result)

    # ----- internals -----

    def _build_verdict(self, trace_id: UUID, seq: int, result: ChainResult) -> Verdict:
        per_claim: list[EventClaimVerdict] = []
        for c in result.claims:
            # Skip claims without numeric content for the event shape
            # (Phase 3 keeps events numeric-only).
            if not isinstance(c.expected, (int, float)) and c.expected is not None:
                continue
            if not isinstance(c.observed, (int, float)) and c.observed is not None:
                continue
            per_claim.append(
                EventClaimVerdict(
                    claim_text=c.claim,
                    passed=c.status == "verified",
                    expected=None if c.expected is None else str(c.expected),
                    actual=None if c.observed is None else str(c.observed),
                    detail=f"status={c.status}; confidence={c.confidence:.2f}",
                )
            )
        return Verdict(
            verdict_id=uuid4(),
            trace_id=trace_id,
            seq=seq,
            verifier=self.verifier,  # type: ignore[arg-type]
            verified=result.passed,
            coverage=result.coverage,
            per_claim=per_claim,
        )


__all__ = ["GateResult", "VerificationGate"]
