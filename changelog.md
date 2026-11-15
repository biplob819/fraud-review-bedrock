# Changelog

Format: `[TYPE] [NODE] — summary`. Types: FEAT, FIX, DOCS, HARNESS, RELEASE.

## 2026-11-15
- [FEAT] [A2.2] — router, queues and audit log: router rules 1–6 with every refusing rule recorded, exactly one disposition per alert with a reconciliation check, priority and standard queues, a hash-chained append-only audit log with per-alert views, and a guard that refuses tool definitions in any model request
- [FEAT] [N1.4] — policy as code: `policy.md` v1 with signal, clear and no-clear clauses; predicates parsed on an `ast` allow-list and type-checked against the alert fields at load, never `eval`; `clearing_allowed` for the router with fail-safe handling of missing values
- [FEAT] [N1.1] — schemas and config: alert, decision, audit and summary records (results format v1, frozen, no NaN or infinity), `AlertTruth` kept apart from agent-visible data, `RunConfig` with the provider switch and a mandatory leakage threshold; fixtures round-trip byte for byte
- [HARNESS] [W0.1] — repo, uv project, CI, the build harness ported from EvalKit (eligibility, node start, node converge with 12 self-tested gates, status, secret scan) plus the new honesty gate; graph of 22 nodes and 108 capabilities; decisions D-1 to D-29; lessons seeded from EvalKit; proofs run under pytest; public-content guard over every tracked file
