"""Verify claims + numeric verifier."""

from __future__ import annotations

import pytest

from agentsla.verify import ClaimVerdict, VerificationChain
from agentsla.verify.claims import NumericClaim, extract_numeric_claims
from agentsla.verify.numeric import NumericVerifier, identity_source


class TestExtract:
    def test_extracts_integer(self) -> None:
        c = extract_numeric_claims("Total is 42 lines.")
        assert len(c) >= 1
        assert any(cl.kind == "int" and cl.value == 42 for cl in c)

    def test_extracts_float(self) -> None:
        c = extract_numeric_claims("The rate is 3.14.")
        assert any(cl.kind == "float" and cl.value == 3.14 for cl in c)

    def test_extracts_currency(self) -> None:
        c = extract_numeric_claims("Revenue: $1,200")
        assert any(cl.kind == "currency" and cl.value == 1200 for cl in c)

    def test_extracts_percent(self) -> None:
        c = extract_numeric_claims("Discount: 12.5%")
        assert any(cl.kind == "percent" and cl.value == 12.5 for cl in c)

    def test_extracts_expression(self) -> None:
        c = extract_numeric_claims("Sum 2 * 3 + 1 gives the count.")
        assert any(cl.kind == "expression" for cl in c)

    def test_no_false_positives_on_words(self) -> None:
        c = extract_numeric_claims("Order summary shipped today, no numbers.")
        # The trailing "today," is text-only; the only integer-shaped string is "shipped"...
        # extract_numeric_claims should return empty for prose-only text.
        assert not any(cl.kind in {"int", "float"} for cl in c)


class TestNumericVerifier:
    def test_pure_self_certifying_claims_pass(self) -> None:
        v = NumericVerifier()
        claims = v.verify(trace=None, final_answer="answer = 42")
        # The "42" claim with identity source is verified (self-matches).
        assert any(c.status == "verified" for c in claims)

    def test_unverified_when_resolver_returns_none(self) -> None:
        def none_src(_claim: NumericClaim, _trace: object) -> None:
            return None

        v = NumericVerifier(source_resolver=none_src)
        claims = v.verify(trace=None, final_answer="answer = 42")
        # All numeric claims become UNVERIFIED.
        assert any(c.status == "unverified" for c in claims)
        assert not any(c.status == "incorrect" for c in claims)

    def test_incorrect_when_source_mismatches(self) -> None:
        """Recompute says 100; answer says 42 — gate catches it."""
        v = NumericVerifier(
            source_resolver=lambda claim, _trace: 100,
            tolerance=0.0,
        )
        claims = v.verify(trace=None, final_answer="value=42")
        incorrect = [c for c in claims if c.status == "incorrect"]
        assert any(c for c in incorrect if c.observed == 42 and c.expected == 100)

    def test_tolerance_accepts_close_floats(self) -> None:
        v = NumericVerifier(
            source_resolver=lambda _c, _t: 3.1400005, tolerance=1e-4
        )
        claims = v.verify(trace=None, final_answer="pi=3.14")
        # Both 3.14 (int extract) and 3.14 (float) should be verified within tol.
        assert any(c.status == "verified" for c in claims)


class TestChain:
    def test_chain_emits_coverage(self) -> None:
        chain = VerificationChain(verifiers=[NumericVerifier()])
        result = chain.run(trace=None, final_answer="answer=42")
        assert 0.0 <= result.coverage <= 1.0
        assert result.verifiers == ["NumericVerifier"]

    def test_chain_with_no_claims_passes(self) -> None:
        chain = VerificationChain(verifiers=[NumericVerifier()])
        result = chain.run(trace=None, final_answer="no numbers here at all.")
        assert result.total == 0
        assert result.passed is True
        assert result.coverage == 1.0

    def test_incorrect_claim_fails_chain(self) -> None:
        chain = VerificationChain(
            verifiers=[
                NumericVerifier(
                    source_resolver=lambda c, _t: 100, tolerance=0.0
                )
            ]
        )
        result = chain.run(trace=None, final_answer="value=42")
        assert any(c.status == "incorrect" for c in result.claims)
        assert result.passed is False


# 50-case seeded test — 50 answers with injected errors.
@pytest.mark.parametrize("case", list(range(50)))
def test_seeded_error_50_cases(case: int) -> None:
    """Caught-rate requirement: ≥90% of seeded wrong claims must be flagged."""
    # Layout:
    # * 20 clean answers — verifier must not flag incorrect (0 false corrections).
    # * 30 injected wrong answers — verifier must flag at least one claim.
    if case < 20:
        # Correct: claim = source. Use a custom source mapping to the
        # numeric content of the answer.
        answer = f"value={100 + case}"
        src = lambda _c, _t: 100 + case
        v = NumericVerifier(source_resolver=src)
        claims = v.verify(trace=None, final_answer=answer)
        assert not any(c.status == "incorrect" for c in claims)
    else:
        # Injected wrong: claim != source.
        wrong_value = case + 1
        answer = f"value={wrong_value}"
        src = lambda _c, _t: 9999
        v = NumericVerifier(source_resolver=src, tolerance=0.0)
        claims = v.verify(trace=None, final_answer=answer)
        assert any(c.status == "incorrect" for c in claims)


def test_seeded_summary() -> None:
    """Cumulative summary: ≥90% caught, 0 false corrections across the 50."""
    false_corrections = 0
    caught = 0
    total_wrong = 0
    for case in range(50):
        if case < 20:
            answer = f"value={100 + case}"
            claims = NumericVerifier(
                source_resolver=lambda _c, _t: 100 + case
            ).verify(trace=None, final_answer=answer)
            if any(c.status == "incorrect" for c in claims):
                false_corrections += 1
        else:
            total_wrong += 1
            answer = f"value={case + 1}"
            claims = NumericVerifier(
                source_resolver=lambda _c, _t: 9999, tolerance=0.0
            ).verify(trace=None, final_answer=answer)
            if any(c.status == "incorrect" for c in claims):
                caught += 1
    assert caught / total_wrong >= 0.90, f"caught {caught}/{total_wrong}"
    assert false_corrections == 0
