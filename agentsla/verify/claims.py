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


def extract_numeric_claims(text: str) -> list[NumericClaim]:
    """Find every numeric claim in ``text`` (order-preserving, deduped)."""
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
