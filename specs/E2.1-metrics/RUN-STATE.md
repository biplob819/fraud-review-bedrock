# E2.1 run state

**Now:** green

## History
- 2026-11-17 — spec, criteria and tasks written. Tests first on synthetic runs, then the `stats.py` additions and `metrics.py`. Runs on real decisions arrive with A2.5.
- 2026-11-17 — converge passed. Verifier pass 1 confirmed MET-01, 02, 03 and 05. MET-04 was disproved as wired: decisions no disposition named still counted toward latency, and could move `latency_p95_stage2_ms`. Fixed:
  - `outcomes` refuses decisions no disposition names, so cost and latency cover exactly the scored alerts;
  - it refuses dispositions the router could not reach from their replies;
  - a missing leakage value is refused when a leakage threshold is configured;
  - a test builds a full `RunSummary` from `compute`;
  - the bootstrap clamp is documented.
- 2026-11-17 — verifier pass 2 confirmed MET-02 to MET-05 across 300 router-produced runs. MET-01 was disproved for runs with no frauds: the baseline comparison reported a −1.0 recall difference. Fixed:
  - with no frauds both recalls are unavailable, the difference is 0, and a recall threshold is left unevaluated;
  - an escalation on a stage-2 hold is refused;
  - minutes per alert must be finite;
  - the wording of the stage-1 rule is now exact.
- 2026-11-17 — verifier pass 3: no MUST-FIX. All five rows were confirmed across 390 router-produced runs in five shapes, with every metric recomputed independently. Applied its cheap should-fixes:
  - `m` must be a real number up to 1,000,000;
  - a non-finite metric value is refused;
  - `compute` takes the decisions once.

  Green.

## Open should-fix (carried forward)
- E2.3 must print n beside every rate. With no frauds, leakage is 0 of 0 and passes, which is not evidence of safety.
- `outcomes` checks a reply's action, not whether the router would have accepted it (that needs the case and policy). A2.5 re-derives dispositions through `router.dispose` itself.
- None yet.
