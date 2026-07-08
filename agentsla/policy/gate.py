"""PolicyGate — the runtime hooks adapter that consults :class:`Policy`.

Implements :class:`agentsla.adapters.base.RuntimeHooks`:

  * ``on_tool_call(call)`` returns a :class:`HookDecision`:
      ``allow`` → True/False; ``reason`` explains; ``rewrite_args``
      carries the rewritten dict (or ``None``); ``args_hash`` is the
      canonical hash of the *post-decision* args (used by the writer
      to defend TOCTOU at TRACE-04 time).
  * ``on_tool_result(call, result)`` is a passthrough (no enforcement
    needed post-tool; the post-execution :class:`Verdict` chain in
    Phase 3 handles result-side checks).
  * ``on_final_answer(trace, verdict)`` records nothing in Phase 2 —
    the classifier (Phase 4) consumes the final trace; Phase 2 leaves
    a hook surface for it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from agentsla.adapters.base import HookDecision
from agentsla.core.events import (
    ToolCall,
    ToolResult,
    Trace,
    Verdict,
    canonical_args_hash,
)
from agentsla.policy.egress import EgressRule, luhn_valid
from agentsla.policy.schema import Policy


class PolicyGate:
    """RuntimeHooks impl that enforces a :class:`Policy`.

    Args:
        policy: Loaded (frozen) policy document.
        clock: Optional clock callable returning ``datetime``. Defaults to
            ``datetime.now(UTC)``; tests inject a fixed-time clock to keep
            wall-clock sensitive checks deterministic.

    Notes:
        The gate maintains an in-memory call-count keyed by
        ``(trace_id, tool_name)``; the bench harness (Phase 5) reads
        these counters via :attr:`call_counts` to surface "denied by
        policy" counts in the report.
    """

    def __init__(self, policy: Policy, *, clock: Any | None = None) -> None:
        self.policy = policy
        self._clock = clock
        self._call_counts: dict[tuple[str, str], int] = defaultdict(int)
        self._audit: list[dict[str, Any]] = []

    # ----- RuntimeHooks -----

    def on_tool_call(self, call: ToolCall) -> HookDecision:
        """Run policy checks and return a single decision.

        Evaluation order (first FAIL wins):
            1. Tool membership in ``allowed_tools``.
            2. Per-tool ``json_schema`` validation (if installed).
            3. Per-tool ``max_calls`` enforcement.
            4. Global ``max_calls_per_trace``.
            5. Egress regex scan against stringified arg values.
        """
        # 1. membership
        if not self.policy.allowed_tools:
            return self._deny(call, reason="policy: allowed_tools is empty (default deny all)")
        if call.tool not in self.policy.allowed_tools:
            return self._deny(
                call,
                reason=f"policy: tool {call.tool!r} is not in allowed_tools",
            )

        # 2. per-tool JSON schema (lazy — only present when installed).
        per_tool = self._per_tool(call.tool)
        if per_tool is not None and per_tool.json_schema:
            try:
                import json

                import jsonschema  # noqa: F401

                schema = (
                    per_tool.json_schema
                    if isinstance(per_tool.json_schema, dict)
                    else json.loads(per_tool.json_schema)
                )
                jsonschema.validate(instance=call.args, schema=schema)
            except ImportError:  # pragma: no cover — jsonschema is optional
                pass
            except Exception as exc:
                return self._deny(
                    call,
                    reason=f"policy: tool {call.tool!r} arg schema mismatch: {exc}",
                )

        # 3. + 4. call counts
        key = (str(call.trace_id), call.tool)
        self._call_counts[key] += 1
        if per_tool is not None and per_tool.max_calls is not None and self._call_counts[key] > per_tool.max_calls:
            return self._deny(
                call,
                reason=f"policy: tool {call.tool!r} exceeded max_calls={per_tool.max_calls}",
            )
        if self._call_counts[key] > self.policy.max_calls_per_trace:
            return self._deny(
                call,
                reason=f"policy: trace exceeded max_calls_per_trace={self.policy.max_calls_per_trace}",
            )

        # 5. egress scan
        hit = self._scan_egress(call.args)
        if hit is not None:
            if hit.severity == "deny":
                return self._deny(
                    call,
                    reason=f"policy: egress detector {hit.name!r} hit in args",
                )
            if hit.severity == "rewrite":
                rewritten = {**call.args, **{"_redacted_by": hit.name}}
                return self._allow(
                    call,
                    reason=f"policy: egress {hit.name!r} redacted (severity=rewrite)",
                    rewrite_args=rewritten,
                )

        # Pass: emit a stable allow with the recomputed args hash.
        return self._allow(call, reason="policy: allowed")

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        return None  # Phase 3 verifier + Phase 4 classifier attach here.

    def on_final_answer(self, trace: Trace, verdict: Verdict | None) -> None:
        return None

    # ----- internals -----

    def _per_tool(self, tool_name: str) -> Any | None:
        for rule in self.policy.tool_rules:
            if rule.name == tool_name:
                return rule
        return None

    def _deny(self, call: ToolCall, *, reason: str) -> HookDecision:
        decision = HookDecision(allow=False, reason=reason)
        self._audit.append(
            {"trace": str(call.trace_id), "tool": call.tool, "decision": "deny", "reason": reason}
        )
        return decision

    def _allow(
        self,
        call: ToolCall,
        *,
        reason: str,
        rewrite_args: dict[str, Any] | None = None,
    ) -> HookDecision:
        post = rewrite_args if rewrite_args is not None else call.args
        decision = HookDecision(
            allow=True,
            reason=reason,
            rewrite_args=rewrite_args,
            extra={"args_hash": canonical_args_hash(post)},
        )
        self._audit.append(
            {
                "trace": str(call.trace_id),
                "tool": call.tool,
                "decision": "allow" if rewrite_args is None else "rewrite",
                "reason": reason,
            }
        )
        return decision

    def _scan_egress(self, args: dict[str, Any]) -> EgressRule | None:
        """Return the first rule that hits; ``None`` if clean.

        Walks every ``str`` value (recursive into lists/dicts) and tests
        against every regex. PAN hits additionally require Luhn
        validity before they count.
        """
        for rule in self.policy.egress_rules:
            pat = rule.pattern
            for value in _iter_str_values(args):
                if pat.search(value):
                    if rule.name == "pan" and not _extract_pan_luhn_ok(value, pat):
                        continue
                    return rule
        return None

    # ----- observability -----

    @property
    def call_counts(self) -> dict[tuple[str, str], int]:
        """Per-trace per-tool call counts (read-only)."""
        return dict(self._call_counts)

    @property
    def audit(self) -> list[dict[str, Any]]:
        """Audit trail of every decision (read-only)."""
        return list(self._audit)


# ---- helpers --------------------------------------------------------------


def _iter_str_values(obj: Any) -> Iterable[str]:
    """Recursively yield every string leaf in a JSON-compatible structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_str_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_str_values(v)


def _extract_pan_luhn_ok(value: str, pattern: re.Pattern[str]) -> bool:
    """Test whether ``value`` contains a Luhn-valid PAN matching ``pattern``."""

    for match in pattern.finditer(value):
        if luhn_valid(match.group(0)):
            return True
    return False


__all__ = ["PolicyGate"]


# Late import to avoid circular between gate.py and egress.py type re-exports.
import re  # noqa: E402
