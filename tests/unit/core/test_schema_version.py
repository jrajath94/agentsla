"""Schema version constant + detect_version + upgrade_in_place."""

from __future__ import annotations

import pytest

from agentsla.bench.upgrader import upgrade_in_place
from agentsla.core.events import Trace
from agentsla.core.schema_version import SCHEMA_VERSION, SchemaVersionError, detect_version
from agentsla.core.types import new_trace_id


def _empty_trace() -> Trace:
    return Trace(
        trace_id=new_trace_id(),
        task_id="t",
        model_id="echo-1",
        events=[],
        final_answer="",
    )


def test_schema_version_is_one() -> None:
    """The shipped runtime writes only schema version 1."""
    assert SCHEMA_VERSION == 1


def test_detect_version_defaults_to_one() -> None:
    """Traces without an explicit schema_version field are assumed v1."""
    assert detect_version(_empty_trace()) == 1


def test_upgrade_in_place_is_noop_for_v1() -> None:
    """A v1 trace round-trips through upgrade_in_place unchanged."""
    trace = _empty_trace()
    upgraded = upgrade_in_place(trace)
    assert upgraded is trace  # identity — pure no-op for current version


def test_upgrade_in_place_raises_on_unknown_version() -> None:
    """A trace with an explicit newer schema_version raises SchemaVersionError."""
    # A trace faking schema_version=99 lands us in the "newer than runtime"
    # branch — the runtime cannot read traces from the future.
    # pydantic v2 model_extra is forbidden for the strict Trace model, so we
    # bypass via SimpleNamespace-style monkey-patch on a copy.
    trace = _empty_trace()
    object.__setattr__(trace, "schema_version", 99)
    with pytest.raises(SchemaVersionError, match="newer than runtime"):
        upgrade_in_place(trace)


def test_upgrade_in_place_raises_on_older_version() -> None:
    """A trace with schema_version=0 (placeholder older than v1) raises."""
    trace = _empty_trace()
    object.__setattr__(trace, "schema_version", 0)
    with pytest.raises(SchemaVersionError, match="older than supported"):
        upgrade_in_place(trace)
