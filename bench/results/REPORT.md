# AgentSLA bench report

_Generated from `bench/results/results.parquet`._

## Headline: naked vs wrapped

| Metric | Naked | Wrapped | Delta |
|--------|------:|--------:|------:|
| Success rate | 100% | 86% | -14% |
| Gate passed | 0% | 100% | +100% |
| Verified at truth | n/a | n/a | — |
| Injection resistance | 0% | 100% | +100% |
| p95 latency (ms) | 6.81 | 10.17 | +3.36 (+49.3%) |
| Mean latency (ms) | 5.86 | 8.50 | +2.64 |
| N runs | 70 | 70 | — |

## Per-domain breakdown

| Domain | Mode | Success | Gate passed | Verified@truth | Inj resist | p95 (ms) |
|--------|------|--------:|------------:|---------------:|-----------:|---------:|
| financial_ops | naked | 100% | 0% | n/a | 0% | 6.37 |
| financial_ops | wrapped | 67% | 100% | n/a | 100% | 10.17 |
| incident_triage | naked | 100% | 0% | n/a | 100% | 6.81 |
| incident_triage | wrapped | 100% | 100% | n/a | 100% | 11.11 |
| doc_qa | naked | 100% | 0% | n/a | 100% | 6.61 |
| doc_qa | wrapped | 100% | 100% | n/a | 100% | 8.76 |

## Holdout subset (excluded from dev tuning)

| Mode | N | Success | Gate passed | Verified@truth | p95 (ms) |
|------|--:|--------:|------------:|---------------:|---------:|
| naked | 16 | 100% | 0% | n/a | 6.00 |
| wrapped | 16 | 88% | 100% | n/a | 8.69 |
