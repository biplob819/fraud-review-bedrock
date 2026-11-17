# A2.5 run state

**Now:** building

## History
- 2026-11-17 — started after A2.4 went green.
- 2026-11-17 — dev run 1 (100): 2 of 21 frauds auto-cleared; OWNER D-39 lowered the risk ceiling to 0.045 (0 leaks on cached replies). Owner approved the eval run over $5 with a one-run $8 cap. Eval run 1 (500, settings 48bda41f): 2 of 79 leaked, $2.55; kept in the log. OWNER D-40 added P5.9 (never auto-clear at night); dev: 0 of 21, auto-clear 14 of 100; a fresh disjoint eval2 (500, 55 frauds, settings 7fec65d1): 0 leaked, upper bound 5.3%, 44 auto-cleared all correct, $3.12. Estimates now plan from a finished run (`--plan-from`). Golden fixture re-recorded under the final settings.

## Open should-fix (carried forward)
- E2.4: the night rule cut auto-clear from 36.8% to 8.8%; the improvement loop should look for safe clears it now blocks.
- From A2.4: an exception inside `triage_alert` leaves no disposition; `run_triage` turns the cap into a clean stop, and a config error ends the run (exit 2) with the ledger line written.
