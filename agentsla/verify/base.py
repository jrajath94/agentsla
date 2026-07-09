"""Shared verifier types (ClaimVerdict, Verifier Protocol).

Split out from :mod:`agentsla.verify.__init__` to avoid circular imports
when ``numeric.py`` is imported from the package's ``__init__``.

Anything in this module is part of the public surface — re-exported from
:mod:`agentsla.verify`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class Verifier(Protocol):
    """Single claim source: returns a list of :class:`ClaimVerdict`."""

    def verify(self, trace: Any, final_answer: str) -> list[InternalClaimVerdict]: ...


@dataclass
class InternalClaimVerdict:
    """Per-claim verifier result (internal layer).

    This is the dataclass form produced by verifiers. It is the
    *internal* representation; the *event* representation lives in
    :class:`agentsla.core.events.ClaimVerdict` (pydantic) and is built
    from this one by :class:`agentsla.verify.gate.VerificationGate`
    before being appended to the trace store.

    Two distinct types live in the codebase by design — the dataclass
    exists for fast, allocation-cheap verifier pipelines; the pydantic
    model is what gets persisted to DuckDB and survives round-trips.
    """

    claim: str
    status: str  # "verified" | "incorrect" | "unverified"
    observed: Any = None
    expected: Any = None
    confidence: float = 1.0


__all__ = ["InternalClaimVerdict", "Verifier"]
