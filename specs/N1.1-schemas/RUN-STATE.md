# N1.1 run state

**Now:** green

## History
- 2026-11-15 — spec and criteria written.
- 2026-11-15 — tests first, schemas, config and v1 fixtures built; converge next.
- 2026-11-15 — converge passed twice; verifier pass 1 disproved SCH-02 (a stage-2 clear could not be read back: the smart union picked Stage1Output; clause ids accepted a trailing newline and Unicode digits), SCH-05 (non-UTF-8 file crashed) and SCH-06 (dict fields mutable). Rewritten: strict models, stage-driven output parsing, fullmatch clause ids, read-only mappings, aware datetimes, cross-field summary rules, config that cannot disable the leakage bar, router limits or spend cap; fixtures for every record kind (L-27).
- 2026-11-15 — verifier pass 2 confirmed the pass-1 fixes; disproved SCH-01 (a-<card number> ids, free-text categories), SCH-02/03 (zero-width text counted as visible), SCH-05 (YAML dates named no field), SCH-06 (missing shapes, re-init mutation, copy and pickle broken). Fixed: 16-hex ids with no long digit runs, the 14 Sparkov categories, visible-text rule, strict int stages, UTC timestamps, FrozenDict re-init guard plus copy and pickle, summary safety consistency, fixed threshold metric names, config floors and non-JSON value paths; Tech Spec §3 synced.
- 2026-11-15 — verifier pass 3 confirmed SCH-01, SCH-04 and SCH-06; disproved SCH-02/03 (blank Hangul fillers and U+1D159 passed as text; override comments and mapping keys had no text rule) and SCH-05 (a mapping cycle named no field). Fixed with one allow-list text rule for every text field, cycle paths, JSON-style numbers, a depth guard, timestamp overflow as a validation error, impossible-count rules and a sealed mapping attribute; the audit hash coverage and alert-id generator rule written into the Tech Spec. Test strings use escapes, never literal invisible characters (L-28).
- 2026-11-15 — verifier pass 4: no MUST-FIX; all six rows confirmed. Fixed now: Tech Spec §3 drift (payload `kind` fields, `ThresholdMetric`, `passed = None`, `LatencySummary`), the truth-import test runs in a subprocess, the fixture test asserts `schema_version` directly. Green.

## Open should-fix (carried forward)
- Config: deep nesting (about 200 to 330 levels) reports a JSON recursion error at `(root)`; cap depth before `json.dumps` and name the top-level key.
- Config: explicit `!!int` / `!!float` tags bypass the JSON-number rule (`!!int 0x1F`); refuse explicit scalar tags.
- Config: YAML merge keys (`<<: *a`) are misreported as an unknown tag; refuse aliases and merges with a plain message.
- Config: alias fan-out is exponential in `_first_non_json`; refusing aliases closes this too.
- Text rule: invisible content may ride along with a visible letter (variation selectors, Zl/Zp, Co, Cn, default-ignorables); reject those categories.
- `Rate.point` uses an absolute 1e-12 tolerance; use ulp-level equality or reword the criteria.
- `FrozenDict`: `__dict__['_sealed']` tampering re-opens `__init__`; use `__slots__`.
- Duplicate-key errors name key and line but not the dotted path.
- Config: an explicit YAML tag that cannot be built (`!!timestamp x`, `!!float x`) raises ValueError/AttributeError instead of ConfigError (found by the N1.4 verifier; policy.py now catches these).
