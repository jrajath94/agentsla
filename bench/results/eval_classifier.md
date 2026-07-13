## Classifier held-out evaluation

_Generated from `tests/fixtures/held_out_labels.jsonl` at 2026-07-10T01:03:12.385566+00:00._

**Headline agreement:** 100% (36/36)

| Gold category | N | Correct | Agreement |
|---------------|--:|--------:|----------:|
| hallucinated_fact | 4 | 4 | 100% |
| none | 4 | 4 | 100% |
| permission_denied | 4 | 4 | 100% |
| planning_error | 4 | 4 | 100% |
| policy_violation | 4 | 4 | 100% |
| reasoning_error | 4 | 4 | 100% |
| retry_loop | 4 | 4 | 100% |
| timeout | 4 | 4 | 100% |
| tool_call_error | 4 | 4 | 100% |

Confusion matrix (rows=gold, cols=predicted):

| gold \ pred | hallucinated_fact | none | permission_denied | planning_error | policy_violation | reasoning_error | retry_loop | timeout | tool_call_error |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hallucinated_fact | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| none | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| permission_denied | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| planning_error | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 |
| policy_violation | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| reasoning_error | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 |
| retry_loop | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 |
| tool_call_error | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 |

Held-out traces were generated independently from the heuristics' training triggers; this evaluation is therefore honest (not circular). Replace the fixture at `tests/fixtures/held_out_labels.jsonl` with human-labeled traces to upgrade the headline number.
