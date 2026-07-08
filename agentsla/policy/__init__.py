from agentsla.policy.egress import TypeIdStr  # noqa: F401, used downstream
"""Policy package: pre-execution enforcement for tool-calling agents.

Two contracts:

  * :class:`Policy` — declarative YAML schema. Loaded at process start,
    validated by Pydantic, immutable for the run's lifetime.
  * :class:`PolicyGate` — concrete :class:`RuntimeHooks` that consults
    the policy and returns a richer :class:`HookDecision` (Phase 2
    extends ``allow`` / ``rewrite_args`` with new fields like
    ``args_hash``).

This package is intentionally side-effect-free on import. The egress
regex pack is the documented set; users can extend via
``Policy(egress_rules=[...])`` without subclassing.
"""

from agentsla.policy.egress import EgressRule, TypeIdStr, default_egress_rules
from agentsla.policy.gate import PolicyGate
from agentsla.policy.loader import load_policy
from agentsla.policy.schema import Policy, ToolRule, TypeIdStr

__all__ = [
    "EgressRule",
    "Policy",
    "PolicyGate",
    "ToolRule",
    "TypeIdStr",
    "default_egress_rules",
    "load_policy",
]
