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
