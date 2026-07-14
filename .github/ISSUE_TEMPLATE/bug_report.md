---
name: Bug report
about: Reproducible defect in AgentSLA runtime, CLI, or bench harness
title: "[bug] "
labels: ["bug"]
assignees: []
---

## Summary

<!-- One-sentence statement of the defect. -->

## Reproduction

```python
# Minimal code or command that triggers the bug.
```

## Expected

<!-- What you expected to happen. -->

## Actual

<!-- What actually happened. Include the full traceback if any. -->

## Environment

- AgentSLA version: `python -c "import agentsla; print(agentsla.__version__)"`
- Python: `python --version`
- Adapter: `rawloop` / `langgraph` / `claude_sdk` / `n/a`
- OS: `uname -a`
- LLM (if real_llm bench): model id

## Logs

```
<paste tail of `agentsla <subcommand>` output>
```

## Severity

<!-- p0 = correctness / data loss / security; p1 = usability; p2 = nice-to-have -->