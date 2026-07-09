"""Trace schema versioning (SCHEMA-VERSION-01).

The AgentSLA trace store is append-only. Once a ``Verdict`` event has
been written to a DuckDB file, the on-disk shape of that event is
frozen — there is no in-place migration. To make future schema
bump-and-replay flows safe, every event carries a ``schema_version``
field on its parent :class:`agentsla.core.events.Trace`, and the
runtime refuses to mix events across versions in the same DuckDB
file.

Why a constant + exception + helper instead of just a doc:

  * ``SCHEMA_VERSION`` is a single import for upgrade scripts to
    pin against. Downstream code reads ``SCHEMA_VERSION`` and never
    hard-codes ``1`` or ``2`` — that way ``grep SCHEMA_VERSION``
    enumerates every consumer.
  * :class:`SchemaVersionError` is the documented failure mode for
    mixed-version databases, replay engines, and Parquet exports.
  * :func:`detect_version` reads a trace and returns its version so
    upgrade helpers do not need to know the column layout.

Schema bump policy (see ``docs/schema-migrations.md`` for the full
workbook):

  1. Add a new ``SCHEMA_VERSION`` literal here.
  2. Add a converter ``vN_to_vM(trace) -> Trace`` under
     ``bench/upgrader.py``. The converter MUST be deterministic and
     re-emittable (round-trip-stable).
  3. Add a worked example to ``docs/schema-migrations.md``.
  4. Tag the previous version's Git tag as ``schema-vN``.

This module intentionally depends on nothing but stdlib + Pydantic,
so a future offline migration tool can import it without pulling in
the full runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from agentsla.core.events import Trace

#: Current trace schema version. Bumped in lock-step with a
#: v(N)->v(N+1) converter in ``bench/upgrader.py``. The shipped
#: runtime writes only this version; older versions are readable
#: via the upgrade path.
SCHEMA_VERSION: Final = 1


class SchemaVersionError(RuntimeError):
    """Raised when a trace's schema_version cannot be reconciled.

    Two failure modes land here:

      * A database file contains events from two different schema
        versions (corrupt / hand-rolled / partially-upgraded state).
      * A reader's pin (``SCHEMA_VERSION``) does not match the
        on-disk version AND no upgrade path is registered.
    """


def detect_version(trace: Trace) -> int:
    """Return the schema version a :class:`Trace` was written at.

    For v1, the version is implicit (the schema was the only one
    shipped). The detection reads a denormalized ``schema_version``
    field on :class:`Trace` if present, else assumes v1 for
    back-compat with v0.1.x traces.
    """
    raw = getattr(trace, "schema_version", None)
    if raw is None:
        return 1
    return int(raw)


__all__ = ["SCHEMA_VERSION", "SchemaVersionError", "detect_version"]
