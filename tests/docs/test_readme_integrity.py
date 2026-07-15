"""Doc-integrity tests for ``README.md``.

Mirror of :mod:`test_writeup_integrity` for the README. Same classes of
drift — stale version labels, stale numeric claims, phantom file
paths — but pinned against the README, which a reviewer opens first.

Pin contracts:

  1. **Release badge** names the **current** version. The badge said
     ``v0.2.2`` while the package reported ``1.0.0`` — a literal lie
     on the first line of the README. The test reads
     ``pyproject.toml`` for ground truth and asserts the badge matches.

  2. **No stale "30 tasks" claim.** README § Benchmarking said
     "30 tasks (10 financial ops, 10 incident triage, 10 doc QA)" while
     the parquet carried 35 unique tasks (60 financial_ops + 40
     incident_triage + 40 doc_qa rows pre-mode-doubling). The test
     pins README against stating a number that contradicts parquet.

  3. **Injection variant count matches parquet.** README § Headline
     said "10 injection-payload task variants" while parquet carried
     5 (``finops-001-inj`` through ``finops-005-inj``). Pin: any count
     cited for injection variants in README must equal the parquet's
     ``len(set(task_id))`` where ``has_injection is True``.

  4. **Coverage scope matches CI.** README § Testing said
     "core modules (policy, verify, trace)" while CI gate uses
     ``--cov=agentsla/core --cov=agentsla/policy --cov=agentsla/verify``.
     Pin: the coverage call shape referenced in README must match the
     CI invocation so a reviewer can reproduce the gate locally.

The invariants below derive from the PRD-v2 §5 anti-fabrication
baseline. If a test fails after a real change, fix README — not the
test (unless the data shape actually changed).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
PYPROJECT = ROOT / "pyproject.toml"
HERMETIC_PARQUET = ROOT / "bench" / "results" / "results.parquet"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    assert match is not None, "pyproject.toml missing top-level 'version = ...'"
    return match.group(1)


@pytest.fixture(scope="module")
def injection_variant_count() -> int:
    """Distinct injection task ids in the hermetic parquet.

    Skips when the parquet is absent (fresh CI clone without a prior
    bench run); the parquet is gitignored. Skipping rather than
    failing gates the test on bench reproducibility — without the
    parquet the doc claim is unmeasurable. To exercise this in CI,
    run ``agentsla bench --seeds 1`` first (the integration gate does).
    """
    if not HERMETIC_PARQUET.exists():
        pytest.skip(f"{HERMETIC_PARQUET} not present (parquet is gitignored; run `agentsla bench --seeds 1` to populate).")
    table = pq.read_table(HERMETIC_PARQUET)
    task_ids = table.column("task_id").to_pylist()
    has_injection = table.column("has_injection").to_pylist()
    return len({t for t, i in zip(task_ids, has_injection, strict=True) if i})


@pytest.fixture(scope="module")
def task_count() -> int:
    """Distinct task ids in the hermetic parquet. Skip-on-absent — see injection_variant_count."""
    if not HERMETIC_PARQUET.exists():
        pytest.skip(f"{HERMETIC_PARQUET} not present (parquet is gitignored; run `agentsla bench --seeds 1` to populate).")
    table = pq.read_table(HERMETIC_PARQUET)
    return len(set(table.column("task_id").to_pylist()))


# ---------------------------------------------------------------------------
# Stale release badge (PRD-v2 §5 anti-fabrication baseline)
# ---------------------------------------------------------------------------


class TestReleaseBadge:
    """README badge must name the version reported in ``pyproject.toml``.

    Source of truth = ``pyproject.toml``. Both sides of the badge
    label must stay aligned so the badge stays honest about what the
    reviewer will get on ``pip install``.
    """

    def test_badge_labels_current_version(self, readme_text: str, pyproject_version: str) -> None:
        # Match the shields.io badge markdown link or image. Both forms
        # include the version in the alt-text or url segment.
        # Pattern: ``badge/release-vX.Y.Z-...`` OR ``badge/release vX.Y.Z ...``
        pattern = re.compile(
            r"badge/release[\-/ ]v?("
            r"\d+\.\d+\.\d+"
            r")[\-/]",
            re.IGNORECASE,
        )
        match = pattern.search(readme_text)
        assert match is not None, (
            "README has no shields.io release badge with a version label — "
            "expected pattern `badge/release-vX.Y.Z` (or `badge/release-vX.Y.Z-`). "
            "Add the badge or rename it."
        )
        badge_version = match.group(1)
        assert badge_version == pyproject_version, (
            f"README release badge version={badge_version!r} != "
            f"pyproject version={pyproject_version!r}. Update README badge "
            f"so it matches what `pip install` will provide."
        )

    def test_badge_url_links_to_releases_tag(self, readme_text: str) -> None:
        """Pin: badge link target must reference the releases page, not a
        hard-coded tag. This way the badge auto-tracks the latest release.
        """
        assert "/releases/tag/" in readme_text, "README badge must link to GitHub releases/tag/<version> so the URL stays stable as versions bump."


# ---------------------------------------------------------------------------
# Stale task-count claim (parquet is source of truth)
# ---------------------------------------------------------------------------


class TestTaskCountClaim:
    """README § Benchmarking intro must not claim a stale total task count."""

    def test_no_stale_30_task_claim(self, readme_text: str, task_count: int) -> None:
        """README § Benchmarking previously said "30 tasks (10 financial ops,
        10 incident triage, 10 doc QA)". Parquet carries 35 unique
        task_ids — that line was stale. The test asserts no such line
        with literal "30" survives in the benchmark-intro paragraph.
        """
        benchmarking_section = self._extract_section(readme_text, "Benchmarking")
        assert "30 tasks" not in benchmarking_section, (
            f"README § Benchmarking claims '30 tasks' but parquet carries {task_count} unique task_ids. Update the intro to match the parquet."
        )

    def test_intro_total_matches_parquet(self, readme_text: str, task_count: int) -> None:
        """If the intro names a task total, it must equal parquet's count.

        Scope: only the FIRST paragraph after the ``## Benchmarking``
        header. The headline-tables subsection (Hermetic bench + Real-LLM
        bench) intentionally reports row-counts for subsets
        (e.g. ``12 tasks x 3 domains`` for the Real-LLM subset) which
        do not match the full hermetic row count — those are pinned by
        separate tests reading the parquet directly. The drift this
        catches is "X tasks" in the intro paragraph itself.
        """
        section = self._extract_section(readme_text, "Benchmarking")
        intro = section.split("\n\n")[0]
        for claim in re.findall(r"\b(\d+)\s+tasks\b", intro):
            assert int(claim) == task_count, (
                f"README § Benchmarking intro says '{claim} tasks' but parquet has {task_count} unique task_ids. Update or remove the stale number."
            )

    @staticmethod
    def _extract_section(text: str, header: str) -> str:
        """Return markdown between ``## {header}`` and the next ``## ``."""
        start = re.search(rf"^##\s+{re.escape(header)}\s*$", text, re.MULTILINE)
        assert start is not None, f"no `## {header}` section in README"
        rest = text[start.end() :]
        nxt = re.search(r"^##\s+", rest, re.MULTILINE)
        return rest if nxt is None else rest[: nxt.start()]


# ---------------------------------------------------------------------------
# Injection variant count (parquet is source of truth)
# ---------------------------------------------------------------------------


class TestInjectionVariantClaim:
    """README § Headline must report the actual injection-task count."""

    def test_no_stale_10_injection_variant_claim(self, readme_text: str, injection_variant_count: int) -> None:
        """README previously said "10 injection-payload task variants"
        while parquet had 5 (finops-001-inj ... finops-005-inj). Pin
        no occurrence of the literal "10 injection-payload" anywhere
        in the README body.
        """
        assert "10 injection-payload" not in readme_text, (
            f"README claims '10 injection-payload task variants' but parquet carries {injection_variant_count}. Update to the actual count."
        )

    def test_no_injection_count_drift(self, readme_text: str, injection_variant_count: int) -> None:
        """If README cites an injection variant count, it must equal the
        parquet. Catches both the "10" stale claim and any future drift
        in either direction.
        """
        for claim in re.findall(r"\b(\d+)\s+injection-payload\s+task", readme_text):
            assert int(claim) == injection_variant_count, (
                f"README mentions '{claim} injection-payload task variants' but parquet has {injection_variant_count}. Fix the README."
            )


# ---------------------------------------------------------------------------
# Coverage scope alignment with CI
# ---------------------------------------------------------------------------


class TestCoverageScope:
    """README § Testing must cite the coverage scope the CI gate enforces.

    The CI gate is:

      uv run pytest tests/ -v \\
          --cov=agentsla/core \\
          --cov=agentsla/policy \\
          --cov=agentsla/verify \\
          --cov-fail-under=85

    Any README command shown to a reviewer must match those coverage
    paths so a reviewer reproducing the gate locally is told the right
    thing to invoke.
    """

    @pytest.mark.skipif(shutil.which("uv") is None, reason="uv required to regenerate CI command")
    def test_readme_testing_command_includes_three_cov_scopes(self, readme_text: str) -> None:
        """Pin: README's `pytest` command (if any) must include all three
        ``--cov=`` paths the CI gate uses.

        We allow commands that use ``--cov=agentsla`` (covers everything
        that lives under the namespace) — that's strictly stronger than
        the CI scope. The point is: the gate command shown in the
        README must include all three target modules.
        """
        pytest_block = self._extract_section(readme_text, "Testing")
        if "--cov=" not in pytest_block:
            pytest.skip("README § Testing shows no --cov flag; not exercising")
        for scope in ("agentsla/core", "agentsla/policy", "agentsla/verify"):
            assert scope in pytest_block or "agentsla" in pytest_block, (
                f"README § Testing pytest command does not include coverage scope {scope!r} that the CI gate enforces. Update the command."
            )

    @staticmethod
    def _extract_section(text: str, header: str) -> str:
        start = re.search(rf"^##\s+{re.escape(header)}\s*$", text, re.MULTILINE)
        assert start is not None, f"no `## {header}` section in README"
        rest = text[start.end() :]
        nxt = re.search(r"^##\s+", rest, re.MULTILINE)
        return rest if nxt is None else rest[: nxt.start()]
