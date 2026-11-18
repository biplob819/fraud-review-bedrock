# D4.1 run state

**Now:** green

## History
- 2026-11-18 — finished in a parallel lane that stopped at a usage limit after committing; merged.
- 2026-11-18 — the owner moved the hosted deploy (DEM-05) to v0.2 (D-44); v0.1.0 ships the replay demo as a local app.

## Open should-fix (carried forward)
- The own-key path has no drawn arrow in the architecture diagram.
- 2026-11-18 — verifier pass 1: one must-fix, fixed. The cloud-client check never read demo/app.py, the real entrypoint, so a cloud import there would have passed; it now walks the entrypoint too, proven by adding a cloud import to a copy. Also: the README says the deploy section is planned for v0.2 and that decisions in the demo are not real, and `fraud demo-cache` warns when the private guard file is missing.
