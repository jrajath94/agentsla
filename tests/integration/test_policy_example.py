"""Integration test: the shipped ``examples/policy.yaml`` loads cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentsla.policy import Policy, load_policy


EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "policy.yaml"


def test_example_policy_yaml_exists() -> None:
    assert EXAMPLE.exists(), f"missing example policy file at {EXAMPLE}"


def test_example_policy_loads_as_policy() -> None:
    p = load_policy(EXAMPLE)
    assert isinstance(p, Policy)
    assert "web_search" in p.allowed_tools
    assert "calculator" in p.allowed_tools
    assert p.max_calls_per_trace == 20
    assert p.mode == "enforce"


def test_example_policy_includes_default_and_tenant_rules() -> None:
    p = load_policy(EXAMPLE)
    names = {r.name for r in p.egress_rules}
    # Default pack names.
    assert {"pan", "ssn", "aws_key", "jwt"}.issubset(names)
    # Tenant-specific detector from the example.
    assert "internal_project_code" in names


def test_example_policy_tool_rules_have_schemas() -> None:
    p = load_policy(EXAMPLE)
    by_name = {r.name: r for r in p.tool_rules}
    assert by_name["web_search"].max_calls == 5
    assert by_name["web_search"].json_schema is not None
    assert by_name["calculator"].json_schema is not None
    assert by_name["calculator"].max_calls is None  # unbounded