# A2.4 run state

**Now:** building

## History
- 2026-11-17 — started after A2.3 went green.
- 2026-11-17 — verifier pass 1: two must-fix (the spec said stage 1 replayed from cache, but the stages code version moved 3 to 4, so both stages were billed; ST2-02's proof skipped transport). Fixed: the spec and the ledger note now say both stages were billed and why (the record already showed it); a transport failure after backoff through `triage_alert` is proven to escalate and be counted. The contract test now checks dispositions against rows, the cost sum, the ledger line and both latency orders. Not changed, to keep `stages` at code_version 4 (any edit to the module moves its version and its cache keys): a misplaced comment on `SCHEMAS_HASH`.
- 2026-11-17 — verifier pass 2: one must-fix (the latency proof could not tell cached from live calls, since replays carry the same latency). Fixed: cached replays are given an extreme latency and must not move p50 or p95. Should-fix applied: the sample script's docstring and its ledger note now say which stage was billed, computed from the cache hits, and the committed ledger row uses that exact wording; the contract keys the stage-1 check on `decided_at_stage` and requires stage-2 errors to escalate.

## Open should-fix (carried forward)
- The sample records no attempts per row (one stage call was retried: 19 billed calls for 18 decisions).
- `error_counts` counts decisions, so one alert can count twice (a stage-1 and a stage-2 error); per-stage counts may suit E2.3.
- A2.5: `CapReached` or `ProviderConfigError` inside `triage_alert` leaves that alert without a disposition; the batch must stop cleanly and resume.
- Fix the misplaced comment on `SCHEMAS_HASH` the next time `stages` changes.
