# N1.1 acceptance criteria

All models are frozen and strict: unknown fields are refused; sequences are tuples and mappings are read-only (they copy, deep-copy and pickle as read-only mappings; re-initialising one fails); floats reject NaN and infinity; booleans and numbers are not coerced from strings, and a stage is a strict int (not `True` or `1.0`), in Python or JSON; datetimes must carry a time zone and are normalised to UTC, and one that cannot be is a validation error. Every text field (names, ids, reasons, override comments, and the keys and values of text mappings) follows one allow-list rule: no control or format characters, and at least one letter or digit that is not a Hangul filler. Symbols and punctuation alone do not count, because some render as nothing. Records are read back from JSON with `model_validate_json`.

**SCH-01 — alert schemas**
- `Features`, `AlertCase`, `Driver` validate the §3 fields; `AlertCase`'s field set equals the allow-list; `alert_id` is `a-` plus 16 hex characters with no run of 12 or more digits, and `category` is one of the 14 Sparkov categories, so neither can carry a name, card number or merchant; `ts_hour` agrees with `features.hour`.
- `AlertTruth` and `Alert` live in `fraudreview.truth`, which `fraudreview.schemas` does not import; driver features are unique; `Alert` requires matching ids.

**SCH-02 — decision schemas**
- `Stage1Output` accepts only `clear|pass`, `Stage2Output` only `clear|escalate|hold`; both reject: no reasons, more than 5, a reason that breaks the text rule or is over 500 characters, no clause, a malformed clause id (fullmatch `P<digits>.<digits>`, ASCII digits, no trailing newline), duplicate clauses, no fields, duplicate or unknown fields, confidence outside [0, 1].
- `TriageDecision` carries every provenance field of §3 including `provider` (D-27) and an `Effort` level; text fields are not blank; it has either an output or an error, never both or neither; the output class follows `stage`, so every shape (stage-1 clear or pass, stage-2 clear, hold or escalate, error) reads back from Python and JSON unchanged.

**SCH-03 — audit schemas**
- `Disposition` requires `priority` for `hold`, `standard` for `escalate`, none for `auto_cleared`; stage 1 cannot hold; decision ids are unique and follow the text rule. An override comment follows the text rule.
- `AuditEvent` payloads are a discriminated union on `kind`; a payload whose type does not match its `kind` is refused; payload and event alert ids match; `hash` is 64 hex, `prev_hash` is empty exactly for `seq` 0 and otherwise 64 hex, and never equals `hash`. The hash covers the whole event without its `hash` field; A2.2 computes and verifies it.

**SCH-04 — summary schemas**
- `Rate` rejects k > n, lo > point, point > hi and a point that is not k/n; `Interval` rejects lo > point or point > hi; cost and minutes intervals cannot go below 0 and agreement or faithfulness intervals stay within [0, 1]; `LatencySummary` needs p50 ≤ p95.
- `RunSummary.data_label` is always `simulated`; counts agree (leaked plus caught frauds = `n_frauds`; alert rates over `n_alerts`; auto-clear precision over the auto-clears; auto-clears plus queued = `n_alerts`); auto-clear precision equals auto-clears minus leaked frauds; stage-1 clears do not exceed auto-clears, and stage-1 clears plus stage-2 arrivals do not exceed `n_alerts`; caught frauds do not exceed the alerts that reached a person; errors do not exceed two per alert; `prompt_hashes`, `models` and `latency_ms` are keyed by `stage1` or `stage2` only; threshold metrics come from a fixed list and appear once; there is exactly one leakage row, `leakage == 0`, and its result, once evaluated, matches the leakage count; every threshold is evaluated once the run has finished; `judge_mode` matches the number of faithfulness entries.

**SCH-05 — config**
- `load_config` loads the committed `config.yaml`. A missing, unknown or wrongly typed field, a string where a number belongs, a value JSON cannot express (a YAML date, binary, set, or a mapping that contains itself), a number in a YAML 1.1 form JSON lacks (`0x1F`, `1:30`, `010`, `20_261_115`), nesting too deep to read, a duplicate key, a bad effort level or threshold metric, a non-YAML file, a non-UTF-8 file or a non-mapping raises `ConfigError` naming the file and, where there is one, the field.
- Safety settings cannot be switched off from config: exactly one leakage row, `leakage == 0`; `clear_ceiling` at most 0.99; `clear_min_confidence` at least 0.5; `max_cost_usd` at most 100; `seed` within 0 to 2^32 − 1.

**SCH-06 — results format v1**
- Fixtures for every record kind (decisions of each shape, audit events of each kind, an alert view, a summary) round-trip byte for byte and match their builders; each carries `schema_version: "1.0"` where the record has one; models and their mappings are frozen and hashable; NaN and infinity are rejected.
