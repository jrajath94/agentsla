"""YAML policy loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentsla.policy.loader import load_policy


def _write_yaml(tmp_path: Path, doc: object) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def test_loader_reads_valid_yaml(tmp_path: Path) -> None:
    p = _write_yaml(
        tmp_path,
        {
            "allowed_tools": ["fetch", "compute"],
            "tool_rules": [{"name": "fetch", "max_calls": 5}],
            "max_calls_per_trace": 30,
        },
    )
    policy = load_policy(p)
    assert policy.allowed_tools == ["fetch", "compute"]
    assert policy.tool_rules[0].max_calls == 5
    assert policy.max_calls_per_trace == 30


def test_loader_inserts_default_egress_pack(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, {"allowed_tools": ["x"]})
    policy = load_policy(p)
    names = sorted(r.name for r in policy.egress_rules)
    assert names == ["aws_access_key", "jwt", "pan", "ssn"]


def test_loader_respects_explicit_empty_egress(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, {"allowed_tools": ["x"], "egress_rules": []})
    policy = load_policy(p)
    assert policy.egress_rules == []


def test_loader_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_policy(tmp_path / "absent.yaml")


def test_loader_non_mapping_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")  # YAML list, not mapping
    with pytest.raises(ValueError, match="must contain a mapping"):
        load_policy(bad)


def test_load_policy_from_example() -> None:
    """Verify the committed example policy loads + validates.

    This pins the shipped ``examples/policy.yaml`` to the loader's
    schema so a future refactor that loosens it surfaces here, not
    silently in production.
    """
    repo_root = Path(__file__).resolve().parents[3]
    example = repo_root / "examples" / "policy.yaml"
    assert example.exists(), f"example policy missing at {example}"
    policy = load_policy(example)
    # Item 6 acceptance: 2+ tool_rules, concrete allowed_tools, mode present.
    assert "web_search" in policy.allowed_tools
    assert "calculator" in policy.allowed_tools
    assert len(policy.tool_rules) >= 2
    rule_names = {r.name for r in policy.tool_rules}
    assert {"web_search", "calculator"}.issubset(rule_names)
    assert policy.max_calls_per_trace == 20
    assert policy.mode == "enforce"
    # The annotated example ships its own explicit egress pack.
    assert len(policy.egress_rules) >= 5
