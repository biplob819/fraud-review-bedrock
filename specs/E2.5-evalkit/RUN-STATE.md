# E2.5 run state

**Now:** building (waiting on EvalKit v0.3.0)

## History
- 2026-11-17 — the bridge was built in a parallel lane at EvalKit development commit add5af9 and merged. On 2,400 seeded alerts rule_compliance agrees with the router on all 1,863 clears. On eval2: compliance passes 46 of 46 clears and 918 of 918 non-clears; values_true was 65.1%, mostly false negatives in value_check, sent to EvalKit and accepted for v0.3.0. EvalKit monitoring moved to E2.4 (MON-05) because it ships in v0.4.0.

## Open should-fix (carried forward)
- The model calls categories at 6-10% "rare"; P6.1 sets rare at under 5% (for the E2.4 improvement loop).
