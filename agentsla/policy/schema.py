"""Policy schema (POLICY-01 / POLICY-02 / POLICY-04).

The schema is intentionally flat: ``allowed_tools`` is a set-like list
of tool names; per-tool rules live on :class:`ToolRule`; egress
detectors on :class:`EgressRule`. There is no recursive nesting — that
buys simpler validation + faster YAML round-trips, and dodges the
Pydantic v2 ``model_json_schema`` fidelity gaps noted in Q4.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentsla.policy.egress import EgressRule, TypeIdStr


class ToolRule(BaseModel):
    """Per-tool enforcement rules.

    All fields are optional — a tool with no rule still enforces
    ``allowed_tools`` membership but skips JSON-schema + egress checks.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: TypeIdStr
    json_schema: TypeIdStr | None = Field(
        default=None,
        description="Optional JSON Schema string the tool args must satisfy. "
        "Validated lazily via ``jsonschema`` if installed; bare-dict pass-through otherwise.",
    )
    max_calls: int | None = Field(
        default=None,
        ge=1,
        description="Maximum allowed calls to this tool in a single trace.",
    )


class Policy(BaseModel):
    """Top-level policy document.

    Loaded from YAML via :func:`agentsla.policy.loader.load_policy`.
    Frozen once loaded — no mid-run mutation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_tools: list[TypeIdStr] = Field(
        default_factory=list,
        description="Tools the agent may call. Empty list = deny everything.",
    )
    tool_rules: list[ToolRule] = Field(
        default_factory=list,
        description="Per-tool rules (json_schema, max_calls).",
    )
    egress_rules: list[EgressRule] = Field(
        default_factory=list,
        description="Detector pack. Empty list = no egress scanning.",
    )
    max_calls_per_trace: int = Field(
        default=20,
        ge=1,
        description="Global upper-bound on tool calls per trace (POLICY-04).",
    )
    mode: Literal["enforce", "shadow"] = Field(
        default="enforce",
        description="enforce = DENY short-circuits the loop; shadow = log only.",
    )


__all__ = ["TypeIdStr", "Policy", "ToolRule"]
