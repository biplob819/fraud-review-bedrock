# E2.4 run state

**Now:** green

## History
- 2026-11-17 — monitor module built in a parallel lane and merged; `fraud monitor` and `fraud experiment` added. EvalKit monitoring (B5.7) ships in v0.4.0, so MON-05 was removed from v0.1.0. Found and fixed: a rate over no cases (no auto-clears) read as 0 and failed its band.
- 2026-11-17 — verifier pass 1: one must-fix, fixed. `fraud experiment` always passed a paired result, and the rule then reverted a neutral change (leakage 0 to 0, auto-clear within tolerance) that the criterion says to keep; the Holm gate now applies only to a claimed auto-clear rise, with a test. Also: a missing-label error from `fraud monitor` now exits 2, not 1, so scripts can tell it from a leak.
- v0.2 lane B: closed two should-fix items: `fraud monitor` no longer appends a run twice, and `fraud experiment` refuses non-dev runs and a labels file for another split.

## Open should-fix (carried forward)
- A bad drift input file raises after printing and skips the append.
- Trend mixes cost bases (billed in the backfill, decision cost from the CLI) and digest lengths (8 vs 16).
- `freeze_guard` returns none when the record holds only a digest.
- D-38 text still places monitoring in EvalKit v0.3.0; it ships in v0.4.0.
