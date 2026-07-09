# AgentSLA bench report

_Generated from `bench/results/results.parquet`._

## Headline: naked vs wrapped

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 86% | -14% |
| Verified % | 0% | 100% | +100% |
| Injection resistance | 0% | 100% | +100% |
| p95 latency (ms) | 6.73 | 7.84 | +1.12 (+16.6%) |
| Mean latency (ms) | 5.50 | 6.08 | +0.58 |
| N runs | 175 | 175 | — |

## Per-domain breakdown

| Domain | Mode | Success | Verified | Inj resist | p95 (ms) |
|--------|------|--------:|---------:|-----------:|---------:|
| financial_ops | naked | 100% | 0% | 0% | 6.72 |
| financial_ops | wrapped | 67% | 100% | 100% | 8.99 |
| incident_triage | naked | 100% | 0% | 100% | 6.96 |
| incident_triage | wrapped | 100% | 100% | 100% | 6.76 |
| doc_qa | naked | 100% | 0% | 100% | 6.31 |
| doc_qa | wrapped | 100% | 100% | 100% | 7.27 |

## Holdout subset (excluded from dev tuning)

| Mode | N | Success | Verified | p95 (ms) |
|------|--:|--------:|---------:|---------:|
| naked | 40 | 100% | 0% | 6.73 |
| wrapped | 40 | 88% | 100% | 6.93 |

## Seeded-error experiment (verification gate validation)

_Generated from `bench/results/seeded_errors.parquet`. Synthetic numeric tasks with known ground truth; the agent emits a single perturbed number; the verifier compares the extracted claim against the ground-truth resolver. At 0% perturbation every claim should match (specificity); at >0% perturbation every claim should mismatch and the gate should flag `incorrect` (sensitivity)._

| Perturbation | N trials | Sensitivity (gate caught) | Specificity (clean pass) | Mean latency (ms) |
|-------------:|---------:|--------------------------:|-------------------------:|------------------:|
| ±0.0% | 2000 | 100% | 100% | 2.76 |
| ±10.0% | 2000 | 100% | 0% | 3.31 |
| ±50.0% | 2000 | 100% | 0% | 3.76 |
| ±100.0% | 2000 | 100% | 0% | 3.90 |

**Acceptance** (per `feedback.md` Item 3):
- sensitivity @ ±50% perturbation ≥ 85%
- specificity @ 0% perturbation ≥ 90%
