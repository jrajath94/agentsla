"""Unit tests for the VerificationGate bridge class.

Phase 3 deliverable: the gate is the typed boundary between
:class:`VerificationChain` (verifier logic) and :class:`TraceWriter`
(append-only event log). The contract under test:

1. ``run(trace, final_answer)`` matches :meth:`VerificationChain.run` —
   ``trace`` is the full trace object so verifiers can inspect events.
2. The gate extracts ``trace_id`` from ``trace.trace_id`` and appends a
   :class:`Verdict` event with the correct ``verdict_id`` / ``trace_id`` /
   ``seq`` / ``verifier`` / ``verified`` / ``coverage`` fields.
3. The returned :class:`GateResult` carries both the verdict and the
   underlying chain result.
4. Per-claim verdicts are emitted only for numeric expected/observed
   pairs (Phase 3 keeps the event shape numeric-only).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from uuid import uuid4

from agentsla.core.events import Trace
from agentsla.core.trace import TraceWriter
from agentsla.verify import (
    NumericVerifier,
    VerificationChain,
    identity_source,
)
from agentsla.verify.gate import GateResult, VerificationGate


def _make_writer(tmp: Path) -> TraceWriter:
    db_path = tmp / "trace.duckdb"
    return TraceWriter(db_path=db_path)


def _make_trace() -> Trace:
    """Build a minimal Trace carrying only the fields the gate reads."""
    return Trace(
        trace_id=uuid4(),
        task_id="t",
        model_id="m",
        events=[],
        final_answer="",
    )


def test_run_returns_gate_result_with_verdict_and_chain() -> None:
    """run() returns GateResult; both verdict + chain are populated."""
    with tempfile.TemporaryDirectory() as td:
        writer = _make_writer(Path(td))
        verifier = NumericVerifier(source_resolver=identity_source)
        chain = VerificationChain(verifiers=[verifier])
        gate = VerificationGate(chain, writer, verifier="composite")

        trace = _make_trace()
        result = gate.run(trace, "the value is 42")

        assert isinstance(result, GateResult)
        assert result.chain.passed is True
        assert result.chain.coverage == 1.0
        assert result.verdict.verified is True
        assert result.verdict.coverage == 1.0
        assert result.verdict.trace_id == trace.trace_id
        assert result.verdict.verifier == "composite"
        assert result.verdict.verdict_id is not None


def test_run_appends_verdict_to_writer() -> None:
    """The Verdict event is appended to the underlying writer."""
    with tempfile.TemporaryDirectory() as td:
        writer = _make_writer(Path(td))
        verifier = NumericVerifier(source_resolver=identity_source)
        chain = VerificationChain(verifiers=[verifier])
        gate = VerificationGate(chain, writer, verifier="composite")

        trace = _make_trace()
        gate.run(trace, "the value is 7")

        # Verify the event is in the writer by reading the parquet export.
        out = Path(td) / "out.parquet"
        writer.export_parquet(out)
        import pyarrow.parquet as pq  # local import — optional in some envs

        table = pq.read_table(out)
        assert len(table) == 1
        row = table.to_pylist()[0]
        assert row["kind"] == "verdict"
        assert row["trace_id"] == str(trace.trace_id)
        payload = json.loads(row["payload"])
        assert payload["verified"] is True
        assert payload["coverage"] == 1.0
        assert payload["verifier"] == "composite"


def test_per_claim_verdict_only_for_numeric() -> None:
    """Per-claim entries are emitted only for numeric expected/observed pairs."""
    with tempfile.TemporaryDirectory() as td:
        writer = _make_writer(Path(td))
        verifier = NumericVerifier(source_resolver=identity_source)
        chain = VerificationChain(verifiers=[verifier])
        gate = VerificationGate(chain, writer, verifier="composite")

        trace = _make_trace()
        result = gate.run(trace, "the value is 99")

        # identity_source returns the number, expected/observed are numeric.
        for cv in result.verdict.per_claim:
            assert cv.expected is None or cv.expected.replace(".", "").replace("-", "").isdigit()


def test_run_with_incorrect_claim_marks_verdict_failed() -> None:
    """When the chain reports incorrect claims, verdict.verified is False."""

    def wrong_resolver(_claim, _trace):  # always return 0 → mismatch for any value
        return 0

    with tempfile.TemporaryDirectory() as td:
        writer = _make_writer(Path(td))
        verifier = NumericVerifier(source_resolver=wrong_resolver, tolerance=0.0)
        chain = VerificationChain(verifiers=[verifier])
        gate = VerificationGate(chain, writer, verifier="composite")

        trace = _make_trace()
        result = gate.run(trace, "the value is 42")
        assert result.verdict.verified is False
        assert result.chain.passed is False


def test_run_signature_matches_verification_chain() -> None:
    """Gate.run and Chain.run share the same (trace, final_answer) signature.

    This is the staff-move invariant: the gate is a typed bridge, not a
    parallel interface. A future refactor that drifts these signatures
    must break this test loud.
    """
    import inspect

    gate_params = list(inspect.signature(VerificationGate.run).parameters)
    chain_params = list(inspect.signature(VerificationChain.run).parameters)
    # Drop ``self`` from both; the rest must match positionally.
    assert gate_params[1:] == chain_params[1:]
