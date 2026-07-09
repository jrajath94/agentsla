"""Numeric claim extraction (VERIFY-SPEC §1).

A *verifiable* claim is any declarative numeric assertion in the
final-answer text. Recognised forms:

  * Integer: ``42``
  * Float: ``3.14``
  * Currency-prefixed: ``$1,200``, ``€99.50``
  * Percentage-suffixed: ``12.5%``
  * Plain arithmetic expressions: ``2 * 3 + 1``, ``(4 - 1) / 2``

Each extracted :class:`NumericClaim` carries:
  * `text` — the literal substring
  * `value` — the parsed numeric value
  * `kind` — `int | float | expression`
  * `span` — (start, end) for highlight overlap checks

The spec is deliberately loose — false positives are tolerable, false
negatives are not, because the verifier's recompute step is what
actually catches errors.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NumericClaim:
    text: str
    value: Any  # int | float | the literal expression string
    kind: str  # "int" | "float" | "currency" | "percent" | "expression"
    span: tuple[int, int]


_KIND_BY_PATTERN: list[tuple[str, re.Pattern[str]]] = [
    ("percent", re.compile(r"(?<![A-Za-z])-?\d+(?:\.\d+)?\s*%")),
    (
        "currency",
        re.compile(r"(?:\$|€|£|¥)\s?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?"),
    ),
    ("float", re.compile(r"(?<![A-Za-z\.\d])-?\d+\.\d+")),
    ("int", re.compile(r"(?<![A-Za-z\.\d])-?\d+(?!\.\d)")),
]

# Range claim patterns. Recognise "$4.2-$4.5", "4.2 to 4.5",
# "100-150". Suffix multipliers (4.2M-4.5M) are intentionally not
# supported here — the parser stays narrow; per-endpoint multipliers
# would inflate false-positive risk on date/phone spans.
#
# Security note: the second half's sign is gated by ``(?:\s*-)?``,
# requiring a whitespace boundary before the optional sign. This blocks
# the semantic-escape case "4--5" → (4, -5): the first dash is the
# separator, and without this anchor the second dash would slide into
# the second number's optional sign, producing a range with a negative
# high endpoint that downstream verifiers treat as legitimate.
# Whitespace-separated signs (``-4 to -5``) still match because the
# ``\s*`` between the separator and the second half consumes the
# boundary. The pattern is also intentionally narrow on suffix
# multipliers — see ``docs/failure-modes.md § 6``.
_RANGE_PATTERN = re.compile(
    r"(?:\$|€|£|¥)?\s*-?\d+(?:[\.,]\d+)?"
    r"\s*(?:-|–|—|\bto\b)\s*"
    r"(?:\$|€|£|¥)?\s*(?:(?<!-)-)?\d+(?:[\.,]\d+)?"
)


def extract_numeric_claims(text: str) -> list[NumericClaim]:
    """Find every numeric claim in ``text`` (order-preserving, deduped).

    Range claims (``"$4.2M-$4.5M"``, ``"3.5 to 4.0"``) are emitted with
    ``kind="range"`` and ``value=(low, high)``. Downstream verifiers
    interpret a range as "verified if the source value lies within
    [low, high]" — see :class:`NumericVerifier._judge_range`.
    """
    claims: list[NumericClaim] = []
    seen_spans: set[tuple[int, int]] = set()

    # Currency + percent get rendered with stripped punctuation; their
    # "value" is the numeric portion.
    for kind, pattern in _KIND_BY_PATTERN:
        for match in pattern.finditer(text):
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            value = _parse_value(match.group(0), kind)
            claims.append(NumericClaim(text=match.group(0), value=value, kind=kind, span=span))

    # Range claims. The pattern consumes both endpoints; we emit one
    # NumericClaim per match with kind="range" and value=(low, high).
    for match in _RANGE_PATTERN.finditer(text):
        span = match.span()
        if span in seen_spans:
            continue
        seen_spans.add(span)
        low, high = _parse_range(match.group(0))
        if low is None or high is None:
            continue
        claims.append(
            NumericClaim(text=match.group(0), value=(low, high), kind="range", span=span)
        )

    # Arithmetic expressions: a span of digits/operators + parens between
    # two claim boundaries. Cheap heuristic — parsed via :mod:`ast`.
    for span in _find_expression_spans(text):
        if span in seen_spans:
            continue
        seen_spans.add(span)
        snippet = text[span[0] : span[1]]
        claims.append(
            NumericClaim(text=snippet, value=snippet.strip(), kind="expression", span=span)
        )

    claims.sort(key=lambda c: c.span[0])
    return claims


# ---- helpers --------------------------------------------------------------


def _parse_value(raw: str, kind: str) -> int | float:
    """Return the numeric portion stripped of currency / percent markup."""
    cleaned = re.sub(r"[^\d.\-]", "", raw.replace(",", ""))
    cleaned = cleaned.strip(".")
    if not cleaned or cleaned in {"-", ".", "-."}:
        return 0
    if "." in cleaned:
        return float(cleaned)
    return int(cleaned)


def _is_range_match(raw: str) -> bool:  # kept for API compat; always True.
    return True


def _parse_range(raw: str) -> tuple[float | None, float | None]:
    """Split ``"$4.2-$4.5"`` → ``(4.2, 4.5)``.

    Currency symbols are stripped; commas inside numbers are removed.
    Returns ``(None, None)`` if the input cannot be parsed, including:

      * either endpoint is empty / non-numeric after currency stripping
      * both endpoints parse to the same value (likely a typo, not a
        range — e.g., ``"4-4"`` is more reliably parsed as a single
        int claim elsewhere in the pipeline)

    Note: ``raw`` is stripped first. The matcher above may capture
    leading whitespace (e.g., ``" -4 to -5"``), and without stripping
    the internal split regex would treat that whitespace as a second
    separator boundary, producing 4 parts instead of 2.
    """
    raw = raw.strip()
    # Two-alternative split: a digit-anchored dash (the common ``4-5`` /
    # ``100-150`` case) OR whitespace-anchored ``to``. A plain
    # ``\s*-\s*`` alternative would also match the leading sign dash
    # in ``-4 to -5``, producing 4 parts; the digit lookbehind gates
    # the dash split to "this dash is between two numbers".
    parts = re.split(r"(?<=\d)\s*-\s*|\s+to\s+", raw)
    if len(parts) != 2:
        return None, None
    nums: list[float] = []
    for p in parts:
        cleaned = re.sub(r"[$€£¥%\s]", "", p)
        cleaned = cleaned.replace(",", "")
        if not cleaned or cleaned in {"-", ".", "-."}:
            return None, None
        try:
            nums.append(float(cleaned))
        except ValueError:
            return None, None
    # Degenerate range: low == high is almost always a parse artifact
    # (a single value accidentally matched the range pattern, like
    # ``"4-4"``). Returning None here forces the caller's `if low is
    # None or high is None` branch to skip emission, instead of letting
    # the claim silently verify as "any source in [4, 4]".
    if nums[0] == nums[1]:
        return None, None
    if nums[0] > nums[1]:
        nums[0], nums[1] = nums[1], nums[0]
    return nums[0], nums[1]


_EXPR_PATTERN = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*[+\-*/]\s*)+\d+(?:\.\d+)?\b|\(\s*\d+(?:\.\d+)?\s*[+\-*/]\s*\d+(?:\.\d+)?\s*\)"
)


def _find_expression_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _EXPR_PATTERN.finditer(text):
        snippet = m.group(0).strip()
        try:
            ast.parse(snippet, mode="eval")
        except SyntaxError:
            continue
        spans.append(m.span())
    return spans


__all__ = ["NumericClaim", "extract_numeric_claims"]
