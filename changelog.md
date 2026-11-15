# Changelog

Format: `[TYPE] [NODE] — summary`. Types: FEAT, FIX, DOCS, HARNESS, RELEASE.

## 2026-11-15
- [FEAT] [N1.1] — schemas and config: alert, decision, audit and summary records (results format v1, frozen, no NaN or infinity), `AlertTruth` kept apart from agent-visible data, `RunConfig` with the provider switch and a mandatory leakage threshold; fixtures round-trip byte for byte
- [HARNESS] [W0.1] — repo, uv project, CI, the build harness ported from EvalKit (eligibility, node start, node converge with 12 self-tested gates, status, secret scan) plus the new honesty gate; graph of 22 nodes and 108 capabilities; decisions D-1 to D-29; lessons seeded from EvalKit; proofs run under pytest; public-content guard over every tracked file
