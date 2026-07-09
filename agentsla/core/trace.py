"""Append-only trace store backed by DuckDB.

Two interfaces:

  * :class:`TraceWriter` — owns one DuckDB connection in read-write mode and
    exposes a single ``append(event)`` method. The connection establishes the
    ``events`` table on first use and never mutates existing rows (the log is
    append-only).

  * :class:`TraceReader` — opens an independent DuckDB connection with
    ``read_only=True`` so the writer's append-only access is unaffected. It
    supports listing trace summaries plus reconstructing the full
    :class:`~agentsla.core.events.Trace` for a given trace_id.

Storage layout:

  Single ``events`` table — one row per event in the trace log. Columns:

    trace_id   UUID
    seq        INTEGER    -- per-trace sequence number
    kind       VARCHAR    -- event discriminator ("tool_call", "tool_result", ...)
    ts         VARCHAR    -- ISO-8601 timestamp (JSON-emitted; survives TZ round-trip)
    payload    JSON       -- the entire event serialized as JSON via Pydantic

  The reader deserializes ``payload`` back into the concrete event class by
  reading ``payload[\"kind\"]`` and dispatching through the discriminated union.
  Trade-off: JSON column is slightly slower than typed columns but lets the
  schema evolve without migrations (PITFALL #12 — verifier schema drift).

Per-run rotation (PITFALL #11 — DuckDB file growth) is plumbed through
``TraceWriter(db_path, rotate_after_bytes=...)``: when the file exceeds the
threshold, the writer closes the current connection, renames the file to
``<path>.rotated-<ts>``, and opens a fresh file. Default is 50 MiB which fits
the bench harness (30 tasks x 5 seeds x 2 modes ≈ 300 short traces) comfortably.

Q3 mitigation: the reader always opens ``read_only=True``; the writer's
read-write connection serializes only when needed. Concurrent writers are
serialized by DuckDB's single-writer lock (intended behavior; out of scope
for the v0.1 single-process runtime).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import duckdb
from pydantic import BaseModel, TypeAdapter

from agentsla.core.events import (
    Event,
    ModelMessage,
    Timestamp,
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
)

# Default rotation threshold (PITFALL #11 — DuckDB file growth).
DEFAULT_ROTATE_AFTER_BYTES = 50 * 1024 * 1024  # 50 MiB

_EVENT_TYPE_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class TraceWriter:
    """Append-only writer for the AgentSLA trace log.

    Establishes the events table on first use; persists across inserts.
    Close explicitly via ``close()`` or via the context manager protocol so
    DuckDB's appender buffers are flushed before the file is released.

    Concurrency:
        Each process holds its own connection. Two writers racing on the same
        DuckDB file WILL block until one releases; this is intended. The
        replay path uses TraceReader which never writes (read_only=True),
        so a running replay cannot corrupt the writer's append-only log.
    """

    def __init__(
        self,
        db_path: Path,
        rotate_after_bytes: int = DEFAULT_ROTATE_AFTER_BYTES,
    ) -> None:
        self.db_path = Path(db_path)
        self.rotate_after_bytes = rotate_after_bytes
        self._con: duckdb.DuckDBPyConnection | None = None
        self._open()

    # ----- lifecycle -----

    def _open(self) -> None:
        """Open the writer's connection (read-write) and ensure the schema."""
        # ``duckdb.connect`` ignores path resolution when the file does not
        # exist yet and creates it on first write. Force the directory to
        # exist so SQLite-style "open or create" semantics hold.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.db_path))
        self._con.execute(_SCHEMA_DDL)
        # Keep a fast path open for non-append events: simple SELECT by id.

    def _maybe_rotate(self) -> None:
        """Rotate when the file exceeds the configured threshold.

        Renames the file to ``<path>.rotated-<unix-ts>`` and reopens.
        No-op while the file is below threshold.
        """
        if not self.db_path.exists():
            return
        size = self.db_path.stat().st_size
        if size < self.rotate_after_bytes:
            return
        # Close + rename + reopen.
        assert self._con is not None
        self._con.close()
        rotation_stamp = int(self.db_path.stat().st_mtime)
        target = self.db_path.with_name(f"{self.db_path.name}.rotated-{rotation_stamp}")
        shutil.move(str(self.db_path), str(target))
        self._open()

    def close(self) -> None:
        """Flush + close the writer's DuckDB connection."""
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover — best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

    # ----- writes -----

    def append(self, event: ToolCall | ToolResult | ModelMessage | Verdict) -> None:
        """Append one event to the log.

        Validates the event via the discriminated-union Pydantic type
        (``Event``) so a caller passing a malformed dict fails loud at the
        boundary — never silently dropped, never stored half-formed.
        """
        if self._con is None:
            raise RuntimeError("TraceWriter is closed")
        # Re-validate through the discriminated-union: this guarantees the
        # ``kind`` field is one of the four literal values, so the reader can
        # dispatch without further checks.
        validated: ToolCall | ToolResult | ModelMessage | Verdict = _EVENT_TYPE_ADAPTER.validate_python(event.model_dump(mode="json"))
        payload_json = validated.model_dump(mode="json")
        row = (
            str(validated.trace_id),
            int(validated.seq),
            validated.kind,
            validated.ts.isoformat(),
            json.dumps(payload_json),
        )
        self._con.execute(
            "INSERT INTO events (trace_id, seq, kind, ts, payload) VALUES (?, ?, ?, ?, ?)",
            row,
        )
        self._maybe_rotate()

    def next_seq(self, trace_id: UUID) -> int:
        """Return ``max(seq) + 1`` for ``trace_id`` (0 when no rows yet)."""
        if self._con is None:
            raise RuntimeError("TraceWriter is closed")
        row = self._con.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM events WHERE trace_id = ?",
            (str(trace_id),),
        ).fetchone()
        return int(row[0]) if row else 0

    # ----- exports -----

    def export_parquet(self, out_path: Path, *, mode: Literal["write", "append"] = "write") -> None:
        """Export every event row to a Parquet file.

        ``mode='append'`` lets the bench harness concat results across runs;
        ``mode='write'`` (default) overwrites.
        """
        if self._con is None:
            raise RuntimeError("TraceWriter is closed")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        append_clause = " ,APPEND" if mode == "append" else ""
        self._con.execute(f"COPY events TO '{out_path.as_posix()}' (FORMAT PARQUET{append_clause})")

    # ----- accessors used by the bench CLI -----

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Direct access to the underlying connection (read-only contract).

        Exposed for the read paths (TraceReader-equivalent) that want to
        avoid a second connection. Callers MUST NOT execute INSERT/UPDATE/
        DELETE/ALTER through this handle — the writer is the only writer.
        """
        if self._con is None:
            raise RuntimeError("TraceWriter is closed")
        return self._con


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class _TraceSummary(BaseModel):
    """Lightweight projection of one trace's metadata."""

    trace_id: UUID
    task_id: str
    model_id: str
    event_count: int
    start_ts: Timestamp
    end_ts: Timestamp | None


class TraceReader:
    """Read-only access to a trace log.

    Opens with ``duckdb.connect(path, read_only=True)`` (Q3 mitigation: the
    replay path cannot corrupt the live writer). Every public method is a
    pure projection — no INSERT/UPDATE/DELETE.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._con: duckdb.DuckDBPyConnection | None = None
        self._open()

    def _open(self) -> None:
        # ``read_only=True`` is the documented DuckDB pattern for concurrent
        # readers against a writer (ARCHITECTURE.md §DuckDB). The replay path
        # uses this handle to guarantee zero writes against the live log.
        self._con = duckdb.connect(str(self.db_path), read_only=True)

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> TraceReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover — best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

    # ----- queries -----

    def list_traces(self) -> list[_TraceSummary]:
        """Return one summary per distinct trace_id, ordered by start time."""
        assert self._con is not None
        rows = self._con.execute(
            """
            SELECT trace_id,
                   MIN(ts) AS start_ts,
                   MAX(ts) AS end_ts,
                   COUNT(*) AS event_count
            FROM events
            GROUP BY trace_id
            ORDER BY start_ts
            """
        ).fetchall()
        summaries: list[_TraceSummary] = []
        for tid_str, start_ts, end_ts, count in rows:
            summaries.append(
                _TraceSummary(
                    trace_id=UUID(tid_str),
                    task_id="",  # populated by Trace metadata table (Phase 5 hook)
                    model_id="",
                    event_count=int(count),
                    start_ts=start_ts,
                    end_ts=end_ts,
                )
            )
        return summaries

    def iter_events(self, trace_id: UUID) -> Iterator[ToolCall | ToolResult | ModelMessage | Verdict]:
        """Yield events for ``trace_id`` in (trace_id, seq) order.

        Each row is deserialized via the discriminated union so callers get
        the concrete subclass. ``payload`` is the canonical JSON string
        produced by :func:`pydantic.BaseModel.model_dump_json`; we re-validate
        from it to apply the v2 alias-resolution rules at read time too.
        """
        assert self._con is not None
        rows = self._con.execute(
            "SELECT payload FROM events WHERE trace_id = ? ORDER BY seq",
            (str(trace_id),),
        ).fetchall()
        for (payload_json,) in rows:
            # DuckDB returns JSON columns as Python ``str`` already.
            data: Any = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
            yield _EVENT_TYPE_ADAPTER.validate_python(data)

    def read_trace(self, trace_id: UUID) -> Trace | None:
        """Reconstruct the full Trace for ``trace_id``.

        Returns ``None`` when the trace id is unknown (not an error). The
        ``task_id`` field defaults to an empty string; ``model_id`` falls
        back to the first observed :class:`ModelMessage`'s model_id, or the
        placeholder ``"unknown"`` if no model messages were recorded.

        Phase 1 emits no top-level trace metadata table; the bench harness
        (Phase 5) will start populating ``task_id`` properly.
        """
        events = list(self.iter_events(trace_id))
        if not events:
            return None
        first = events[0]
        last = events[-1]
        # Pick the recorded model_id from the first ModelMessage when present;
        # otherwise fall back to a sentinel that satisfies the TypeIdStr
        # min_length=1 constraint.
        model_id = "unknown"
        for ev in events:
            mid = getattr(ev, "model_id", None)
            if isinstance(mid, str) and mid:
                model_id = mid
                break
        return Trace(
            trace_id=trace_id,
            task_id="",
            model_id=model_id,
            events=events,
            final_answer="",
            start_ts=first.ts,
            end_ts=last.ts,
        )


# ---------------------------------------------------------------------------
# Schema DDL — single source of truth
# ---------------------------------------------------------------------------


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS events (
    trace_id VARCHAR NOT NULL,
    seq      INTEGER NOT NULL,
    kind     VARCHAR NOT NULL,
    ts       VARCHAR NOT NULL,
    payload  JSON    NOT NULL,
    PRIMARY KEY (trace_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_trace_id_kind ON events(trace_id, kind);
"""


__all__ = [
    "DEFAULT_ROTATE_AFTER_BYTES",
    "TraceReader",
    "TraceWriter",
]
