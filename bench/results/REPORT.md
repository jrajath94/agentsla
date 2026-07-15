# AgentSLA bench report

_Generated from `bench/results/results.parquet`._

> **Honest gap — `verified_at_truth` not measured.**
> The hermetic `EchoModel` self-certifies but does not declare
> task ground truths, so no run can be checked against truth.
> To populate this column, run:
> ```
> ANTHROPIC_API_KEY=sk-... \
>   python -m agentsla bench-real \
>     --model claude-haiku-4-5-20251001 \
>     --tasks-per-domain 5 \
>     --out bench/results/real_llm.parquet
> ```
> The harness path, tests, and CLI are real (see
> `agentsla/bench/real_llm.py` + `tests/unit/bench/test_real_llm.py`);
> only the live numbers are missing.

## Headline: naked vs wrapped

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 86% | -14% |
| Gate passed | 0% | 100% | +100% |
| Verified at truth | n/a | n/a | — |
| Injection resistance | 0% | 100% | +100% |
| p95 latency (ms) | 6.02 | 8.52 | +2.50 (+41.6%) |
| Mean latency (ms) | 5.19 | 7.12 | +1.93 |
| N runs | 70 | 70 | — |

## Per-domain breakdown

| Domain | Mode | Success | Gate passed | Verified@truth | Inj resist | p95 (ms) |
|--------|------|--------:|------------:|---------------:|-----------:|---------:|
| financial_ops | naked | 100% | 0% | n/a | 0% | 5.73 |
| financial_ops | wrapped | 67% | 100% | n/a | 100% | 7.79 |
| incident_triage | naked | 100% | 0% | n/a | 100% | 5.54 |
| incident_triage | wrapped | 100% | 100% | n/a | 100% | 9.76 |
| doc_qa | naked | 100% | 0% | n/a | 100% | 6.10 |
| doc_qa | wrapped | 100% | 100% | n/a | 100% | 8.10 |

## Holdout subset (excluded from dev tuning)

| Mode | N | Success | Gate passed | Verified@truth | p95 (ms) |
|------|--:|--------:|------------:|---------------:|---------:|
| naked | 16 | 100% | 0% | n/a | 5.73 |
| wrapped | 16 | 88% | 100% | n/a | 7.96 |

## Seeded-error experiment (verification gate validation)

_Generated from `bench/results/seeded_errors.parquet`. Synthetic numeric tasks with known ground truth; the agent emits a single perturbed number; the verifier compares the extracted claim against the ground-truth resolver. At 0% perturbation every claim should match (specificity); at >0% perturbation every claim should mismatch and the gate should flag `incorrect` (sensitivity)._

| Perturbation | N trials | Sensitivity (gate caught) | Specificity (clean pass) | Mean latency (ms) |
|-------------:|---------:|--------------------------:|-------------------------:|------------------:|
| ±0.0% | 100 | 100% | 100% | 3.48 |
| ±50.0% | 100 | 100% | 0% | 3.45 |

**Acceptance** (per `feedback.md` Item 3):
- sensitivity @ ±50% perturbation ≥ 85%
- specificity @ 0% perturbation ≥ 90%

## Cross-adapter parity (rawloop vs langgraph)

_Generated from `bench/results/parity.parquet`._

| Adapter | N | Successes | Mean events/run | Mean latency (ms) |
|---------|--:|----------:|----------------:|------------------:|
| rawloop | 30 | 30 | 4.00 | 7.71 |
| langgraph | 30 | 30 | 4.00 | 8.23 |

**Paired runs:** 30
**Success agreement:** 100%
**Event-count agreement:** 100%

Event-kind sequence equality is enforced by the unit suite (`tests/integration/test_cross_adapter_parity.py`); this section surfaces the parity evidence at the bench scale.

## Figures

### Cost Per Task

![Cost Per Task](figures/cost_per_task.png)

### Gate Passed

![Gate Passed](figures/gate_passed.png)

### Injection Resistance

![Injection Resistance](figures/injection_resistance.png)

### Latency Cdf

![Latency Cdf](figures/latency_cdf.png)

### Success Rate

![Success Rate](figures/success_rate.png)

## Classifier held-out evaluation

_Generated from `tests/fixtures/held_out_labels.jsonl` at 2026-07-15T11:40:19.324963+00:00._

**Headline agreement:** 100% (117/117)

> **Fixture provenance: synthetic (model=echo-1).** 117/117 rows are hermetic — generated from the synthetic held-out builders. The number above is real for the synthetic patterns but not for live traffic. To upgrade, run `python -m agentsla eval-classifier --build-fixture` with `ANTHROPIC_API_KEY` set and `--no-fallback` to enforce the real-API path.

| Gold category | N | Correct | Agreement |
|---------------|--:|--------:|----------:|
| hallucinated_fact | 13 | 13 | 100% |
| none | 13 | 13 | 100% |
| permission_denied | 13 | 13 | 100% |
| planning_error | 13 | 13 | 100% |
| policy_violation | 13 | 13 | 100% |
| reasoning_error | 13 | 13 | 100% |
| retry_loop | 13 | 13 | 100% |
| timeout | 13 | 13 | 100% |
| tool_call_error | 13 | 13 | 100% |

Confusion matrix (rows=gold, cols=predicted):

| gold \ pred | hallucinated_fact | none | permission_denied | planning_error | policy_violation | reasoning_error | retry_loop | timeout | tool_call_error |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hallucinated_fact | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| none | 0 | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| permission_denied | 0 | 0 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| planning_error | 0 | 0 | 0 | 13 | 0 | 0 | 0 | 0 | 0 |
| policy_violation | 0 | 0 | 0 | 0 | 13 | 0 | 0 | 0 | 0 |
| reasoning_error | 0 | 0 | 0 | 0 | 0 | 13 | 0 | 0 | 0 |
| retry_loop | 0 | 0 | 0 | 0 | 0 | 0 | 13 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 13 | 0 |
| tool_call_error | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 13 |

Held-out traces were generated by the synthetic builders (independent from the heuristics' training triggers); this evaluation is therefore honest (not circular). To upgrade with real Claude API labels, run `scripts/build_held_out_fixture.py` with `ANTHROPIC_API_KEY` set.


## Real-LLM bench

_Generated from `bench/results/real_llm.parquet`. Model: `MiniMax-M3`. This is the only path that produces measured `verified_at_truth` numbers — the hermetic EchoModel bench cannot._

| Mode | Success | Gate passed | Verified@truth | N rows | p95 (ms) |
|------|--------:|------------:|---------------:|-------:|---------:|
| naked | 92% | 0% | 92% | 12 | 2981 |
| wrapped | 92% | 100% | 92% | 12 | 2981 |