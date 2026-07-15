"""Doc-integrity tests for ``WRITEUP.md``.

Pins WRITEUP.md against three classes of drift that the v0.2 close
retracted from CHANGELOG (see ``CHANGELOG.md § Correction log
(2026-07-14)``):

  1. **Stale version labels.** A release-line narrative that says
     "v1.0 (this push)" when the actual release is v0.2.x is the same
     kind of fabrication the retraction caught. Any "What shipped in
     v1.0 (this push)" header, "v1 push added N tests", or "v1.0
     honest gaps" header must not appear.

  2. **Stale test-count claims.** A specific integer like "432 passed"
     becomes a lie the moment a CI run goes green with 487. The test
     pins that WRITEUP must not embed a stale hard-coded number; the
     surface must reference the live pytest run, not a frozen count.

  3. **Phantom file paths.** Every ``agentsla/...`` or ``tests/...``
     path referenced from WRITEUP must resolve on disk. The v0.2 close
     had to retract a claim that "test_claude_sdk_parity.py" existed
     because at retraction-time it didn't (it was re-added since);
     the integrity test defends against future drift in either
     direction.

The invariants below were derived from the PRD-v2 §5 anti-fabrication
baseline ("No fabricated features. Document what exists, not roadmap")
plus the workspace CLAUDE.md session protocol ("End: paste test/bench
output as evidence or state blocker").

If a test fails after a real change, the right fix is almost always
to update WRITEUP — not to soften the test. The test is the audit
trail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRITEUP = ROOT / "WRITEUP.md"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def writeup_text() -> str:
    return WRITEUP.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Stale version labels (PRD-v2 §5 anti-fabrication baseline)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_phrase",
    [
        "What shipped in v1.0 (this push)",
        "Quality gates (v1.0)",
        "v1.0 honest gaps",
        "v1 push added",
        "v1 closes the third-adapter gap",
    ],
)
def test_writeup_does_not_claim_v1_0(writeup_text: str, forbidden_phrase: str) -> None:
    """No narrative claims about a v1.0 release that doesn't exist in CHANGELOG.

    The release line per ``CHANGELOG.md`` is v0.1.0 → v0.2.0 → v0.2.1 →
    v0.2.2. A "v1.0 (this push)" header implies a release tag that was
    never cut; this is the same drift the v0.2 close retraction caught
    in CHANGELOG.
    """
    assert forbidden_phrase not in writeup_text, (
        f"WRITEUP.md contains '{forbidden_phrase}'. The release line is v0.2.2 "
        "per CHANGELOG.md — reframe the section to match the actual release, "
        "or delete it. The v0.2 close retracted an identical drift; do not "
        "re-introduce it via WRITEUP."
    )


def test_writeup_version_label_matches_changelog() -> None:
    """Any version label in WRITEUP must be one that exists in CHANGELOG.

    Permits v0.1.0, v0.2.0, v0.2.1, v0.2.2 (and minor variations like
    "v0.2 / v0.2.2"). Flags anything else — including the retracted
    v1.0.0.
    """
    text = WRITEUP.read_text(encoding="utf-8")
    # Find all vX.Y.Z tokens in WRITEUP, including prose like "at v1".
    version_like = sorted(set(re.findall(r"\bv0?\.?\d+(?:\.\d+)?(?:\.\d+)?\b", text)))
    # Filter to ones that look like release labels (start with v).
    release_tokens = [v for v in version_like if v.startswith("v")]
    # Allowed tokens: exact list from CHANGELOG + the natural "v0.2" shorthand.
    allowed = {
        "v0.1",
        "v0.1.0",
        "v0.2",
        "v0.2.0",
        "v0.2.1",
        "v0.2.2",
        "v0.3",  # future shorthand is fine; no v1.0 shorthand
    }
    drift = [v for v in release_tokens if v not in allowed]
    assert not drift, (
        f"WRITEUP.md references versions not in CHANGELOG: {drift}. "
        "Allowed: v0.1, v0.1.0, v0.2, v0.2.0, v0.2.1, v0.2.2. "
        "If a new release was cut, add it to CHANGELOG.md first; if a "
        "claim is forward-looking, mark it [FUTURE] not 'shipped'."
    )


# ---------------------------------------------------------------------------
# Stale test-count claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stale_number",
    [
        "432 passed",  # we are at 487
        "295 → 432",  # delta is wrong
        "432 passed, ~12s",
        "v1 push added 137",  # delta math broken
    ],
)
def test_writeup_does_not_embed_stale_test_counts(writeup_text: str, stale_number: str) -> None:
    """WRITEUP must not embed a specific test count that drifts from reality.

    The honest move is to point at ``agentsla report`` /
    ``pytest tests/ -q`` (the live source) rather than a frozen
    integer. If WRITEUP needs a number, it must be tagged with the
    date it was measured.
    """
    assert stale_number not in writeup_text, (
        f"WRITEUP.md embeds a stale test count '{stale_number}'. The live "
        "count is whatever `pytest tests/ -q --co | tail -1` reports. "
        "Either remove the number, tag it with the measurement date, "
        "or rephrase to point at the live command."
    )


def test_writeup_test_count_claim_matches_pytest_collection() -> None:
    """If WRITEUP claims a test count, it must equal what pytest collects today.

    Soft check: pytest collection itself isn't invoked here (this is a
    pure doc test), but any explicit "N tests pass" line in WRITEUP
    must be preceded by a date stamp and a measure-source citation.
    """
    text = WRITEUP.read_text(encoding="utf-8")
    # Match "<N> tests" or "<N> passed" near a pytest mention.
    claims = re.findall(
        r"(?:pytest|tests).*?(\d{2,4})\s+(?:tests?\s+pass|pass(?:ed)?)",
        text,
        flags=re.IGNORECASE,
    )
    for n in claims:
        pytest.fail(
            f"WRITEUP.md embeds a test count '{n} tests passed' next to a "
            "pytest reference. Either remove the number, replace it with "
            "a measurement date + `pytest tests/ -q --co` reproduction, "
            "or move the number to a CHANGELOG-style dated entry."
        )


# ---------------------------------------------------------------------------
# Phantom file paths
# ---------------------------------------------------------------------------


def _extract_paths(text: str) -> list[str]:
    """Extract ``agentsla/...`` and ``tests/...`` paths mentioned in prose.

    Heuristic: a backtick-wrapped token that starts with one of the
    root dirs and contains a ``.py`` or ``.md`` suffix. We deliberately
    ignore anchors / URLs / line numbers to keep the test focused on
    "is the file there?".
    """
    pattern = re.compile(r"`((?:agentsla|tests)/[A-Za-z0-9_./-]+\.(?:py|md))`")
    return sorted(set(pattern.findall(text)))


def test_writeup_referenced_paths_resolve(writeup_text: str) -> None:
    """Every agentsla/ or tests/ path backtick-wrapped in WRITEUP must exist.

    Guards against two failure modes:

      * Claiming a path that doesn't exist (the v0.2 close retraction).
      * Moving a file and forgetting to update the writeup.
    """
    paths = _extract_paths(writeup_text)
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, (
        f"WRITEUP.md references paths that don't exist on disk: {missing}. "
        "Either restore the file, update the reference, or remove the claim. "
        "Do not soften this test — the v0.2 close retracted an identical drift."
    )


# ---------------------------------------------------------------------------
# Failure-modes count claim (cross-doc consistency)
# ---------------------------------------------------------------------------


def test_writeup_failure_modes_count_matches_doc() -> None:
    """WRITEUP § "Failure modes we observed" must reflect the actual count.

    The claim is anchored at the top of the failure-modes section.
    We cross-check against ``docs/failure-modes.md`` so the two docs
    cannot drift silently.
    """
    fm_text = (ROOT / "docs" / "failure-modes.md").read_text(encoding="utf-8")
    # Count distinct numbered sections (## N. or ## 7. etc.).
    headings = re.findall(r"^##\s+(\d+)\.\s+", fm_text, flags=re.MULTILINE)
    n_modes = len(headings)
    writeup = WRITEUP.read_text(encoding="utf-8")
    # WRITEUP must mention the actual count somewhere when it makes the claim.
    # The § "Failure-mode appendix" uses "at p99.9" framing; only the
    # in-narrative claim ("6 → 15") needs to be checked.
    if "6 → 15" in writeup or "6 → 15" in writeup.replace(" ", ""):
        assert n_modes == 15, (
            f"WRITEUP claims '6 → 15' failure modes but docs/failure-modes.md has {n_modes} numbered sections. Update the doc or the writeup."
        )
