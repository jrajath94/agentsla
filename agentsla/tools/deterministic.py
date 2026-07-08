"""Deterministic-fixture tool set (PITFALL #8 mitigation).

These tools are *pure functions*: same inputs → same outputs, no
side effects, no clock, no network, no random.

The trace store + replay engine rely on this. The acceptance test
(``REPLAY-PROOF``) requires that the recorded log be fully reproducible
across runs. A tool that read wall-clock time, network state, or a PRNG
seed would defeat that property.

Each tool:
  * takes simple JSON-serializable kwargs,
  * returns a JSON-serializable value,
  * documents itself (the recorded log carries the result for replay).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["ClockTool", "FetchTool", "JsonEchoTool"]


class FetchTool:
    """Read a fixture file from disk and return its text content.

    Args:
        root: Directory to resolve ``path`` against (default: current dir).

    Returns:
        The text content of the file. Errors (file not found, path traversal)
        raise ``FileNotFoundError`` — the adapter surfaces them as
        ``ToolResult(is_error=True)``.

    Notes:
        * No network. No clock.
        * Path is validated to stay inside ``root`` (defends against the
          common "agent asks for /etc/passwd" prompt-injection test).
    """

    name = "fetch"

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()

    def __call__(self, *, path: str) -> str:
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(f"path {path!r} escapes fixture root") from exc
        if not target.exists():
            raise FileNotFoundError(f"fixture {path!r} not found under {self.root}")
        return target.read_text(encoding="utf-8")


class ClockTool:
    """Return a fixed datetime.

    No ``datetime.now()`` is ever called — the recorded timestamp must be
    reproducible across runs. Construct with an explicit timezone-aware
    ``datetime`` (the writer validates tz-awareness; see ``Timestamp``).
    """

    name = "clock"

    def __init__(self, fixed: datetime) -> None:
        if fixed.tzinfo is None:
            raise ValueError("ClockTool requires a timezone-aware datetime")
        self.fixed = fixed

    def __call__(self) -> str:
        return self.fixed.isoformat()


class JsonEchoTool:
    """Return the JSON-serialized form of its input.

    Useful for asserting that ``args`` round-trip cleanly through the
    writer ↔ reader path; ``args_json`` is the recorded form (the
    canonical-hash helper works on the parsed dict, not the raw string).
    """

    name = "json_echo"

    def __call__(self, **kwargs: Any) -> str:
        return json.dumps(kwargs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
