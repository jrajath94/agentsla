"""Cross-adapter parity: identical task under rawloop + langgraph with same policy."""

from __future__ import annotations

from pathlib import Path

from agentsla.adapters.langgraph import LangGraphAdapter
from agentsla.adapters.rawloop import RawLoopAdapter
from agentsla.policy.gate import PolicyGate
from agentsla.policy.schema import Policy
from agentsla.tools.deterministic import JsonEchoTool


def _gate() -> PolicyGate:
    policy = Policy(allowed_tools=["json_echo"], max_calls_per_trace=5)
    return PolicyGate(policy)


def test_both_adapters_allow_same_call(tmp_path: Path) -> None:
    for AdapterCls in (RawLoopAdapter, LangGraphAdapter):
        a = AdapterCls(
            tools={"json_echo": JsonEchoTool()},
            task_text="parity-ok",
        )
        out = a.run("parity", hooks=_gate())
        kinds = [ev.kind for ev in out.trace.events]
        assert kinds == ["model_message", "tool_call", "tool_result", "model_message"]


def test_both_adapters_deny_same_disallowed_tool(tmp_path: Path) -> None:
    for AdapterCls in (RawLoopAdapter, LangGraphAdapter):
        a = AdapterCls(
            tools={"shell": lambda: "boom"},  # 'shell' is not allowed
            task_text="parity-deny",
        )
        out = a.run("parity", hooks=_gate())
        kinds = [ev.kind for ev in out.trace.events]
        # Loop short-circuits after the denied ToolCall.
        assert kinds == ["model_message", "tool_call"]
        assert out.text == ""


def test_gate_audit_trail_consistent() -> None:
    """Same gate instance, two adapters → audit reflects both runs identically."""
    gate = PolicyGate(Policy(allowed_tools=["json_echo"], max_calls_per_trace=5))
    RawLoopAdapter(tools={"json_echo": JsonEchoTool()}, task_text="audit").run("a", hooks=gate)
    LangGraphAdapter(tools={"json_echo": JsonEchoTool()}, task_text="audit").run("b", hooks=gate)
    decisions = [a["decision"] for a in gate.audit]
    # Each run is exactly one allowed ToolCall.
    assert decisions.count("allow") == 2
