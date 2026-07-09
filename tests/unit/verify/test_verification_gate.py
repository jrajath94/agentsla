"""Unit tests for the VerificationGate bridge class.

Phase 3 deliverable: the gate is the typed boundary between
:class:`VerificationChain` (verifier logic) and :class:`TraceWriter`
(append-only event log). The contract under test:

1. ``run(trace_id, final_answer)`` calls ``chain.run(...)`` with the
   final-answer text and the trace id.
2. It then appends a :class:`Verdict` event to the writer with the
   correct ``verdict_id`` / ``trace_id`` / ``seq`` / ``verifier`` /
   ``verified`` / ``coverage`` fields.
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


def test_run_returns_gate_result_with_verdict_and_chain() -> None:
    """run() returns GateResult; both verdict + chain are populated."""
    with tempfile.TemporaryDirectory() as td:
        writer = _make_writer(Path(td))
        verifier = NumericVerifier(source_resolver=identity_source)
        chain = VerificationChain(verifiers=[verifier])
        gate = VerificationGate(chain, writer, verifier="composite")

        trace_id = uuid4()
        result = gate.run(trace_id, "the value is 42")

        assert isinstance(result, GateResult)
        assert result.chain.passed is True
        assert result.chain.coverage == 1.0
        assert result.verdict.verified is True
        assert result.verdict.coverage == 1.0
        assert result.verdict.trace_id == trace_id
        assert result.verdict.verifier == "composite"
        assert result.verdict.verdict_id is not None


def test_run_appends_verdict_to_writer() -> None:
    """The Verdict event is appended to the underlying writer."""
    with tempfile.TemporaryDirectory() as td:
        writer = _make_writer(Path(td))
        verifier = NumericVerifier(source_resolver=identity_source)
        chain = VerificationChain(verifiers=[verifier])
        gate = VerificationGate(chain, writer, verifier="composite")

        trace_id = uuid4()
        gate.run(trace_id, "the value is 7")

        # Verify the event is in the writer by reading the parquet export.
        out = Path(td) / "out.parquet"
        writer.export_parquet(out)
        import pyarrow.parquet as pq  # local import — optional in some envs

        table = pq.read_table(out)
        assert len(table) == 1
        row = table.to_pylist()[0]
        assert row["kind"] == "verdict"
        assert row["trace_id"] == str(trace_id)
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

        trace_id = uuid4()
        result = gate.run(trace_id, "the value is 99")

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

        trace_id = uuid4()
        result = gate.run(trace_id, "the value is 42")
        assert result.verdict.verified is False
        assert result.chain.passed is False
