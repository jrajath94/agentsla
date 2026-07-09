"""Schema upgrade helpers (SCHEMA-VERSION-01).

Each ``vN_to_vM(trace)`` function takes a :class:`Trace` written at
schema version ``N`` and returns an equivalent :class:`Trace` at
version ``M``. The functions are pure (no I/O) so the upgrade is
testable end-to-end without a database.

The shipped v0.2.0 only knows v1 (the as-shipped schema). Future
versions add ``v1_to_v2`` here with a worked example in
``docs/schema-migrations.md``.

CLI wiring lives at :mod:`agentsla.cli`; this module is importable
on its own for use in notebooks and ETL pipelines.
"""

from __future__ import annotations

from agentsla.core.events import Trace
from agentsla.core.schema_version import SCHEMA_VERSION, SchemaVersionError


def upgrade_in_place(trace: Trace) -> Trace:
    """Upgrade ``trace`` to the current :data:`SCHEMA_VERSION`.

    v1 is the current shipping version, so this is a no-op identity
    pass-through. The function exists so callers can write
    version-agnostic upgrade code today and slot real converters in
    when v2 lands.
    """
    from agentsla.core.schema_version import detect_version

    detected = detect_version(trace)
    if detected == SCHEMA_VERSION:
        return trace
    if detected < SCHEMA_VERSION:
        # v0.2 ships only v1, so a <1 trace is a future-schemer; we
        # do not know how to roll forward.
        msg = f"trace schema_version={detected} is older than supported {SCHEMA_VERSION}; no downgrade path"
        raise SchemaVersionError(msg)
    msg = f"trace schema_version={detected} is newer than runtime {SCHEMA_VERSION}; upgrade the runtime"
    raise SchemaVersionError(msg)


__all__ = ["Trace", "upgrade_in_place"]
