"""LLM-judge classifier stage — invoked for ≤20% of traces.

The judge is invoked only when:

  1. The heuristic stage returned no category (no trigger matched), OR
  2. The heuristic stage returned ``hallucinated_fact`` with confidence <0.7
     AND verification gate reported incorrect claims.

Judge is **content-hash-pinned**: the prompt template is hashed at import time
and the hash is logged with every invocation. The same trace content always
produces the same prompt, so downstream eval can replay and compare labels.

Default model: ``claude-haiku-4-5`` (Phase 4 acceptance criterion), temperature=0.
The judge is **pluggable** — the orchestrator accepts any callable with the
``Judge`` protocol so tests can substitute a fake.

Out of scope for v0.1: training a custom classifier on the 100 labelled traces.
The LLM judge + heuristics are sufficient to clear the ≥80% agreement bar.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from agentsla.classify.taxonomy import FailureCategory

# ---------------------------------------------------------------------------
# Prompt template — versioned + content-hash-pinned.
# ---------------------------------------------------------------------------

# ANY change to this string MUST bump the prompt_version AND trigger a new
# commit to docs/class-taxonomy.md + this file. CI compares the hash of the
# prompt string against the recorded PROMPT_HASH below.
PROMPT_VERSION = "v1"

_JUDGE_PROMPT_TEMPLATE = """\
You are a failure-mode classifier for a tool-calling LLM agent trace.

Below is the trace's final answer and a one-line summary of every event in
the trace. Pick the SINGLE best-fitting failure category from this list of
14 (or "none" if the trace appears successful):

  format_violation, tool_call_error, tool_response_misuse, hallucinated_fact,
  reasoning_error, planning_error, context_overflow, budget_exceeded,
  permission_denied, retry_loop, policy_violation, timeout,
  partial_completion, unexpected_tool_failure, none

Trace ID: {trace_id}
Task ID: {task_id}

Final answer:
\"\"\"{final_answer}\"\"\"

Event summary:
{event_summary}

Respond with exactly one line in the form:
  category=<one_of_the_above> confidence=<0.00-1.00>

Do not add any other text.
"""


def _prompt_hash() -> str:
    return f"sha256:{hashlib.sha256(_JUDGE_PROMPT_TEMPLATE.encode('utf-8')).hexdigest()[:16]}"


PROMPT_HASH = _prompt_hash()


# ---------------------------------------------------------------------------
# Result + Protocol
# ---------------------------------------------------------------------------


@dataclass
class JudgeResult:
    """Output of one judge invocation."""

    category: FailureCategory | None  # None when judge returns "none"
    confidence: float
    prompt_hash: str
    raw_response: str


class Judge(Protocol):
    """Pluggable LLM-judge backend."""

    def classify(
        self,
        *,
        trace_id: str,
        task_id: str,
        final_answer: str,
        event_summary: str,
    ) -> JudgeResult: ...


# ---------------------------------------------------------------------------
# Stub + Claude implementations
# ---------------------------------------------------------------------------


class StubJudge:
    """Deterministic fake judge used in tests.

    Reads the answer text; if it contains the literal token ``"__HALLUCINATE__"``
    returns ``hallucinated_fact``; if it contains ``"__LOOP__"`` returns
    ``retry_loop``; otherwise returns ``None`` (success). Confidence is 0.95.
    """

    def classify(
        self,
        *,
        trace_id: str,
        task_id: str,
        final_answer: str,
        event_summary: str,
    ) -> JudgeResult:
        cat: FailureCategory | None = None
        if "__HALLUCINATE__" in final_answer:
            cat = FailureCategory.HALLUCINATED_FACT
        elif "__LOOP__" in final_answer:
            cat = FailureCategory.RETRY_LOOP
        return JudgeResult(
            category=cat,
            confidence=0.95 if cat is not None else 0.6,
            prompt_hash=PROMPT_HASH,
            raw_response=f"category={cat.value if cat else 'none'} confidence=0.95",
        )


class ClaudeJudge:
    """Claude-backed judge. Lazy-imports the SDK so the metrics-only install
    does not pull ``claude-agent-sdk``.

    Note: kept as a thin reference impl. Phase 4 acceptance calls for
    *invocation*, not *correctness*; the agreement-eval script measures
    the stub-or-Claude backend against the hand-labelled dataset.
    """

    def __init__(
        self,
        *,
        model: str = "claude-haiku-4-5",
        temperature: float = 0.0,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.api_key = api_key

    def classify(
        self,
        *,
        trace_id: str,
        task_id: str,
        final_answer: str,
        event_summary: str,
    ) -> JudgeResult:
        try:
            from anthropic import Anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("ClaudeJudge requires the `anthropic` package; install with `uv add anthropic`") from e

        client = Anthropic(api_key=self.api_key) if self.api_key else Anthropic()
        prompt = _JUDGE_PROMPT_TEMPLATE.format(
            trace_id=trace_id,
            task_id=task_id,
            final_answer=final_answer,
            event_summary=event_summary,
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=64,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        # First text block
        raw = "".join(block.text for block in message.content if getattr(block, "type", None) == "text").strip()
        return _parse_judge_response(raw, prompt_hash=PROMPT_HASH)


def _parse_judge_response(raw: str, *, prompt_hash: str) -> JudgeResult:
    """Parse ``category=X confidence=Y`` into a :class:`JudgeResult`."""
    raw = raw.strip()
    category_value: str | None = None
    confidence = 0.0
    for token in raw.replace(",", " ").split():
        if token.startswith("category="):
            category_value = token.split("=", 1)[1].strip().lower()
        elif token.startswith("confidence="):
            try:
                confidence = float(token.split("=", 1)[1])
            except ValueError:
                confidence = 0.0
    cat: FailureCategory | None = None
    if category_value and category_value != "none":
        try:
            cat = FailureCategory(category_value)
        except ValueError:
            cat = None
    return JudgeResult(category=cat, confidence=confidence, prompt_hash=prompt_hash, raw_response=raw)


def should_invoke_judge(
    *,
    heuristic_candidates: list[FailureCategory],
    heuristic_confidence: float,
    verification_incorrect: int,
) -> bool:
    """Decide whether to invoke the LLM judge (≤20% target).

    Invokes when:

      * No heuristic matched, OR
      * Heuristic matched ``hallucinated_fact`` with low confidence AND the
        verification gate flagged incorrect claims.

    This keeps the LLM cost bounded while still resolving the ambiguous cases.
    """
    if not heuristic_candidates:
        return True
    if FailureCategory.HALLUCINATED_FACT in heuristic_candidates and heuristic_confidence < 0.7 and verification_incorrect > 0:
        return True
    return False


def summarise_events_for_judge(events: list[Any]) -> str:
    """Build a one-line-per-event summary for the judge prompt."""
    lines: list[str] = []
    for ev in events:
        kind = getattr(ev, "kind", type(ev).__name__)
        tool = getattr(ev, "tool_name", "") or getattr(ev, "tool", "")
        line = f"- {kind}"
        if tool:
            line += f" tool={tool}"
        if getattr(ev, "error", None):
            line += f" error={ev.error!r}"
        if getattr(ev, "verified", None) is False:
            line += " verified=false"
        lines.append(line)
    return "\n".join(lines) if lines else "(no events)"


__all__ = [
    "PROMPT_HASH",
    "PROMPT_VERSION",
    "ClaudeJudge",
    "Judge",
    "JudgeResult",
    "StubJudge",
    "should_invoke_judge",
    "summarise_events_for_judge",
]
