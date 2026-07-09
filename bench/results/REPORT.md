# AgentSLA bench report

_Generated from `bench/results/results.parquet`._

## Headline: naked vs wrapped

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 100% | +0% |
| Verified % | 0% | 100% | +100% |
| Injection resistance | 0% | 0% | +0% |
| p95 latency (ms) | 6.21 | 5.35 | -0.85 (-13.8%) |
| Mean latency (ms) | 5.07 | 4.81 | -0.26 |
| N runs | 175 | 175 | — |

## Per-domain breakdown

| Domain | Mode | Success | Verified | Inj resist | p95 (ms) |
|--------|------|--------:|---------:|-----------:|---------:|
| financial_ops | naked | 100% | 0% | 0% | 6.83 |
| financial_ops | wrapped | 100% | 100% | 0% | 5.15 |
| incident_triage | naked | 100% | 0% | 100% | 5.91 |
| incident_triage | wrapped | 100% | 100% | 100% | 5.37 |
| doc_qa | naked | 100% | 0% | 100% | 5.48 |
| doc_qa | wrapped | 100% | 100% | 100% | 5.36 |

## Holdout subset (excluded from dev tuning)

| Mode | N | Success | Verified | p95 (ms) |
|------|--:|--------:|---------:|---------:|
| naked | 40 | 100% | 0% | 5.60 |
| wrapped | 40 | 100% | 100% | 5.40 |
