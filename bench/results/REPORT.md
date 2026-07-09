# AgentSLA bench report

_Generated from `bench/results/results.parquet`._

## Headline: naked vs wrapped

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 86% | -14% |
| Gate passed | 0% | 100% | +100% |
| Verified at truth | n/a | n/a | — |
| Injection resistance | 0% | 100% | +100% |
| p95 latency (ms) | 6.67 | 6.94 | +0.26 (+4.0%) |
| Mean latency (ms) | 5.14 | 5.41 | +0.27 |
| N runs | 175 | 175 | — |

## Per-domain breakdown

| Domain | Mode | Success | Gate passed | Verified@truth | Inj resist | p95 (ms) |
|--------|------|--------:|------------:|---------------:|-----------:|---------:|
| financial_ops | naked | 100% | 0% | n/a | 0% | 6.83 |
| financial_ops | wrapped | 67% | 100% | n/a | 100% | 6.79 |
| incident_triage | naked | 100% | 0% | n/a | 100% | 5.68 |
| incident_triage | wrapped | 100% | 100% | n/a | 100% | 7.72 |
| doc_qa | naked | 100% | 0% | n/a | 100% | 6.73 |
| doc_qa | wrapped | 100% | 100% | n/a | 100% | 6.26 |

## Holdout subset (excluded from dev tuning)

| Mode | N | Success | Gate passed | Verified@truth | p95 (ms) |
|------|--:|--------:|------------:|---------------:|---------:|
| naked | 40 | 100% | 0% | n/a | 6.30 |
| wrapped | 40 | 88% | 100% | n/a | 6.79 |
