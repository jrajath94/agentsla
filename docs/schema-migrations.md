# Trace schema migrations

This document is the worked-example notebook for the trace schema
versioning story. The constants + exception + helpers live in
`agentsla/core/schema_version.py`; the upgrade stubs live in
`agentsla/bench/upgrader.py`. This file explains the *why* and the
*protocol*; refer to those modules for the machine-checkable
contract.

## Why a schema version

The AgentSLA trace store is append-only. A `Verdict` event written
to a DuckDB file in 2026-July is byte-identical to one written in
2026-August unless we explicitly bump the schema. The append-only
invariant is a *feature* — it makes deterministic replay possible —
but it creates a versioning problem: when a new field lands in
`core/events.py`, every existing DuckDB file becomes silently
mis-typed.

The fix is to attach a `schema_version` field to every persisted
`Trace` and to make the runtime refuse to mix versions in the same
file. This document is the playbook for bumping that version.

## Current state (v0.2.0)

- `SCHEMA_VERSION = 1` (exported from `agentsla.core.schema_version`).
- The shipped `Trace` model does not yet carry an explicit
  `schema_version` field; v1 traces are detected by the absence of
  the field and treated as v1.
- `upgrade_in_place(trace)` is a no-op for v1 and a `SchemaVersionError`
  for any other version (newer or older).

## When to bump

Bump `SCHEMA_VERSION` when **any** of the following changes ship:

1. A field is added to `Trace` that is not optional.
2. A new event `kind` discriminator is added to the `Event`
   discriminated union in `core/events.py`.
3. A field's *type* changes (e.g., `int` → `Decimal` for the
   numeric verifier).
4. An event field is removed or renamed.

Bumping is *not* required for:

- Optional field additions (the field defaults to `None`).
- Heuristic trigger additions in `classify/heuristics.py` (those
  live outside the trace store).
- Documentation changes.

## Bump protocol

When a bump is needed:

1. **Bump the constant.** Update `SCHEMA_VERSION` in
   `agentsla/core/schema_version.py`. Add a short bullet to the
   `Schema bump policy` docstring describing what changed.
2. **Add a converter.** In `agentsla/bench/upgrader.py`, write a
   pure function `v1_to_v2(trace) -> Trace` that transforms a v1
   trace into a v2 trace. The function must be deterministic and
   round-trip stable (running it twice produces identical output).
3. **Wire `upgrade_in_place`.** Extend the dispatch in
   `upgrade_in_place` so a v1 trace passes through `v1_to_v2` and
   returns a v2 trace.
4. **Add a property test.** Under `tests/property/`, write a
   `test_upgrade_v1_to_v2_*.py` that asserts the converter
   round-trips: `v1_to_v2(v1) == v1_to_v2(v1)`.
5. **Update this file.** Add a worked example below for the new
   version pair, including a real (small) input/output pair from
   the test.
6. **Tag the prior version.** `git tag schema-v1` on the commit
   that ships the v1→v2 converter. That tag is the *upgrader*'s
   reference, not the *runtime*'s — runtime reads `SCHEMA_VERSION`
   directly.

## Worked example (v1 → v2)

*Hypothetical — v2 has not shipped yet. This block exists as the
pattern future bump PRs will copy.*

Suppose v2 adds a `caller_id: str | None = None` field to
`ModelMessage` so multi-agent traces can attribute each model
message to the agent that produced it.

### Diff sketch

```python
# agentsla/core/events.py — diff against v1
class ModelMessage(_StrictModel):
    kind: Literal["model_message"] = "model_message"
    # ... existing fields ...
    caller_id: str | None = None  # NEW in v2
```

```python
# agentsla/bench/upgrader.py — diff against v1
def v1_to_v2(trace: Trace) -> Trace:
    """v1 → v2: lift optional fields, add caller_id=None default."""
    new_events = []
    for ev in trace.events:
        if isinstance(ev, ModelMessage):
            ev = ev.model_copy(update={"caller_id": getattr(ev, "caller_id", None)})
        new_events.append(ev)
    return trace.model_copy(update={"events": new_events, "schema_version": 2})


def upgrade_in_place(trace: Trace) -> Trace:
    detected = detect_version(trace)
    if detected == SCHEMA_VERSION:
        return trace
    if detected == 1 and SCHEMA_VERSION == 2:
        return v1_to_v2(trace)
    # ... existing error branches ...
```

### Test sketch

```python
# tests/property/test_upgrade_v1_to_v2.py
def test_v1_to_v2_is_idempotent():
    trace_v1 = _fixture_v1_trace()
    once = v1_to_v2(trace_v1)
    twice = v1_to_v2(once)
    assert once.model_dump() == twice.model_dump()


def test_v1_to_v2_adds_caller_id_default_none():
    trace_v1 = _fixture_v1_trace()
    upgraded = v1_to_v2(trace_v1)
    for ev in upgraded.events:
        if isinstance(ev, ModelMessage):
            assert ev.caller_id is None
```

### Migration script (operator side)

```bash
# Old database, in-place upgrade to v2:
python -m agentsla upgrade-traces \
    --db traces.duckdb \
    --in-schema 1 \
    --out-schema 2 \
    --out-db traces.v2.duckdb
```

The `upgrade-traces` CLI is out of scope for v0.2.0; the entry point
will land alongside the first real v1→v2 converter.

## Cross-references

- `agentsla/core/schema_version.py` — `SCHEMA_VERSION` constant,
  `SchemaVersionError`, `detect_version` helper.
- `agentsla/bench/upgrader.py` — `upgrade_in_place` dispatcher,
  per-version converters.
- `tests/unit/core/test_schema_version.py` — unit tests for the
  constant + helper + dispatcher.
- `docs/comparative-analysis.md` — AgentSLA's "offline / hermetic
  replay" row ties to the versioning story: replay needs the schema
  version to be pinned before the trace stream is read.
