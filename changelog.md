# Changelog

Format: `[TYPE] [NODE] — summary`. Types: FEAT, FIX, DOCS, HARNESS, RELEASE.

## 2026-11-17
- [FEAT] [E2.1] — metrics and intervals: fraud leakage with its exact Clopper–Pearson bound when none leak, end-to-end recall against the baseline as a paired difference, auto-clear rate and precision, stage shares, queue mix, bootstrap intervals for modelled minutes and cost per 1,000, nearest-rank latency over live calls, and threshold checks where leakage above zero exits 1
- [FEAT] [A2.1] — features, baseline, alerts and SHAP: point-in-time card features with a truncation-property leak test, a deterministic LightGBM baseline on the seven allow-listed fields, the D-9 threshold (98% validation recall, lowered if alerts would be mostly fraud), seeded dev (100) and eval (500) alert samples, and SHAP top-five drivers kept in separate truth files; `fraud baseline`

## 2026-11-16
- [FEAT] [N1.2] — data load, minimisation and splits: `fraud load` downloads the pinned Sparkov files (the Fraud Dataset Benchmark's `sparknov` source), drops personal and location columns, replaces card and transaction numbers with opaque ids, strips the `fraud_` merchant prefix after checking it is on every row, cuts time-ordered train, validation and test splits with content hashes checked on every read, and documents it all in `DATA_CARD.md`

## 2026-11-15
- [FEAT] [A2.2] — router, queues and audit log: router rules 1–6 with every refusing rule recorded, exactly one disposition per alert with a reconciliation check, priority and standard queues, a hash-chained append-only audit log with per-alert views, and a guard that refuses tool definitions in any model request
- [FEAT] [N1.4] — policy as code: `policy.md` v1 with signal, clear and no-clear clauses; predicates parsed on an `ast` allow-list and type-checked against the alert fields at load, never `eval`; `clearing_allowed` for the router with fail-safe handling of missing values
- [FEAT] [N1.1] — schemas and config: alert, decision, audit and summary records (results format v1, frozen, no NaN or infinity), `AlertTruth` kept apart from agent-visible data, `RunConfig` with the provider switch and a mandatory leakage threshold; fixtures round-trip byte for byte
- [HARNESS] [W0.1] — repo, uv project, CI, the build harness ported from EvalKit (eligibility, node start, node converge with 12 self-tested gates, status, secret scan) plus the new honesty gate; graph of 22 nodes and 108 capabilities; decisions D-1 to D-29; lessons seeded from EvalKit; proofs run under pytest; public-content guard over every tracked file
