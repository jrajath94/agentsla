"""Release-provenance invariants for AgentSLA.

Pins that the latest release version is reachable from ``main`` HEAD.
If a fresh ``git clone`` + ``git checkout <latest>`` would land on a
phase branch instead of ``main``, the version-on-disk is consistent
(pin :mod:`test_release_consistency`) but the version a contributor
gets from default branch is *not* — invariant fails.

Concrete drift this catches: a v1.0.0 tag pushed onto a
``phase-3/writeup-integrity`` branch where ``main`` is still at v0.2.x.
The reader of the repo never sees the latest stable on the branch
that GitHub's default checkout uses.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ``main`` invariants only hold after the release branch is merged. On
# a PR build, the latest tag is by construction on the PR's head branch,
# NOT on ``main`` — the invariant will fire until the PR is merged.
# Skip these two gates on ``pull_request`` events; they catch real drift
# on push-to-main. Mirrors the env-var branching in
# :mod:`test_release_consistency:_branch_tip_ref`.
_IS_PR_BUILD = os.environ.get("GITHUB_EVENT_NAME") == "pull_request"

skip_on_pr = pytest.mark.skipif(
    _IS_PR_BUILD,
    reason=("main invariants only hold AFTER merge; on pull_request the release tag is on the PR's head branch, not main"),
)


def _latest_release_tag() -> str:
    """Reuse the consistency test's filter so both suites share one definition."""
    from tests.release.test_release_consistency import _latest_git_tag

    return _latest_git_tag()


def _branch_tip_ref() -> str:
    """Reuse the consistency test's branch-tip resolver.

    PR-build HEAD = synthetic merge commit; this resolves to
    ``origin/<branch>`` so the invariant compares against the actual
    branch tip in both ``pull_request`` and ``push`` events.
    """
    from tests.release.test_release_consistency import _branch_tip_ref as _resolve

    return _resolve()


def _run_git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 -- args are literal git invocations, not user-controlled
        ["git", *args],  # noqa: S607 -- literal "git"
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    ).stdout.strip()


class TestSkipOnPrEnforced:
    """Pin the ``@skip_on_pr`` decorator flips on PR builds but not push.

    The two real invariant tests below skip under this marker; these
    RED → GREEN assertions prove the marker obeys ``GITHUB_EVENT_NAME``.
    """

    def test_marker_skips_when_event_is_pull_request(self, monkeypatch: object) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")  # type: ignore[attr-defined]
        # Re-evaluate marker (env was read at import time).
        import importlib

        from tests.release import test_release_provenance as mod

        importlib.reload(mod)
        assert mod._IS_PR_BUILD is True

    def test_marker_runs_when_event_is_push(self, monkeypatch: object) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")  # type: ignore[attr-defined]
        import importlib

        from tests.release import test_release_provenance as mod

        importlib.reload(mod)
        assert mod._IS_PR_BUILD is False


@skip_on_pr
def test_latest_release_tag_is_ancestor_of_main() -> None:
    """Latest release tag must be reachable from ``main`` HEAD via parent chain.

    Equivalent: ``git merge-base --is-ancestor <tag> main`` exits 0.

    Skipped on PR builds: the tag lives on the PR head branch by
    construction; the invariant only fires correctly on push-to-main
    (where ``main`` has caught up to the release tag).
    """
    tag = _latest_release_tag()
    out = subprocess.run(  # noqa: S603 -- tag comes from our own _latest_git_tag()
        ["git", "merge-base", "--is-ancestor", tag, "main"],  # noqa: S607 -- literal "git"
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert out.returncode == 0, (
        f"{tag} is NOT an ancestor of main HEAD. "
        f"main HEAD may be stale — fast-forward it onto the release branch "
        f"or, if main is correct, re-point the tag with `git tag -f {tag} <correct-sha>`."
    )


@skip_on_pr
def test_main_has_no_commits_ahead_of_latest_release_tag() -> None:
    """``main`` must not have commits strictly ahead of the latest release tag.

    Distinct from :mod:`test_release_consistency` (which pins the current
    branch): this pins ``main`` itself, so a phase branch that drifts
    past the tag does not silently advance ``main``.

    Skipped on PR builds: see :func:`test_latest_release_tag_is_ancestor_of_main`.
    """
    tag = _latest_release_tag()
    out = _run_git("rev-list", f"main..{tag}", "--count")
    behind = int(out) if out.isdigit() else 0
    assert behind == 0, (
        f"main is {behind} commit(s) behind {tag}. Run: `git checkout main && git merge --ff-only {tag}` (or rebase phase branch onto main)."
    )


def test_release_branch_not_ahead_of_main_on_release_tag() -> None:
    """The release branch HEAD must equal the latest release tag (catches `-f` re-points that skip commits).

    Equivalent to: ``git rev-parse <branch-tip>`` matches
    ``git rev-parse <latest>``. Guards against ``git tag -f`` re-points
    that move the tag forward without the underlying code catching up.

    Compares against :func:`_branch_tip_ref` (resolves to
    ``origin/<branch>`` on PR + push) so the invariant holds in both CI
    contexts. CI run :gh-run:`29428237463` demonstrated the merge-commit
    drift when this compared against HEAD; routed through the helper.
    """
    tag = _latest_release_tag()
    tag_sha = _run_git("rev-parse", tag)
    tip_sha = _run_git("rev-parse", _branch_tip_ref())
    assert tag_sha == tip_sha, (
        f"branch tip={tip_sha[:12]} != {tag}={tag_sha[:12]}. "
        f"Either re-tag at branch tip (`git tag -f {tag} {_branch_tip_ref()}`) "
        f"or commit on top of the tag."
    )
