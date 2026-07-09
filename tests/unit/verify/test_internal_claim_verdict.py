"""Pin the renamed public surface of the internal verify layer.

After the schema-unification commit, the verify package exports
``InternalClaimVerdict`` (the dataclass) instead of ``ClaimVerdict``
(which collides with the pydantic event-shape class in
``agentsla.core.events``). This test guards the rename.
"""

from __future__ import annotations


def test_internal_claim_verdict_importable_from_verify_package() -> None:
    from agentsla.verify import InternalClaimVerdict

    assert InternalClaimVerdict.__module__ == "agentsla.verify.base"


def test_internal_claim_verdict_importable_from_verify_base() -> None:
    from agentsla.verify.base import InternalClaimVerdict

    assert InternalClaimVerdict.__dataclass_fields__["claim"].name == "claim"
    assert InternalClaimVerdict.__dataclass_fields__["status"].name == "status"
    assert InternalClaimVerdict.__dataclass_fields__["observed"].name == "observed"
    assert InternalClaimVerdict.__dataclass_fields__["expected"].name == "expected"
    assert InternalClaimVerdict.__dataclass_fields__["confidence"].name == "confidence"


def test_old_name_claim_verdict_removed_from_verify_base() -> None:
    """The old dataclass name must not exist on agentsla.verify.base."""
    import agentsla.verify.base as base_mod

    assert not hasattr(base_mod, "ClaimVerdict"), (
        "agentsla.verify.base.ClaimVerdict was renamed to InternalClaimVerdict; "
        "the old name collides with agentsla.core.events.ClaimVerdict."
    )
