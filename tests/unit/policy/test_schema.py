"""Policy schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentsla.policy.egress import EgressRule
from agentsla.policy.schema import Policy, ToolRule


def test_tool_rule_minimal_valid() -> None:
    r = ToolRule(name="fetch")
    assert r.max_calls is None
    assert r.json_schema is None


def test_tool_rule_json_schema_required() -> None:
    r = ToolRule(name="compute", json_schema='{"type":"object"}', max_calls=3)
    assert r.json_schema == '{"type":"object"}'
    assert r.max_calls == 3


def test_tool_rule_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ToolRule(name="x", bogus=1)


def test_egress_rule_frozen() -> None:
    r = EgressRule(name="ssn", regex=r"\d{3}-\d{2}-\d{4}")
    with pytest.raises(ValidationError):
        r.name = "other"


def test_policy_defaults() -> None:
    p = Policy(allowed_tools=["fetch"])
    assert p.tool_rules == []
    assert p.egress_rules == []
    assert p.max_calls_per_trace == 20
    assert p.mode == "enforce"


def test_policy_rejects_zero_max_calls() -> None:
    with pytest.raises(ValidationError):
        Policy(allowed_tools=["fetch"], max_calls_per_trace=0)


def test_policy_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Policy(allowed_tools=["fetch"], surprise="oops")
