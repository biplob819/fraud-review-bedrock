# W0.2 run state

**Now:** building (converging)

## History
- 2026-11-17 — the AWS login works with profile `fraud`, project Region us-east-2. Bedrock reports `NOT_AUTHORIZED` for Haiku 5.5 and Sonnet 5.5 and the use-case form is not filled. The project is on the free plan, which refuses the form submission ("not authorized"), and the Claude quotas are 0. Rate card read (D-33 prices); EvalKit pinned at v0.2.0; `fraud doctor` built.

- 2026-11-17 — OWNER D-35: switched to the Claude API with the owner's key. `fraud doctor --smoke` is all ok, and `fraud doctor --record` wrote `docs/measurements/preflight.json`: valid structured replies, a cache read on the repeat for both models, 10,000 requests a minute, about half a cent.

## Open should-fix (carried forward)
- None yet.
