# A2.3 run state

**Now:** building

## History
- 2026-11-17 — started after N1.3 went green; OWNER D-37 approved the alert set.
- 2026-11-17 — verifier pass 1: two must-fix (the cache key left out the rendered text, the reply-handling code version and the output limit, so a stale decision could be served; two proofs missed part of their claim), both fixed: the key now covers the rendered alert, alert id, provider, `max_output_tokens` and the `prompts` and `model_call` code versions, and a hit is checked against provider and model; tests prove each miss, a schema change and an error going to stage 2. Should-fix applied: the import ban walks the agent path's transitive closure, the real-sample render check is its own test (skipped where samples are absent), the prompt now states B3's conflicting-evidence rule and B5's comparison with usual behaviour, and the sample script logs a pre-run estimate. The live sample was rerun under the new prompt ($0.0041, 11 calls including one schema retry).

## Open should-fix (carried forward)
- From N1.3: register `stages` and `prompts` in `code_versions.json` (done here).
