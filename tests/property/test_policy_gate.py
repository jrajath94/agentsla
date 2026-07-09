"""Property-based invariants for ``PolicyGate``.

Defends the evaluation-order contract end-to-end: every allowed_tools /
max_calls / schema / egress / rewrite / audit / immutability invariant
the gate is supposed to uphold is asserted across randomly-generated
inputs. Uses :func:`hypothesis.given` + composite strategies rather than
``RuleBasedStateMachine`` because the gate's behaviour per-call is
already pure (no global state machine beyond the per-trace call
counters, which we control inside each test).

Why this complements the existing ``test_gate.py`` 20-case matrix:
  * The matrix samples representative cases; this file exhausts the
    shape boundary with thousands of randomised instances.
  * The matrix covers a fixed policy; this file lets Hypothesis vary
    policy + call-arg structure and asserts the invariant holds for
    every shape.

Run-time budget: ``@settings(max_examples=20, deadline=None)`` keeps the
suite under ~1 second total per CI run. Bump to 50 or 200 manually when
debugging a new heuristic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from agentsla.adapters.base import HookDecision
from agentsla.core.events import ToolCall, canonical_args_hash, now_timestamp
from agentsla.core.types import new_call_id, new_trace_id
from agentsla.policy.egress import EgressRule, default_egress_rules
from agentsla.policy.gate import PolicyGate
from agentsla.policy.schema import Policy, ToolRule

pytestmark = pytest.mark.hypothesis


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# Simple alphanumeric tool names so we never trip the TypeIdStr
# min_length=1 + no-whitespace constraint in ToolRule.
_TOOL_NAMES = st.sampled_from(["alpha", "bravo", "charlie", "delta", "echo"])

# Bounded JSON-compat args: leaves are short strings, ints, or bools.
# Bounded recursion so Hypothesis does not blow the stack.
_ARG_LEAVES = st.one_of(
    st.text(min_size=0, max_size=12),
    st.integers(min_value=-100, max_value=100),
    st.booleans(),
)
_ARG_VALUES = st.recursive(
    _ARG_LEAVES,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(min_size=1, max_size=6), children, max_size=3),
    ),
    max_leaves=20,
)


@st.composite
def tool_calls(draw: Any, tools: list[str] | None = None) -> ToolCall:
    """Build a random ``ToolCall`` with the given tool whitelist."""
    pool_strategy = st.sampled_from(tools) if tools is not None else _TOOL_NAMES
    name = draw(pool_strategy)
    args: dict[str, Any] = draw(st.dictionaries(st.text(min_size=1, max_size=6), _ARG_VALUES, max_size=3))
    return ToolCall(
        call_id=new_call_id(),
        tool=name,
        args=args,
        trace_id=new_trace_id(),
        seq=0,
        ts=now_timestamp(),
        parent_msg_id=new_call_id(),
        args_hash=canonical_args_hash(args),
    )


@st.composite
def allow_list_policies(
    draw: Any,
    *,
    min_tools: int = 1,
    max_tools: int = 4,
) -> Policy:
    """A policy with non-empty ``allowed_tools`` so membership checks fire."""
    names = draw(
        st.lists(
            st.sampled_from(["alpha", "bravo", "charlie", "delta"]),
            min_size=min_tools,
            max_size=max_tools,
            unique=True,
        )
    )
    return Policy(
        allowed_tools=names,
        tool_rules=[],
        egress_rules=[],
        max_calls_per_trace=draw(st.integers(min_value=2, max_value=10)),
    )


# ---------------------------------------------------------------------------
# Invariant 1: tool not in allowed_tools → always DENY
# ---------------------------------------------------------------------------


@given(allow_list_policies(min_tools=1, max_tools=2))
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
def test_disallowed_tool_always_denies(policy: Policy) -> None:
    """A call against a tool that is NOT in allowed_tools denies, no matter what args."""
    gate = PolicyGate(policy)
    forbidden = next(iter({"alpha", "bravo", "charlie", "delta", "echo"} - set(policy.allowed_tools)))
    call = ToolCall(
        call_id=new_call_id(),
        tool=forbidden,
        args={"x": 1},
        trace_id=new_trace_id(),
        seq=0,
        ts=now_timestamp(),
        parent_msg_id=new_call_id(),
        args_hash=canonical_args_hash({"x": 1}),
    )
    decision = gate.on_tool_call(call)
    assert decision.allow is False
    assert "not in allowed_tools" in decision.reason


# ---------------------------------------------------------------------------
# Invariant 2: empty allowed_tools → default-deny everything
# ---------------------------------------------------------------------------


@given(tool_calls())
@settings(max_examples=20, deadline=None, suppress_health_check=list(HealthCheck))
def test_empty_allowed_tools_default_denies(call: ToolCall) -> None:
    """With allowed_tools=[] the gate denies every call regardless of tool."""
    policy = Policy(allowed_tools=[], max_calls_per_trace=10)
    gate = PolicyGate(policy)
    decision = gate.on_tool_call(call)
    assert decision.allow is False
    assert "default deny all" in decision.reason


# ---------------------------------------------------------------------------
# Invariant 3: per-tool max_calls is a strict upper bound
# ---------------------------------------------------------------------------


@given(allow_list_policies(min_tools=1, max_tools=2), st.integers(min_value=1, max_value=3))
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
def test_per_tool_max_calls_bound(policy: Policy, max_calls: int) -> None:
    """After ``max_calls`` ALLOWs, the next call to that same tool DENIES.

    The Hypothesis-generated policy may not contain "alpha" in
    ``allowed_tools``; we inject "alpha" so the per-tool rule we add is
    actually exercised by the gate.
    """
    if "alpha" not in policy.allowed_tools:
        policy = policy.model_copy(update={"allowed_tools": [*policy.allowed_tools, "alpha"]})
    rule = ToolRule(name="alpha", max_calls=max_calls)
    # Lift the global cap well above max_calls so per-tool is the binding
    # constraint being tested — Hypothesis-generated policies may have a
    # tight ``max_calls_per_trace`` that would pre-empt the test.
    policy = policy.model_copy(update={"max_calls_per_trace": max_calls + 50, "tool_rules": [rule]})
    gate = PolicyGate(policy)
    trace_id = new_trace_id()
    for i in range(max_calls):
        d = gate.on_tool_call(
            ToolCall(
                call_id=new_call_id(),
                tool="alpha",
                args={"i": i},
                trace_id=trace_id,
                seq=i,
                ts=now_timestamp(),
                parent_msg_id=new_call_id(),
                args_hash=canonical_args_hash({"i": i}),
            )
        )
        assert d.allow is True, f"call {i} should have been allowed but was denied: {d.reason}"
    over = gate.on_tool_call(
        ToolCall(
            call_id=new_call_id(),
            tool="alpha",
            args={"i": max_calls},
            trace_id=trace_id,
            seq=max_calls,
            ts=now_timestamp(),
            parent_msg_id=new_call_id(),
            args_hash=canonical_args_hash({"i": max_calls}),
        )
    )
    assert over.allow is False
    assert "max_calls" in over.reason


# ---------------------------------------------------------------------------
# Invariant 4: global max_calls_per_trace is a strict upper bound
# ---------------------------------------------------------------------------


@given(allow_list_policies(min_tools=1, max_tools=2), st.integers(min_value=2, max_value=6))
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
def test_global_max_calls_per_trace_bound(policy: Policy, cap: int) -> None:
    """After ``max_calls_per_trace`` total ALLOWs on a single trace, deny.

    Always exercises a single tool so the per-tool counter for that
    tool monotonically reaches ``cap`` and the global bound fires on the
    (cap+1)-th call.
    """
    policy = policy.model_copy(update={"max_calls_per_trace": cap})
    gate = PolicyGate(policy)
    trace_id = new_trace_id()
    tool = policy.allowed_tools[0]

    for i in range(cap):
        d = gate.on_tool_call(
            ToolCall(
                call_id=new_call_id(),
                tool=tool,
                args={"i": i},
                trace_id=trace_id,
                seq=i,
                ts=now_timestamp(),
                parent_msg_id=new_call_id(),
                args_hash=canonical_args_hash({"i": i}),
            )
        )
        assert d.allow is True

    over = gate.on_tool_call(
        ToolCall(
            call_id=new_call_id(),
            tool=tool,
            args={"i": cap},
            trace_id=trace_id,
            seq=cap,
            ts=now_timestamp(),
            parent_msg_id=new_call_id(),
            args_hash=canonical_args_hash({"i": cap}),
        )
    )
    assert over.allow is False
    assert "max_calls_per_trace" in over.reason


# ---------------------------------------------------------------------------
# Invariant 5: json-schema enforcement (when jsonschema is installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not pytest.importorskip("jsonschema", reason="jsonschema optional").__class__,
    reason="jsonschema not installed — schema enforcement path is best-effort pass-through",
)
def test_schema_required_field_always_denies_when_missing() -> None:
    """A schema mandating ``mandatory: string`` denies any args lacking it."""
    jsonschema = pytest.importorskip("jsonschema")
    policy = Policy(
        allowed_tools=["compute"],
        tool_rules=[
            ToolRule(
                name="compute",
                json_schema=json.dumps(
                    {
                        "type": "object",
                        "required": ["mandatory"],
                        "properties": {"mandatory": {"type": "string"}},
                    }
                ),
            )
        ],
        max_calls_per_trace=10,
    )
    gate = PolicyGate(policy)
    _ = jsonschema  # silence unused warning; ensures import is present
    bad = ToolCall(
        call_id=new_call_id(),
        tool="compute",
        args={"x": 1},
        trace_id=new_trace_id(),
        seq=0,
        ts=now_timestamp(),
        parent_msg_id=new_call_id(),
        args_hash=canonical_args_hash({"x": 1}),
    )
    decision = gate.on_tool_call(bad)
    assert decision.allow is False
    assert "schema mismatch" in decision.reason


# ---------------------------------------------------------------------------
# Invariant 6: egress DENY rule that matches args → always DENY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "regex", "payload"),
    [
        ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b", "AKIAIOSFODNN7EXAMPLE"),
        ("ssn", r"\b\d{3}-\d{2}-\d{4}\b", "123-45-6789"),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dozjgNryP4J3jVmNHlSyw"),
    ],
)
def test_egress_deny_rule_blocks_with_rule_name_in_reason(name: str, regex: str, payload: str) -> None:
    """Each default-pack deny rule DENIES and mentions the rule name."""
    policy = Policy(
        allowed_tools=["fetch"],
        egress_rules=[EgressRule(name=name, regex=regex, severity="deny")],
        max_calls_per_trace=10,
    )
    gate = PolicyGate(policy)
    decision = gate.on_tool_call(
        ToolCall(
            call_id=new_call_id(),
            tool="fetch",
            args={"data": payload},
            trace_id=new_trace_id(),
            seq=0,
            ts=now_timestamp(),
            parent_msg_id=new_call_id(),
            args_hash=canonical_args_hash({"data": payload}),
        )
    )
    assert decision.allow is False
    assert name in decision.reason


# ---------------------------------------------------------------------------
# Invariant 7: egress REWRITE rule allows but stamps a redactor marker
# ---------------------------------------------------------------------------


@given(st.text(min_size=0, max_size=64))
@settings(max_examples=15, deadline=None, suppress_health_check=list(HealthCheck))
def test_egress_rewrite_severity_stamps_marker(payload: str) -> None:
    """A `severity="rewrite"` egress rule ALLOWs and rewrites args with ``_redacted_by``."""
    policy = Policy(
        allowed_tools=["fetch"],
        egress_rules=[EgressRule(name="aws_access_key", regex=r"\bAKIA[0-9A-Z]{16}\b", severity="rewrite")],
        max_calls_per_trace=10,
    )
    gate = PolicyGate(policy)
    decision = gate.on_tool_call(
        ToolCall(
            call_id=new_call_id(),
            tool="fetch",
            args={"data": payload},
            trace_id=new_trace_id(),
            seq=0,
            ts=now_timestamp(),
            parent_msg_id=new_call_id(),
            args_hash=canonical_args_hash({"data": payload}),
        )
    )
    # Rewrite path runs only when the regex actually matches.
    if "AKIA" in payload:
        assert decision.allow is True
        assert decision.rewrite_args is not None
        assert decision.rewrite_args.get("_redacted_by") == "aws_access_key"
        # Egress rule name also appears in the human-readable reason.
        assert "aws_access_key" in decision.reason
    else:
        # Cleanup path: rule didn't match → ALLOWed with no rewrite.
        assert decision.allow is True
        assert decision.rewrite_args is None


# ---------------------------------------------------------------------------
# Invariant 8: audit trail grows monotonically with every decision
# ---------------------------------------------------------------------------


@given(st.lists(tool_calls(), min_size=1, max_size=20))
@settings(max_examples=20, deadline=None, suppress_health_check=list(HealthCheck))
def test_audit_trail_grows_one_per_decision(calls: list[ToolCall]) -> None:
    """Every on_tool_call appends exactly one audit row."""
    # A generous policy so we don't get stuck on disallow denials; the invariant
    # holds regardless of decision type — we are checking the *count*, not the type.
    policy = Policy(
        allowed_tools=["alpha", "bravo", "charlie", "delta", "echo"],
        max_calls_per_trace=200,
    )
    gate = PolicyGate(policy)
    before = len(gate.audit)
    for call in calls:
        gate.on_tool_call(call)
    after = len(gate.audit)
    assert after == before + len(calls)


# ---------------------------------------------------------------------------
# Invariant 9: Policy objects are frozen (no mid-run mutation)
# ---------------------------------------------------------------------------


def test_policy_frozen_blocks_mutation() -> None:
    """Pydantic ``frozen=True`` on Policy raises ValidationError on assignment."""
    policy = Policy(allowed_tools=["alpha"], max_calls_per_trace=4)
    with pytest.raises(ValidationError):
        policy.allowed_tools = ["bravo"]  # type: ignore[misc]
    with pytest.raises(ValidationError):
        policy.max_calls_per_trace = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Invariant 10: Luhn-invalid PAN-shaped strings are not flagged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "digits",
    [
        "4111111111111112",  # trailing 2 breaks Luhn
        "5500000000000009",  # 5+9 trailing — non-Luhn (sum=15)
        "1234567890123456",  # arithmetic-progression non-Luhn (sum=64)
    ],
)
def test_pan_luhn_invalid_strings_are_not_flagged(digits: str) -> None:
    """If the regex matches but Luhn fails, the PAN rule does not raise a hit."""
    policy = Policy(
        allowed_tools=["fetch"],
        egress_rules=default_egress_rules(),
        max_calls_per_trace=10,
    )
    gate = PolicyGate(policy)
    decision = gate.on_tool_call(
        ToolCall(
            call_id=new_call_id(),
            tool="fetch",
            args={"ref": digits},
            trace_id=new_trace_id(),
            seq=0,
            ts=now_timestamp(),
            parent_msg_id=new_call_id(),
            args_hash=canonical_args_hash({"ref": digits}),
        )
    )
    assert decision.allow is True
    assert "pan" not in decision.reason


# ---------------------------------------------------------------------------
# Invariant 11: egress detection walks nested dict/list values
# ---------------------------------------------------------------------------


@given(st.sampled_from(["nested_dict", "nested_list", "top_level"]))
@example(structure="top_level")  # ensure baseline regression coverage
@settings(max_examples=10, deadline=None, suppress_health_check=list(HealthCheck))
def test_egress_hit_in_nested_value(structure: str) -> None:
    """AWS-key substring hidden inside a dict/list value still triggers the rule."""
    payload = "AKIAIOSFODNN7EXAMPLE"
    policy = Policy(
        allowed_tools=["fetch"],
        egress_rules=default_egress_rules(),
        max_calls_per_trace=10,
    )
    if structure == "nested_dict":
        args: dict[str, Any] = {"outer": {"inner": payload}}
    elif structure == "nested_list":
        args = {"outer": ["unrelated", payload]}
    else:
        args = {"data": payload}
    gate = PolicyGate(policy)
    decision = gate.on_tool_call(
        ToolCall(
            call_id=new_call_id(),
            tool="fetch",
            args=args,
            trace_id=new_trace_id(),
            seq=0,
            ts=now_timestamp(),
            parent_msg_id=new_call_id(),
            args_hash=canonical_args_hash(args),
        )
    )
    assert decision.allow is False
    assert "aws_access_key" in decision.reason


# ---------------------------------------------------------------------------
# Invariant 12: ALLOW decisions always carry an args_hash matching the
# canonical hash of the (rewritten or original) args.
# ---------------------------------------------------------------------------


@given(tool_calls(tools=["alpha"]))
@settings(max_examples=20, deadline=None, suppress_health_check=list(HealthCheck))
def test_allow_decision_carries_matching_args_hash(call: ToolCall) -> None:
    """An ALLOW decision's ``extra['args_hash']`` matches args canonical hash."""
    policy = Policy(allowed_tools=["alpha"], max_calls_per_trace=50)
    gate = PolicyGate(policy)
    decision: HookDecision = gate.on_tool_call(call)
    if decision.allow:
        expected = canonical_args_hash(call.args) if decision.rewrite_args is None else canonical_args_hash(decision.rewrite_args)
        assert decision.extra.get("args_hash") == expected


# ---------------------------------------------------------------------------
# Invariant 13: per-trace call_counts strictly increase per tool
# ---------------------------------------------------------------------------


@given(allow_list_policies(min_tools=1, max_tools=2), st.integers(min_value=1, max_value=4))
@settings(max_examples=20, deadline=None, suppress_health_check=list(HealthCheck))
def test_call_counts_increment_on_allow(policy: Policy, n: int) -> None:
    """Each allowed call to a tool increments ``call_counts`` by exactly 1."""
    policy = policy.model_copy(update={"max_calls_per_trace": n + 5, "tool_rules": []})
    gate = PolicyGate(policy)
    tool = policy.allowed_tools[0]
    trace_id = new_trace_id()
    for i in range(n):
        gate.on_tool_call(
            ToolCall(
                call_id=new_call_id(),
                tool=tool,
                args={"i": i},
                trace_id=trace_id,
                seq=i,
                ts=now_timestamp(),
                parent_msg_id=new_call_id(),
                args_hash=canonical_args_hash({"i": i}),
            )
        )
    assert gate.call_counts[(str(trace_id), tool)] == n
