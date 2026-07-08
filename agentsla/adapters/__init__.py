"""Adapter surface — concrete adapters that realize the RuntimeHooks contract
against a specific framework (Claude Agent SDK, LangGraph, raw tool-loop).

Submodules:
    base    — AgentAdapter ABC + RuntimeHooks Protocol (plan 01.5).
    rawloop — reference tool-loop implementation, hermetic + deterministic.
              Used as the test-bed for ``REPLAY-PROOF``.
"""

from __future__ import annotations

__all__ = ["base", "rawloop"]
