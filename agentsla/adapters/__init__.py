"""Adapter surface — concrete adapters that realize the RuntimeHooks contract
against a specific framework (Claude Agent SDK, LangGraph, raw tool-loop).

Submodules:
    base        — AgentAdapter ABC + RuntimeHooks Protocol (plan 01.5).
    rawloop     — reference tool-loop implementation, hermetic + deterministic.
                  Used as the test-bed for ``REPLAY-PROOF``.
    langgraph   — LangGraph-shaped stub adapter; parity-equivalent to rawloop.
    claude_sdk  — Claude Agent SDK adapter; driven by an injected SDK client
                  so tests can hermetically script SDK responses.
    noop_hooks  — default RuntimeHooks impl (always-ALLOW). Phase 2's
                  PolicyGate slots in here for real decisions.
"""

from __future__ import annotations

from agentsla.adapters.base import AgentAdapter, FinalAnswer, HookDecision, RuntimeHooks
from agentsla.adapters.claude_sdk import ClaudeSdkAdapter
from agentsla.adapters.langgraph import LangGraphAdapter
from agentsla.adapters.noop_hooks import NoOpHooks
from agentsla.adapters.rawloop import EchoModel, RawLoopAdapter

__all__ = [
    "AgentAdapter",
    "ClaudeSdkAdapter",
    "EchoModel",
    "FinalAnswer",
    "HookDecision",
    "LangGraphAdapter",
    "NoOpHooks",
    "RawLoopAdapter",
    "RuntimeHooks",
    "base",
    "claude_sdk",
    "langgraph",
    "noop_hooks",
    "rawloop",
]
