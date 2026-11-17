# A2.5 run state

**Now:** building

## History
- 2026-11-17 — started after A2.4 went green.

## Open should-fix (carried forward)
- From A2.4: an exception inside `triage_alert` leaves no disposition; `run_triage` turns the cap into a clean stop, and a config error ends the run (exit 2) with the ledger line written.
