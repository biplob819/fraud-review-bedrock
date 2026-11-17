# N1.3 run state

**Now:** building

## History
- 2026-11-17 — started after W0.2 went green.

- 2026-11-17 — verifier pass 1: three must-fix (unresolvable credentials crashed instead of exit 2; spend could pass the cap because reservations were estimates; no CLI exit-code mapping), all fixed with five should-fix (guard on retries proven, whole-module code version, the live script reserves and writes its own ledger line, the dry-run test patches the real clients, all other 4xx are config errors).

## Open should-fix (carried forward)
- From N1.2: route every request through `guard_request` (done here by design).
