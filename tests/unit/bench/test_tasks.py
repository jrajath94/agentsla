"""Unit tests for bench/tasks.py — both the hermetic corpus and the
ground-truthable corpus used by real_llm.

RED: ground_truthable loader must exist and return tasks whose
``ground_truth`` is set to a substring the model reliably produces.
"""

from __future__ import annotations

from agentsla.bench.tasks import load_ground_truthable_tasks, load_tasks


class TestGroundTruthableCorpus:
    """The real_llm bench needs tasks where ``verified_at_truth`` is
    measurable — i.e. the task declares a ``ground_truth`` substring that
    a well-behaved model will reliably include in its answer.

    Hermetic tasks use ``expected_substring=\"<echo:\"`` and ``ground_truth=None``
    because they target the EchoModel (deterministic echoer), not a real LLM.
    """

    def test_loader_exists(self) -> None:
        assert callable(load_ground_truthable_tasks)

    def test_loader_returns_non_empty(self) -> None:
        tasks = load_ground_truthable_tasks()
        assert len(tasks) >= 3, "need at least 3 ground-truthable tasks per call"

    def test_every_task_has_ground_truth(self) -> None:
        tasks = load_ground_truthable_tasks()
        for t in tasks:
            assert t.ground_truth, f"task {t.task_id} missing ground_truth: {t}"
            assert len(t.ground_truth) >= 2, f"ground_truth too short to be a meaningful substring: {t.task_id}={t.ground_truth!r}"

    def test_every_task_has_meaningful_expected_substring(self) -> None:
        """``expected_substring`` must NOT be the hermetic ``<echo:`` marker —
        real models don't echo, so that marker would zero out success_rate
        for live benches."""
        tasks = load_ground_truthable_tasks()
        for t in tasks:
            assert t.expected_substring != "<echo:", (
                f"ground-truthable task {t.task_id} uses hermetic echo marker; real_llm bench would record 0% success"
            )

    def test_covers_three_domains(self) -> None:
        tasks = load_ground_truthable_tasks()
        domains = {t.domain for t in tasks}
        assert {"financial_ops", "incident_triage", "doc_qa"} <= domains, f"ground-truthable corpus must span all three bench domains; got {domains}"

    def test_no_injection_payload(self) -> None:
        """These tasks go to the live API; injection variants are handled
        separately by the hermetic bench. A injection payload mixed into
        the live API corpus would leak adversarial text into the model's
        context and skew gate metrics."""
        tasks = load_ground_truthable_tasks()
        for t in tasks:
            assert t.injection is None, f"unexpected injection payload on {t.task_id}: {t.injection!r}"


class TestHermeticCorpusUnchanged:
    """Adding the ground-truthable loader must NOT mutate the hermetic corpus
    (which feeds ``agentsla bench``). The hermetic count is locked at 30 base
    + 5 injection = 35 by the integration tests; changing it would break
    downstream numbers.
    """

    def test_hermetic_count_unchanged(self) -> None:
        all_tasks = load_tasks(include_injection=True)
        assert len(all_tasks) == 35, f"hermetic bench drift: {len(all_tasks)} tasks (expected 35)"

    def test_hermetic_base_count_unchanged(self) -> None:
        base_tasks = load_tasks(include_injection=False)
        assert len(base_tasks) == 30, f"hermetic base drift: {len(base_tasks)} tasks (expected 30)"
