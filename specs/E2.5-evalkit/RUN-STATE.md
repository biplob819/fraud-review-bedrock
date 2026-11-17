# E2.5 run state

**Now:** building (waiting on EvalKit v0.3.0)

## History
- 2026-11-17 — the bridge was built in a parallel lane at EvalKit development commit add5af9 and merged. On 2,400 seeded alerts rule_compliance agrees with the router on all 1,863 clears.  EvalKit monitoring moved to E2.4 (MON-05) because it ships in v0.4.0.
- 2026-11-17 — EvalKit's `value_check` left v0.3.0 (EvalKit D-74); this project's strict `valuecheck` (D-42) replaced it, with a seeded mutation test in which no false variant is verified. The pin moved to the v0.3.0 release candidate 118b661 (`evalkit.__version__` 0.3.0). On the fresh eval's 3,913 reasons: 28.1% verified (1,098), 0 contradicted, the rest unverifiable. Verifier: no must-fix; one real gap, a false merchant claim in wording the checker did not read ("merchant is brand new, amount 120") let a true number beside it verify the reason. Fixed: any merchant claim the checker cannot read, in any clause or sentence, makes the reason unverifiable; the cases are in the safety test. Waiting only on the EvalKit v0.3.0 tag, to move the pin from the release candidate to the tag.

## Open should-fix (carried forward)
- The model calls categories at 6-10% "rare"; P6.1 sets rare at under 5% (for the E2.4 improvement loop).
