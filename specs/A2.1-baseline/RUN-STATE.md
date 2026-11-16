# A2.1 run state

**Now:** green

## History
- 2026-11-17 — spec, criteria and tasks written. Tests first, then `features.py`, `stats.py`, `baseline.py` and `fraud baseline`. LightGBM needs `libomp` on macOS (Homebrew locally; a CI step added).
- 2026-11-17 — real run (simulated data), about 20 seconds; byte-identical model and samples on a rerun:
  - Threshold 0.003396. It was not lowered: validation recall 98.0%, precision 20.3%.
  - Test: recall 96.9% [96.1%, 97.5%] (2,078 of 2,145 frauds); precision 13.5% [13.0%, 14.1%]; alert rate 2.76%.
  - Alerts: 11,054 in validation (20.3% fraud) and 15,344 in test (13.5% fraud).
  - Dev sample: 100 alerts, 21 fraud. Eval sample: 500 alerts, 79 fraud. With 79 frauds and none leaked, the Clopper–Pearson upper bound on leakage is about 3.7%.
  - Owner sign-off on the threshold and alert mix is needed before A2.3 (Tech Spec §11.3).
- 2026-11-17 — verifier pass 1: no MUST-FIX. All five rows were confirmed:
  - features recomputed by hand for 60 cards on the real data;
  - truncation checked at four cut points;
  - the D-9 threshold checked by brute force on the real and 4,000 random inputs;
  - SHAP checked on all 600 rows;
  - every recorded number reproduced.
- 2026-11-17 — should-fixes applied:
  - a zero prior median gives null, and a non-probability score is refused;
  - `baseline.json` records the threshold before any lowering;
  - spec names now match the code;
  - the same-second history rule is stated;
  - the agent import ban is moved to A2.3.

  Green.

## Open should-fix (carried forward)
- A2.3 must add the safety test that agent code imports neither `fraudreview.truth` nor `fraudreview.baseline` (which imports truth).
- The threshold and alert mix depend on the seed through LightGBM's bin sampling (seed 7 gives threshold 0.003209 and 12,048 validation alerts), so the owner's sign-off holds for seed 20261115 only.
