# E2.5 — EvalKit v0.3.0 capabilities

**Wave 3. Depends on:** E2.1. **Unblocks:** E2.2 (explanation checks), E2.4 (monitoring and improvement).

## Goal
Use EvalKit's project-driven capabilities instead of project-only code (OWNER D-38). The rule-compliance scorer and structured-evidence faithfulness with value checks are built in the EvalKit repo (its B5.13; public `faithfulness` in its B5.2). They are pinned here and wired in through one bridge module. Monitoring (EvalKit B5.7, v0.4.0) is picked up in E2.4 (MON-05).

## Outputs
- `pyproject.toml` pins EvalKit to an exact commit. Until v0.3.0 is tagged, that is the EvalKit development commit where B5.2 and B5.13 are green. The pin moves to the v0.3.0 tag before this node is green (EVK-04).
- `src/fraudreview/evalkit_bridge.py`:
  - `ruleset(policy)`: our clauses as an EvalKit `RuleSet` (clear→allow, no_clear→never, signal→signal).
  - `record(case)`: the flat agent-visible alert.
  - `compliance(decision, case, rules)`: `rule_compliance` with `cited_key="policy_clauses"` and `allow='action == "clear"'` (EVK-01).
  - `evidence(case)`: one sentence per glossary field, via `render_evidence`, for EvalKit `faithfulness` (EVK-02).
  - `value_shares`: verified, contradicted and unverifiable shares of reasons, from this project's strict `valuecheck` module (D-42), which replaces EvalKit's withdrawn `value_check` until its v0.4.0 (EVK-02).
- `scripts/evalkit_sample.py` scores a finished run folder offline.
- `fraud doctor` checks that the pinned EvalKit's public API (rule compliance, evidence rendering, faithfulness) imports.

## Rules
- EvalKit's `rule_compliance` must agree with our router's rules 2–4 on accept versus refuse, on at least 2,000 seeded alerts including night cases.
- A false claim must never come back `verified` (D-42): a seeded mutation test over thousands of false variants of true claims must find none, and any merchant claim the checker cannot read makes the reason unverifiable.
