# N1.1 — Schemas and config

**Wave 1. Depends on:** W0.1. **Unblocks:** N1.2, N1.3, N1.4, A2.2 and through them everything else.

## Goal
One module, `fraudreview.schemas`, holds every typed record in Tech Spec §3, so data, agent, router, audit, evaluation and report all speak the same results format v1. `fraudreview.config` loads `RunConfig` from YAML with clear errors.

## Outputs
- `src/fraudreview/schemas.py`: `FeatureName`, `Features`, `AlertCase`, `Driver`, `AlertTruth` (SCH-01; `AlertTruth` and `Alert` live in `fraudreview.truth`, never imported by agent code), `Stage1Output`, `Stage2Output`, `Tokens`, `TriageDecision` (SCH-02), `Disposition`, `AnalystAction`, `AuditEvent` (SCH-03), `Rate`, `Interval`, `BaselineSummary`, `ExplanationSummary`, `ThresholdResult`, `RunSummary` (SCH-04).
- `src/fraudreview/config.py`: `StageConfig`, `RunConfig` (with `provider: bedrock | anthropic`, D-27), `load_config(path)` raising `ConfigError` that names the file and the field (SCH-05); a committed `config.yaml` with defaults (Haiku 5.5 effort medium, Sonnet 5.5 effort high, model ids marked "verify in W0.2").
- Results format v1 (SCH-06): every top-level file record carries `schema_version: "1.0"`; fixtures under `tests/fixtures/v1/` round-trip byte-identically through `model_dump_json`.

## Rules
- All models frozen; sequences are tuples (frozen is shallow on lists, EvalKit L-8); `extra="forbid"`.
- Floats reject NaN and infinity (EvalKit L-9). Rates, confidences and risk scores are within [0, 1]; costs, tokens, latencies and counts are non-negative.
- `Stage1Output`/`Stage2Output`: 1–5 non-empty reasons, at least one clause id matching `^P\d+\.\d+$`, at least one field, fields unique.
- `Rate` checks `0 ≤ k ≤ n`, `lo ≤ point ≤ hi`, and `point == k/n` (or 0 when n = 0).
- `RunSummary.data_label` can only be `"simulated"` (NFR5).
- `AuditEvent.payload` is a discriminated union keyed by `kind`; a payload whose type does not match `kind` is rejected.

## Out of scope
Computing any value (features, intervals, hashes): later nodes. The audit hash chain logic is A2.2; here only the record shape.
