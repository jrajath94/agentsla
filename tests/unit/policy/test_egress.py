"""Egress regex pack — detectors fire on fixture data, not on benign text."""

from __future__ import annotations

import pytest

from agentsla.policy.egress import default_egress_rules, luhn_valid


@pytest.mark.parametrize(
    ("rule_name", "value"),
    [
        ("ssn", "SSN 123-45-6789 appeared in the form data"),
        ("aws_access_key", "AKIAIOSFODNN7EXAMPLE leaked in env"),
        ("jwt", "Authorization: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dozjgNryP4J3jVmNHlSyw"),
    ],
)
def test_default_pack_detects_fixtures(rule_name: str, value: str) -> None:
    """Each detector fires on its positive fixture."""
    rules = {r.name: r for r in default_egress_rules()}
    assert rule_name in rules, f"missing {rule_name}"
    pat = rules[rule_name].pattern
    assert pat.search(value), f"rule {rule_name!r} failed to detect {value!r}"


@pytest.mark.parametrize(
    "value",
    [
        "plain text",
        "order id 12345",
        "phone 555-123-4567",
        "date 2026-07-08",
    ],
)
def test_default_pack_no_false_positives_on_benign_text(value: str) -> None:
    """None of the detectors fire on everyday strings."""
    for rule in default_egress_rules():
        assert not rule.pattern.search(value), f"rule {rule.name!r} false-positived on {value!r}"


def test_pan_luhn_detects_valid_card() -> None:
    """Luhn-gated PAN regex: only fires for valid card numbers."""
    rules = {r.name: r for r in default_egress_rules()}
    pan_pat = rules["pan"].pattern
    # Valid Visa test number from payment industry fixtures.
    assert pan_pat.search("card 4111111111111111 vi"), "valid visa should match"
    # Same digits but reordered — also Luhn-valid.
    assert pan_pat.search("5500000000000004 visa-mc"), "valid mc should match"


def test_luhn_helper_known_values() -> None:
    assert luhn_valid("4111111111111111") is True
    assert luhn_valid("4111111111111112") is False
    assert luhn_valid("1234567890123456") is False
    assert luhn_valid("5500 0000 0000 0004".replace(" ", "")) is True
