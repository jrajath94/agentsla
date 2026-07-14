<!--
Per workspace CLAUDE.md PR Hygiene: every PR MUST fill in Problem | Approach |
Evidence | Tradeoffs | Out of scope. Section headers are required; the
"Out of scope" line must be honest (what you intentionally did NOT do).
-->

## Problem

<!-- What user-facing problem or internal gap does this PR close? Cite an
issue number (`Closes #N`), PRD/TRD section, or a one-sentence framing.
-->

## Approach

<!-- The design choice you made. Pull from PRD-v2 / TRD-v2 if a relevant
section exists; quote the file path + section header. -->

## Evidence

<!-- Tests run, numbers produced. Paste pytest/mypy/ruff output verbatim,
or a Markdown table from `bench/results/REPORT.md`. Every cell traceable
to a parquet in `bench/results/` or a CI run URL. -->

```
$ uv run ruff check .
$ uv run pytest -q
```

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| _example_ | 86% | 92% | +6% |

## Tradeoffs

<!-- What did you choose NOT to do, and why? This is the most important
section — reviewers learn more from honest rejection criteria than from
happy-path summaries. Reference at least one rejected alternative. -->

- **Rejected: <alternative A>** — <reason>.
- **Rejected: <alternative B>** — <reason>.

## Out of scope

<!-- What this PR deliberately does not address. List explicitly so
follow-up issues can pick it up. -->

- <item>
- <item>

## Checklist

- [ ] `ruff check` clean
- [ ] `ruff format --check` clean
- [ ] `mypy agentsla/core agentsla/policy agentsla/verify` clean
- [ ] `pytest --cov` at the module floor
- [ ] Conventional Commit subject(s)
- [ ] CHANGELOG.md updated if user-visible behavior changed
- [ ] No secrets, `.env`, or fabricated numbers