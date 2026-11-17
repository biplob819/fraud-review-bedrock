# A2.4 acceptance criteria

**ST2-01 — stage 2 call**
- `run_stage(2, …)` sends Sonnet 5.5 at effort `high` with the stage-2 job and returns a `Stage2Output` (clear, hold or escalate) or an error kind.

**ST2-02 — failures escalate and are counted**
- An invalid reply is retried once and then escalates as `schema`; a refusal escalates as `refusal` with no retry; transport failure after backoff escalates as `transport`.
- `error_counts` returns the count for each type.

**ST2-03 — router hand-off**
- `triage_alert` returns exactly one disposition. An accepted stage-1 clear is `auto_cleared` at stage 1, with no stage-2 call.
- A stage-2 hold goes to the priority queue, and an escalation or any error to the standard queue.
- A stage-2 clear refused by the router (no clear clause holds, a no-clear clause holds, risk at or above the ceiling, or confidence below `clear_min_confidence`) becomes an escalation, with the refusal reasons on the disposition.

**ST2-04 — latency**
- p50 and p95 per stage over live calls are recorded in `stage2-sample.json`; cache hits do not count.
