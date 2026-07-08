"""Default egress regex pack (POLICY-02).

These detectors scan tool-call argument values for sensitive data they
should not be exfiltrating. The pack is deliberately small + curated:

  * SSN — US Social Security numbers (\\d{3}-\\d{2}-\\d{4})
  * PAN — Luhn-valid 13-19 digit card numbers (no hyphens, contiguous)
  * AWS access key id (starts with ``AKIA`` + 16 uppercase alnum)
  * JWT (three dot-separated base64url segments)

Each :class:`EgressRule` is loaded once, compiled, and frozen. The
policy gate tests each tool-call ``args.values()`` against every rule
and accumulates hits; the operator decides whether to DENY or REWRITE
based on ``severity``.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

# Constrained string alias: min_length=1, no leading/trailing whitespace.
TypeIdStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class EgressRule(BaseModel):
    """One named regex detector.

    The compiled ``pattern`` is built once at validation time and
    stored alongside ``regex`` for round-tripping in diagnostics.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: TypeIdStr
    regex: TypeIdStr
    severity: Literal["deny", "rewrite"] = "deny"

    @property
    def pattern(self) -> re.Pattern[str]:
        return re.compile(self.regex)


# ---- default pack --------------------------------------------------------


def _ssn() -> EgressRule:
    # Match 9-digit SSNs with hyphen separators only (no plain 9-digit matches
    # so we do not flag arbitrary number runs).
    return EgressRule(name="ssn", regex=r"\b\d{3}-\d{2}-\d{4}\b", severity="deny")


def _pan_luhn() -> EgressRule:
    # Match a contiguous 13-19 digit run, then Luhn-check via a small helper
    # in :mod:`agentsla.policy.gate`. The regex here is intentionally loose;
    # the gate enforces Luhn validity before raising the hit.
    return EgressRule(name="pan", regex=r"\b(?:\d[ -]?){13,19}\b", severity="deny")


def _aws_key() -> EgressRule:
    return EgressRule(
        name="aws_access_key",
        regex=r"\bAKIA[0-9A-Z]{16}\b",
        severity="deny",
    )


def _jwt() -> EgressRule:
    # Three dot-separated base64url-encoded segments; each segment >= 4 chars.
    return EgressRule(
        name="jwt",
        regex=r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b",
        severity="deny",
    )


def default_egress_rules() -> list[EgressRule]:
    """Return the shipped egress rule pack.

    Order matters: ``pan`` runs *before* ``ssn`` because the PAN regex
    is broader (13-19 digits with optional separators) and would
    otherwise absorb the SSN's 9-digit run as a partial match.
    """
    return [_pan_luhn(), _ssn(), _aws_key(), _jwt()]


# Luhn helper — used by :mod:`agentsla.policy.gate` to validate PAN hits.
def luhn_valid(digits: str) -> bool:
    """Standard Luhn check on a digit string (spaces/dashes stripped)."""
    s = re.sub(r"[^0-9]", "", digits)
    if not 13 <= len(s) <= 19:
        return False
    total = 0
    parity = (len(s) - 2) % 2
    for i, ch in enumerate(s):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


__all__ = ["EgressRule", "TypeIdStr", "default_egress_rules", "luhn_valid"]
