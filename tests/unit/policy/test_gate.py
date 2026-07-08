"""PolicyGate — the 20-case violation matrix."""

from __future__ import annotations

from typing import Any

import pytest

from agentsla.adapters.base import HookDecision
from agentsla.core.events import (
    ToolCall,
    canonical_args_hash,
    now_timestamp,
)
from agentsla.core.types import new_call_id, new_trace_id
from agentsla.policy.egress import default_egress_rules
from agentsla.policy.gate import PolicyGate
from agentsla.policy.schema import Policy, ToolRule


def _make_call(
    *,
    tool: str = "fetch",
    args: dict[str, Any] | None = None,
    trace_id: Any = None,
) -> ToolCall:
    args = args if args is not None else {"path": "ok.txt"}
    return ToolCall(
        call_id=new_call_id(),
        tool=tool,
        args=args,
        trace_id=trace_id or new_trace_id(),
        seq=0,
        ts=now_timestamp(),
        parent_msg_id=new_call_id(),
        args_hash=canonical_args_hash(args),
    )


@pytest.fixture
def base_policy() -> Policy:
    return Policy(
        allowed_tools=["fetch", "compute"],
        tool_rules=[ToolRule(name="fetch", max_calls=2)],
        egress_rules=default_egress_rules(),
        max_calls_per_trace=3,
    )


def test_allow_default(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    d = gate.on_tool_call(_make_call(tool="fetch"))
    assert isinstance(d, HookDecision)
    assert d.allow is True


# ---- deny matrix ----------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "call_factory", "expect_substring"),
    [
        ("deny-disallowed-tool", lambda p, trace_id=None: _make_call(tool="shell", trace_id=trace_id), "not in allowed_tools"),
        ("deny-per-tool-cap", lambda p, trace_id=None: _make_call(tool="fetch", args={"path": "a"}, trace_id=trace_id), "max_calls"),
    ],
)
def test_deny_paths(
    label: str,
    call_factory: Any,
    expect_substring: str,
    base_policy: Policy,
) -> None:
    gate = PolicyGate(base_policy)
    shared_trace_id = new_trace_id()
    if label == "deny-per-tool-cap":
        # Burn through the per-tool cap first (same trace id so counters share).
        for _ in range(2):
            gate.on_tool_call(call_factory(base_policy, trace_id=shared_trace_id))
    d = gate.on_tool_call(call_factory(base_policy, trace_id=shared_trace_id))
    assert d.allow is False
    assert expect_substring in d.reason


def test_deny_global_max_calls(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    shared = new_trace_id()
    last: HookDecision | None = None
    for i in range(4):
        last = gate.on_tool_call(_make_call(tool="compute", args={"x": i}, trace_id=shared))
    assert last is not None
    assert last.allow is False
    assert "max_calls_per_trace" in last.reason


def test_empty_allowed_tools_denies_all() -> None:
    policy = Policy(allowed_tools=[], max_calls_per_trace=10)
    gate = PolicyGate(policy)
    d = gate.on_tool_call(_make_call(tool="fetch"))
    assert d.allow is False
    assert "default deny all" in d.reason


# ---- egress --------------------------------------------------------------


def test_deny_ssn_in_args(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    d = gate.on_tool_call(_make_call(tool="fetch", args={"data": "ssn=123-45-6789"}))
    assert d.allow is False
    assert "ssn" in d.reason


def test_deny_aws_key_in_args(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    d = gate.on_tool_call(_make_call(tool="fetch", args={"data": "key=AKIAIOSFODNN7EXAMPLE"}))
    assert d.allow is False
    assert "aws_access_key" in d.reason


def test_deny_jwt_in_args(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    d = gate.on_tool_call(_make_call(tool="fetch", args={"token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dozjgNryP4J3jVmNHlSyw"}))
    assert d.allow is False
    assert "jwt" in d.reason


def test_deny_pan_in_args(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    d = gate.on_tool_call(_make_call(tool="fetch", args={"card": "4111111111111111"}))
    assert d.allow is False
    assert "pan" in d.reason


def test_pan_luhn_invalidation_skips_hit(base_policy: Policy) -> None:
    """A non-Luhn 16-digit run is *not* a card — gate does not flag it."""
    gate = PolicyGate(base_policy)
    # `4111111111111112` fails Luhn; the regex would match, but Luhn gate denies the hit.
    d = gate.on_tool_call(_make_call(tool="fetch", args={"ref": "4111111111111112"}))
    assert d.allow is True


# ---- rewrite --------------------------------------------------------------


def test_rewrite_severity_marks_payload(base_policy: Policy) -> None:
    policy = base_policy.model_copy(
        update={
            "egress_rules": [
                rule.model_copy(update={"severity": "rewrite"})
                for rule in base_policy.egress_rules
            ]
        }
    )
    gate = PolicyGate(policy)
    d = gate.on_tool_call(_make_call(tool="fetch", args={"data": "ssn=123-45-6789"}))
    assert d.allow is True
    assert d.rewrite_args is not None
    assert "_redacted_by" in d.rewrite_args


# ---- json schema ---------------------------------------------------------


def test_json_schema_enforced() -> None:
    """Lazy jsonschema validation: ToolRule.json_schema must be applied."""
    policy = Policy(
        allowed_tools=["compute"],
        tool_rules=[
            ToolRule(
                name="compute",
                # `required` drives deterministic deny regardless of int/float quirks.
                json_schema='{"type":"object","required":["mandatory"],"properties":{"mandatory":{"type":"string"}}}',
            )
        ],
        max_calls_per_trace=5,
    )
    gate = PolicyGate(policy)
    shared_trace_id = new_trace_id()
    # Missing mandatory -> deny.
    d_bad = gate.on_tool_call(_make_call(tool="compute", args={"x": 5}, trace_id=shared_trace_id))
    # Provided mandatory -> allow.
    d_ok = gate.on_tool_call(_make_call(tool="compute", args={"mandatory": "ok"}, trace_id=shared_trace_id))
    assert d_bad.allow is False
    assert "schema mismatch" in d_bad.reason
    assert d_ok.allow is True


def test_audit_trail_records_decisions(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    gate.on_tool_call(_make_call(tool="fetch"))
    gate.on_tool_call(_make_call(tool="shell"))
    assert any(a["decision"] == "allow" for a in gate.audit)
    assert any(a["decision"] == "deny" for a in gate.audit)


def test_call_counts_visible(base_policy: Policy) -> None:
    gate = PolicyGate(base_policy)
    trace_id = new_trace_id()
    gate.on_tool_call(_make_call(tool="fetch", trace_id=trace_id))
    gate.on_tool_call(_make_call(tool="fetch", trace_id=trace_id))
    counts = gate.call_counts
    assert sum(1 for k in counts if k[0] == str(trace_id)) == 1
    assert counts[(str(trace_id), "fetch")] == 2
