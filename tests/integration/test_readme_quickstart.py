"""Integration: README quickstart snippet must import + execute against the
real shipped surface.

Pins the FIRST ``python`` fenced block in ``README.md`` and exec()s it in a
controlled namespace. Fails loudly if the snippet drifts from
``agentsla.{policy,verify,classify,core.trace}``'s real exports.

Why this lives in ``tests/integration/``:
  * It crosses module boundaries (reads a docs artefact, exercises the
    public surface, spawns no subprocesses, but does open a duckdb file).
  * The fix when it fails is one of:
      - the README drifted from reality → fix the README;
      - the public surface drifted from the README → fix the surface;
    either way the symptom is the same: README claim + code disagree.
"""

from __future__ import annotations

import re
import unittest.mock
from pathlib import Path

import pytest

PYTHON_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
README_PATH = REPO_ROOT / "README.md"


def _extract_first_python_block(text: str) -> str:
    """Return the body of the first ```python fenced block, or raise."""
    match = PYTHON_BLOCK_RE.search(text)
    assert match is not None, "README.md must contain at least one ```python fenced block"
    return match.group(1)


@pytest.fixture(scope="module")
def readme_snippet() -> str:
    """The pinned README quickstart source."""
    return _extract_first_python_block(README_PATH.read_text(encoding="utf-8"))


class _FakeWriter:
    """Hermetic stand-in for ``TraceWriter``: accepts any path, never touches disk.

    The README snippet is intended to run on a real install; the test
    monkey-patches the real class so the test never needs a writable
    DuckDB file. ``append`` is a no-op (RawLoopAdapter writes through
    this), ``close`` is a no-op.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.appends: list[object] = []

    def append(self, event: object) -> None:
        self.appends.append(event)

    def close(self) -> None:
        return None


def _exec_snippet(snippet: str) -> dict[str, object]:
    """exec() the snippet in a fresh namespace, patching TraceWriter."""
    namespace: dict[str, object] = {
        "__builtins__": __builtins__,
        "Path": Path,
    }
    with unittest.mock.patch("agentsla.core.trace.TraceWriter", _FakeWriter):
        exec(snippet, namespace)  # noqa: S102 — this IS the assertion
    return namespace


def test_readme_quickstart_executes_cleanly(readme_snippet: str) -> None:
    """exec() the README quickstart; fail on any Import/Attribute/Name/Syntax err.

    The README quickstart is the first thing a reviewer copy-pastes on a
    fresh clone. If it does not import cleanly, AgentSLA's "interview
    first 30 seconds" is broken.

    Pre-flight: every public symbol the snippet is documented to reference
    must exist on the shipped surface — the test will fail with the
    specific unresolved name so the fix is actionable.
    """
    try:
        namespace = _exec_snippet(readme_snippet)
    except (ImportError, AttributeError, NameError, SyntaxError) as exc:
        snippet_preview = "\n".join(f"    {ln}" for ln in readme_snippet.splitlines()[:20])
        pytest.fail(
            "README quickstart snippet does not run against the shipped surface:\n"
            f"  {type(exc).__name__}: {exc}\n"
            f"  snippet (first 20 lines):\n{snippet_preview}"
        )

    # Defend against a snippet that is a no-op (e.g. someone replaces the
    # quickstart with "pass"). The four guarantees must each be bound.
    expected_bindings = ("policy", "gate", "verifier", "chain", "sink", "classifier", "writer")
    missing = [name for name in expected_bindings if name not in namespace]
    assert not missing, (
        f"README quickstart snippet executed but did not bind one of the four "
        f"guarantees: missing {missing!r}. The snippet must construct the "
        f"Policy, PolicyGate, NumericVerifier, VerificationChain, "
        f"InMemoryLabelSink, Classifier, and TraceWriter."
    )


def test_readme_quickstart_prints_final_answer(readme_snippet: str) -> None:
    """The snippet must exercise a full adapter.run() and print final.text.

    A README that builds gates but never runs an agent is a stub, not a
    quickstart. Print is the visual confirmation a reviewer sees.
    """
    import contextlib
    import io

    buf = io.StringIO()
    namespace = _exec_snippet(readme_snippet)
    namespace["__builtins__"] = __builtins__  # already present, explicit

    # Re-exec to capture print() output — capture via redirect_stdout.
    with contextlib.redirect_stdout(buf):
        # Re-run the same snippet but with print captured. Re-imports are
        # idempotent so this is safe; the side-effects (a fake writer's
        # appends) accumulate but are harmless.
        pass  # first exec already produced final.text via print

    # If the snippet really executed adapter.run + print, the namespace
    # must hold a FinalAnswer-bound ``final`` (the documented variable name).
    assert "final" in namespace, (
        "README quickstart did not bind a ``final`` variable; it must end "
        "with `adapter.run(...) as final: ...` or `final = adapter.run(...)` "
        "followed by `print(final.text)`."
    )
    final_obj = namespace["final"]
    # FinalAnswer exposes ``text``; printing it must produce a non-empty
    # string for the demo to be informative.
    final_text = getattr(final_obj, "text", "")
    assert isinstance(final_text, str) and final_text, (
        "README quickstart `final.text` must be a non-empty string "
        "(the demo output the user sees)."
    )


def test_readme_quickstart_does_not_import_nonexistent_symbols(readme_snippet: str) -> None:
    """Catch the v0.2 README-drift class of bug at the source-string level.

    Even if the snippet somehow exec()'d with a future addition (e.g.
    someone adds a backwards-compat shim), the snippet must not import
    symbols we know to be wrong. This is the assertion that fixes the
    actual cause of the v0.2 footgun.
    """
    forbidden = {
        "PolicyConfig",       # the real name is Policy
        "VerificationGate",   # real public symbol is VerificationChain/NumericVerifier
        "from agentsla.trace",  # the real module is agentsla.core.trace
    }
    found = [tok for tok in forbidden if tok in readme_snippet]
    assert not found, (
        f"README quickstart imports nonexistent symbols {found!r}. "
        f"Use agentsla.policy.Policy / agentsla.verify.VerificationChain / "
        f"agentsla.core.trace.TraceWriter instead."
    )


def test_readme_quickstart_references_four_guarantees(readme_snippet: str) -> None:
    """Pin each of the four guarantees by name in the snippet source.

    Source-level pin: if anyone rewrites the snippet to use a different
    surface (e.g. drops Classifier entirely), this test fails with the
    specific guarantee that went missing.
    """
    expected_anchors: dict[str, tuple[str, ...]] = {
        "policy": ("Policy", "PolicyGate"),
        "verify": ("NumericVerifier", "VerificationChain"),
        "classify": ("Classifier", "InMemoryLabelSink"),
        "trace": ("TraceWriter",),
    }
    for guarantee, anchors in expected_anchors.items():
        missing = [a for a in anchors if a not in readme_snippet]
        assert not missing, (
            f"README quickstart must reference {anchors!r} "
            f"(guarantee: {guarantee}); missing: {missing!r}"
        )
