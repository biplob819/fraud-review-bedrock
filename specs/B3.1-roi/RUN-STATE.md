# B3.1 run state

**Now:** green

## History
- 2026-11-18 — built in a parallel lane that stopped at a usage limit with its files uncommitted; merged, one test-evaluator bug fixed (an IF branch that is not taken must not run).

## Open should-fix (carried forward)
- At the 5.3% leakage upper bound the default model shows no payback; the page says the 0-of-55 result is too small to rule that out.
- 2026-11-18 — verifier pass 1: no must-fix. Worked example recomputed by hand and a mutation was caught. Should-fix applied where it concerned the page (PAT-04 now names the judging function that counts the ledger and a test that settles on every path).
