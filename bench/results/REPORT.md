# AgentSLA bench report

_Generated from `bench/results/results.parquet`._

## Headline: naked vs wrapped

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 86% | -14% |
| Verified % | 0% | 100% | +100% |
| Injection resistance | 0% | 100% | +100% |
| p95 latency (ms) | 10.20 | 9.75 | -0.46 (-4.5%) |
| Mean latency (ms) | 7.05 | 7.77 | +0.73 |
| N runs | 175 | 175 | — |

## Per-domain breakdown

| Domain | Mode | Success | Verified | Inj resist | p95 (ms) |
|--------|------|--------:|---------:|-----------:|---------:|
| financial_ops | naked | 100% | 0% | 0% | 10.41 |
| financial_ops | wrapped | 67% | 100% | 100% | 9.06 |
| incident_triage | naked | 100% | 0% | 100% | 9.50 |
| incident_triage | wrapped | 100% | 100% | 100% | 9.95 |
| doc_qa | naked | 100% | 0% | 100% | 9.01 |
| doc_qa | wrapped | 100% | 100% | 100% | 9.51 |

## Holdout subset (excluded from dev tuning)

| Mode | N | Success | Verified | p95 (ms) |
|------|--:|--------:|---------:|---------:|
| naked | 40 | 100% | 0% | 9.10 |
| wrapped | 40 | 88% | 100% | 9.02 |
