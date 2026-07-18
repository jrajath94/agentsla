"""Verification gate — post-execution claim recomputation.

Phase 3 deliverable. Composes one or more verifiers into a single
:class:`ChainResult` with a coverage metric. NumericVerifier is the one
shipped verifier; the chain accepts any Verifier implementation.

Per VERIFY-SPEC: only declarative numeric / structured claims
count toward coverage. Opinions, qualitative facts, and uncited
external claims do not (yet) surface in coverage.
"""

from __future__ import annotations

from agentsla.verify.base import InternalClaimVerdict, Verifier
from agentsla.verify.chain import ChainResult, VerificationChain
from agentsla.verify.gate import GateResult, VerificationGate
from agentsla.verify.numeric import (
    DEFAULT_TOLERANCE,
    NumericVerifier,
    identity_source,
)

__all__ = [
    "DEFAULT_TOLERANCE",
    "ChainResult",
    "GateResult",
    "InternalClaimVerdict",
    "NumericVerifier",
    "VerificationChain",
    "VerificationGate",
    "Verifier",
    "identity_source",
]
