# E2.2 run state

**Now:** building

## History
- 2026-11-17 — built in parallel lanes after E2.5. Judging overspent its approval (about $28.79 against $15; OWNER D-43); the result is an Opus-only partial summarised from cache. EXP-09 moved out of v0.1.0.
- 2026-11-17 — verifier pass 1: two must-fix, both fixed. (1) The judging cap restarted on every invocation, so a rerun could overspend again (the cause of D-43): `fraud judge` now sums the ledger for the run id and treats the cap as a total, refuses when it is reached, and no longer says to rerun; a test covers it. (2) The committed summary did not say what it is: it now carries `judge_mode` single, a partial flag and notes on the two measurement defects and on calibration, and a contract test checks them. Should-fix applied: the summary separates the 465 extracted explanations from the 458 with claims, the cost check compares the judge plus extractor within 5 cents, the D-43 text says where the $15 came from and splits Opus from extraction cost, the protected scan no longer flags card-age or state-of-the-art wording and now catches more demographic terms, and the comparison says "safer, less automation" whenever a claimed leakage fall comes with a claimed auto-clear fall.

## Open should-fix (carried forward)
- Judging results name decision ids that embed alert ids in stdout and `results/` (not committed); hash them before sharing any output.
- Evidence sentences round numbers (0.02 for 0.0167) and carry no policy text, which pushes faithfulness down; fix before any future judging run.
- The model calls categories at 5-10% "rare"; P6.1 defines rare as under 5% (555 of 739 rarity claims).
