"""Policy YAML loader."""

from __future__ import annotations

from pathlib import Path

import yaml

from agentsla.policy.egress import default_egress_rules
from agentsla.policy.schema import Policy


def load_policy(path: str | Path) -> Policy:
    """Read + parse + validate ``path`` as a :class:`Policy`.

    Uses ``yaml.safe_load`` (no arbitrary object instantiation), then
    hands the resulting dict to Pydantic for validation. Missing files
    raise ``FileNotFoundError``; invalid shapes raise
    ``pydantic.ValidationError``.

    If the YAML omits ``egress_rules``, the default pack is inserted —
    operators get SSN/PAN/AWS-key/JWT detection out of the box. Set
    ``egress_rules: []`` explicitly to disable.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"policy file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"policy file {p} must contain a mapping; got {type(raw).__name__}")
    # Default the egress pack when unspecified.
    raw.setdefault("egress_rules", [r.model_dump() for r in default_egress_rules()])
    return Policy.model_validate(raw)


__all__ = ["load_policy"]
