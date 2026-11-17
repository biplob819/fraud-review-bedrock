# A2.3 run state

**Now:** green

## History
- 2026-11-17 — started after N1.3 went green; OWNER D-37 approved the alert set.
- 2026-11-17 — verifier pass 1: two must-fix (the cache key left out the rendered text, the reply-handling code version and the output limit, so a stale decision could be served; two proofs missed part of their claim), both fixed: the key now covers the rendered alert, alert id, provider, `max_output_tokens` and the `prompts` and `model_call` code versions, and a hit is checked against provider and model; tests prove each miss, a schema change and an error going to stage 2. Should-fix applied: the import ban walks the agent path's transitive closure, the real-sample render check is its own test (skipped where samples are absent), the prompt now states B3's conflicting-evidence rule and B5's comparison with usual behaviour, and the sample script logs a pre-run estimate. The live sample was rerun under the new prompt ($0.0041, 11 calls including one schema retry).
- 2026-11-17 — verifier pass 2: no must-fix. Should-fix applied: the cache key also covers a hash of `schemas.py` (validators the JSON schema cannot express), tests prove misses on provider, prompt version and validator changes and that an entry for another alert is refused, and the sample script writes its ledger line after the rerun. Green.

## Open should-fix (carried forward)
- From N1.3: register `stages` and `prompts` in `code_versions.json` (done here).
- ST1-03's check of every real dev and eval case runs only where `results/` exists (local); CI checks synthetic cases.
- The sample's ledger estimate (§9 planning tokens) was about 24% under actual; A2.5 replaces planning tokens with dev averages.
- Some stage-1 clears cite never-auto-clear clauses whose predicates are false (accepted by rule 4); flag in E2.2.
