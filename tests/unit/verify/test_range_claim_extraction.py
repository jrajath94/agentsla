"""F7. Range claims with per-endpoint multipliers (PRD §2.1 F7 / TRD §1.3).

A range like ``$4.2M-$4.5M`` currently parses only the first endpoint
(the ``M`` suffix slips into the second half, breaking float parsing).
Per-endpoint currency + multiplier prefixes must be honored on BOTH
endpoints, and the parser must apply the multiplier (K=1e3, M=1e6,
B=1e9) so coverage stops inflating the "unverifiable" tally.

Regression guards:
  * ``4--5`` must NOT parse as ``(4, -5)`` (semantic-escape hardening)
  * ``4-4`` (degenerate equal endpoints) still skipped
  * simple range ``4-5`` unchanged

Test-first per red/green TDD.
"""

from __future__ import annotations

import pytest

from agentsla.verify.claims import extract_numeric_claims


def _ranges_only(claims):
    """Return only the ``kind == "range"`` claims (ignoring int/float/etc)."""
    return [c for c in claims if c.kind == "range"]


class TestRangeClaimExtraction:
    """Per-endpoint currency + multiplier support for range claims."""

    def test_range_claim_handles_per_endpoint_multiplier(self) -> None:
        """'$4.2M-$4.5M' emits ONE range claim, both endpoints scaled by 1e6.

        Without the fix, the ``M`` suffix on the second endpoint leaves
        ``_parse_range`` with a non-numeric token, returning ``(None,
        None)`` and dropping the claim silently. Coverage inflates;
        the gate never sees this verifiable claim.
        """
        claims = extract_numeric_claims("Revenue is $4.2M-$4.5M this quarter.")
        ranges = _ranges_only(claims)

        assert len(ranges) == 1, f"expected exactly one range claim, got {len(ranges)}: {ranges}"
        low, high = ranges[0].value
        assert low == pytest.approx(4_200_000), f"first endpoint $4.2M must scale to 4_200_000; got {low!r}"
        assert high == pytest.approx(4_500_000), f"second endpoint $4.5M must scale to 4_500_000; got {high!r}"

    def test_range_claim_handles_currency_mix(self) -> None:
        """Each test text emits a parseable range claim with both endpoints.

        ``$100-$200`` is USD on both sides; ``€100-€200`` is EUR on both.
        The value scale is unchanged for currency-only — the symbols
        are stripped before float parsing. The fix must not regress
        these common, currency-only patterns.
        """
        for text, expected in (
            ("Amount was $100-$200.", (100.0, 200.0)),
            ("Total cost €100-€200 today.", (100.0, 200.0)),
        ):
            claims = extract_numeric_claims(text)
            ranges = _ranges_only(claims)
            assert len(ranges) == 1, f"{text!r}: expected one range claim; got {ranges}"
            assert ranges[0].value == expected, f"{text!r}: endpoints {ranges[0].value!r} != {expected!r}"

    def test_range_claim_unchanged_for_simple_range(self) -> None:
        """'4-5' parses as (4.0, 5.0) — no semantic-escape regression.

        The new multiplier token must NOT loosen the sign guard on the
        second endpoint. If it did, ``4--5`` would leak through as
        ``(4, -5)`` — the security hardening from the parser-differential
        review would silently regress.
        """
        claims = extract_numeric_claims("Value is 4-5 here.")
        ranges = _ranges_only(claims)
        assert len(ranges) == 1, f"simple '4-5' must parse as one range; got {ranges}"
        low, high = ranges[0].value
        assert low == pytest.approx(4.0)
        assert high == pytest.approx(5.0)

    def test_range_claim_handles_comma_separated(self) -> None:
        """'$1,200-$1,800' parses both endpoints with comma stripping.

        Commas inside numbers (thousand-separators) are stripped before
        float parsing. Both endpoints must survive. Without the fix,
        the second endpoint (which now ALSO permits currency + comma +
        multiplier) could break; this test pins the common case.
        """
        claims = extract_numeric_claims("Forecast $1,200-$1,800 for Q2.")
        ranges = _ranges_only(claims)
        assert len(ranges) == 1, f"expected one range claim; got {ranges}"
        low, high = ranges[0].value
        assert low == pytest.approx(1200.0), f"$1,200 must parse as 1200.0; got {low!r}"
        assert high == pytest.approx(1800.0), f"$1,800 must parse as 1800.0; got {high!r}"

    def test_range_claim_still_rejects_double_dash_semantic_escape(self) -> None:
        """Regression guard: '4--5' must NOT parse as (4, -5).

        The hardening from the parser-differential security review
        must not be lost when extending the regex to allow per-endpoint
        suffixes. If it were, downstream verifiers would treat
        ``(4, -5)`` as legitimate — a silent correctness regression.
        """
        claims = extract_numeric_claims("Value 4--5 here.")
        ranges = _ranges_only(claims)
        assert ranges == [], f"double-dash '4--5' must not parse as range; got {ranges}"
