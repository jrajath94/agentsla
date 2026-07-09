"""Shared verifier types (ClaimVerdict, Verifier Protocol).

Split out from :mod:`agentsla.verify.__init__` to avoid circular imports
when ``numeric.py`` is imported from the package's ``__init__``.

Anything in this module is part of the public surface — re-exported from
:mod:`agentsla.verify`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class Verifier(Protocol):
    """Single claim source: returns a list of :class:`ClaimVerdict`."""

    def verify(self, trace: Any, final_answer: str) -> list["ClaimVerdict"]: ...


@dataclass
class ClaimVerdict:
    """Per-claim result."""

    claim: str
    status: str  # "verified" | "incorrect" | "unverified"
    observed: Any = None
    expected: Any = None
    confidence: float = 1.0


__all__ = ["ClaimVerdict", "Verifier"]