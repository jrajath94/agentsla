"""F6. Per-verifier tolerance config (PRD §2.1 F6 / TRD §1.2).

The hardcoded 1e-2 default was wrong for finops (financial accuracy
wants 1e-6) and for doc-QA (citation copy-paste wants 1e-2). v1 makes
tolerance a per-``NumericVerifier`` constructor kwarg, defaulting to
``1e-6``.

These tests pin the spec:
  * the constructor accepts and stores a per-instance ``tolerance``
  * the default is strict ``1e-6`` (regression guard)
  * a stricter tolerance rejects a perturbation the looser accepts
  * ``tolerance=0.0`` enforces exact equality (no fuzzy float match)

Written test-first per the red/green protocol. NumericVerifier already
honors the kwarg; the tests are the contract.
"""

from __future__ import annotations

import pytest

from agentsla.verify.numeric import DEFAULT_TOLERANCE, NumericVerifier


class TestNumericVerifierToleranceConfig:
    """Per-instance tolerance config for NumericVerifier (v1 F6)."""

    def test_numeric_verifier_honors_per_instance_tolerance(self) -> None:
        """tolerance=1e-6 rejects 1e-4 perturbation; tolerance=1e-2 accepts it.

        Same source_resolver on both — only the tolerance differs. The
        perturbation is 1e-4 relative to the claim value; the strict
        verifier (1e-6) flags ``incorrect``, the loose verifier (1e-2)
        flags ``verified``.
        """
        answer = "revenue = 1.0000"  # claim value = 1.0

        def src(_claim, _trace):
            return 1.0001  # perturbation = 1e-4 relative

        strict = NumericVerifier(source_resolver=src, tolerance=1e-6)
        loose = NumericVerifier(source_resolver=src, tolerance=1e-2)

        strict_verdicts = strict.verify(trace=None, final_answer=answer)
        loose_verdicts = loose.verify(trace=None, final_answer=answer)

        strict_statuses = {v.status for v in strict_verdicts}
        loose_statuses = {v.status for v in loose_verdicts}

        assert "incorrect" in strict_statuses, f"strict (1e-6) must reject 1e-4 perturbation; got {strict_verdicts}"
        assert "incorrect" not in loose_statuses, f"loose (1e-2) must accept 1e-4 perturbation; got {loose_verdicts}"
        assert "verified" in loose_statuses, f"loose (1e-2) must verify within tolerance; got {loose_verdicts}"

    def test_numeric_verifier_default_tolerance_is_strict(self) -> None:
        """Default tolerance is ``1e-6`` (regression guard for finops).

        v0.1 shipped with a hardcoded 1e-2 default that inflated
        acceptable error for financial claims. A future refactor that
        loosens the default breaks this test loud.
        """
        verifier = NumericVerifier()
        assert verifier.tolerance == pytest.approx(1e-6), f"default tolerance must be 1e-6, got {verifier.tolerance!r}"
        assert verifier.tolerance == DEFAULT_TOLERANCE, (
            f"instance tolerance must equal module DEFAULT_TOLERANCE; got {verifier.tolerance!r} vs {DEFAULT_TOLERANCE!r}"
        )

    def test_numeric_verifier_tolerance_zero_means_exact(self) -> None:
        """``tolerance=0.0`` enforces exact float equality.

        A perturbation of even one ULP must be flagged ``incorrect``;
        only identical values match. This is the finops-strict contract.
        """
        answer = "value = 100"

        def src_close(_claim, _trace):
            return 100.0 + 1e-12  # 1e-12 relative perturbation

        exact = NumericVerifier(source_resolver=src_close, tolerance=0.0)
        verdicts = exact.verify(trace=None, final_answer=answer)

        # With tolerance=0.0, even a 1e-12 perturbation must be flagged.
        incorrect = [v for v in verdicts if v.status == "incorrect"]
        assert incorrect, f"tolerance=0.0 must reject non-exact float; got {verdicts}"

    def test_numeric_verifier_tolerance_is_stored_as_attribute(self) -> None:
        """The constructor kwarg is exposed as ``self.tolerance``.

        Operators introspect the verifier (e.g., in REPL or audit logs);
        the attribute must reflect the value passed at construction,
        not be hidden behind a default keyword.
        """
        cases = [
            (1e-6, 1e-6),
            (1e-2, 1e-2),
            (1e-9, 1e-9),
            (0.0, 0.0),
        ]
        for kwarg, expected in cases:
            v = NumericVerifier(tolerance=kwarg)
            assert v.tolerance == pytest.approx(expected), f"tolerance={kwarg!r} must store as self.tolerance={expected!r}; got {v.tolerance!r}"
