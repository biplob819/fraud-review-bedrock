# D4.3 run state

**Now:** green

## History
- 2026-11-18 — built in a parallel lane and merged. The case study was brought up to date with the final evaluation.

## Open should-fix (carried forward)
- `pyproject.toml` is at 0.0.1 and `changelog.md` has no v0.1.0 heading; both change at tagging, which waits for the owner.
- Bedrock and live-credential paths in the quickstart are untested here.
- 2026-11-18 — verifier pass 1: no must-fix. Applied: the docs say the wheel and source archive are not a standalone install; the smoke job runs the offline doctor; the release workflow refuses a tag that is not on main. Left for later: ship the defaults inside the package (v0.2); the source archive omits config, policy and tests.
