# Security Policy

## Reporting a vulnerability

Email: **rajath@example.invalid** (placeholder — replace before public launch).

If the report is sensitive (key extraction, prompt-injection bypass,
policy-gate evasion), please **do not** open a public GitHub issue.
Use the email above and expect an acknowledgement within 72 hours.

For non-sensitive bug reports (CLI crash, missing error message), open a
GitHub issue using the `bug` template.

## Supported versions

| Version | Supported |
|---------|-----------|
| `v0.2.x` | ✅ active |
| `v0.1.x` | security fixes only |
| `< v0.1` | ❌ end of life |

## Threat-model scope

AgentSLA is a **control plane** for tool-calling LLM agents. The threat
model in `docs/TRD-v2.md § 5` enumerates the surfaces we defend:

| Surface | Defense |
|---|---|
| Egress of secrets/PII in tool args | `PolicyGate.on_tool_call` + default egress regex pack |
| Tool-output prompt injection | `Classifier` heuristics + judge fallback |
| Bypass via free-text response | `bench/real_llm.py` synthetic ToolCall wrapping |
| Policy bypass via arg mutation | `tool_rules[].json_schema` |
| Replay tamper | Append-only DuckDB event log |
| Prometheus LAN exposure | `--metrics-addr` default `127.0.0.1` |

## What we do NOT defend against

- **Internal-tool sub-calls.** If a single tool internally makes external
  requests (e.g. a calculator that fetches live FX rates), the gate
  cannot intercept those sub-calls. See `docs/TRD-v2.md § 2.1`.
- **Non-deterministic services under replay.** Replay re-uses recorded
  results; live API drift will diverge unless `mode=tolerant`.
- **Model version drift.** Replay fails if `model_id` differs, unless
  explicitly tolerated.

## Egress regex pack defaults

The shipped pack covers PAN (with Luhn), SSN, AWS access keys, and
JWTs. Adding new rules: see `examples/policy.yaml`. Tenant-specific
rules can be appended at the end of `egress_rules` without touching the
default pack.