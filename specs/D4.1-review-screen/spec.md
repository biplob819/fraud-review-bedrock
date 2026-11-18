# D4.1 — Analyst review screen

**Wave 4. Depends on:** E2.3, A2.2.

## Goal
A replay demo of the analyst review screen: see an alert, its reasons and drivers, record a decision, and download the audit chain (FR12, US3, US8, D-15, D-41).

## Outputs
- `demo/` app and `demo/replay.jsonl` built by `fraud demo-cache`, which skips cases with guarded terms and counts them.
- `demo/requirements.txt` pinned to the lock and `demo/README.md`.
- v0.1.0 is replay-only; the hosted passkey demo moves to v0.2 (D-41).

## Rules
- No cloud client or secret in the demo package; no block button.
