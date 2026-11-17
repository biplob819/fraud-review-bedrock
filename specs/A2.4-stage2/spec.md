# A2.4 — Stage 2 triage

**Wave 2. Depends on:** A2.3. **Unblocks:** A2.5 (batch run), E2.2 (explanation checks).

## Goal
Sonnet 5.5 at effort `high` reviews every alert stage 1 did not clear and recommends clear, hold or escalate with reasons and confidence. The router accepts a clear only under rules 1–6 (rule 6: confidence at least `clear_min_confidence`). A hold goes to the priority queue, and everything else to the standard queue. Failures escalate and are counted by type (FR6, FR7, D-5, D-16, D-20, NFR6).

## Outputs
- `stages.run_stage(2, …)` with `Stage2Output` and the stage-2 prompt job (built in A2.3 for both stages) (ST2-01).
- `stages.triage_alert(case, ctx)` runs stage 1, the hand-off, stage 2 when needed, and `router.dispose`, and returns the decisions and exactly one `Disposition` (ST2-03).
- `stages.error_counts(decisions)` counts `schema`, `refusal` and `transport` failures (ST2-02). The retry rules come from N1.3: an invalid reply is retried once, a refusal is never retried, and transport errors back off up to 6 times.
- `docs/measurements/stage2-sample.json`: a live run of `triage_alert` on the same 10 dev alerts as A2.3. Stage 1 replays from the cache, so only stage 2 is billed. It records dispositions, refusal reasons, error counts, and p50/p95 latency per stage over live calls from `metrics.latency` (ST2-04), with tokens and cost. Spend is under $0.15.

## Rules
- A stage-2 error, a refusal, a schema failure or a refused clear is never a clear.
- Latency counts only live calls; cache hits are excluded.
