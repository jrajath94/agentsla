"""AgentSLA — SLO-aware reliability runtime for tool-calling LLM agents.

Tier-1 portfolio project for the Anthropic Staff+ candidacy (July 2026 cycle).
Public surface is intentionally small in Phase 1; later phases add the policy
gate, verification gate, classifier, and bench CLI.

Version is bumped in pyproject.toml; this module re-exports it for convenience.
"""

from __future__ import annotations

__version__ = "0.2.0.dev0"

__all__ = ["__version__"]
