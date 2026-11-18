# E2.5 run state

**Now:** green

## History
- 2026-11-17 — the bridge was built in a parallel lane at EvalKit development commit add5af9 and merged. On 2,400 seeded alerts rule_compliance agrees with the router on all 1,863 clears.  EvalKit monitoring moved to E2.4 (MON-05) because it ships in v0.4.0.
- 2026-11-17 — EvalKit's `value_check` left v0.3.0 (EvalKit D-74); this project's strict `valuecheck` (D-42) replaced it, with a seeded mutation test in which no false variant is verified. The pin moved to the v0.3.0 release candidate 118b661 (`evalkit.__version__` 0.3.0). On the fresh eval's 3,913 reasons: 28.1% verified (1,098), 0 contradicted, the rest unverifiable. Verifier: no must-fix; one real gap, a false merchant claim in wording the checker did not read ("merchant is brand new, amount 120") let a true number beside it verify the reason. Fixed: any merchant claim the checker cannot read, in any clause or sentence, makes the reason unverifiable; the cases are in the safety test. Waiting only on the EvalKit v0.3.0 tag, to move the pin from the release candidate to the tag.
- 2026-11-17 — flipped green on the release-candidate pin: the criterion asks for an exact commit that reports v0.3.0, which the candidate does, and the integration, proofs and one verifier pass (with its gap fixed) are done. The move to the tag, once EvalKit publishes it, is a one-line change tracked below.
- 2026-11-17 — EvalKit published v0.3.0 (tag, commit 4ddfef5; PyPI evalkit-harness 0.3.0). The pin moved from the release candidate to the tag's commit; the three commits between them change only EvalKit's release record, docs, a CI budget and its canary workflow. Tests unchanged and passing.
- v0.2 lane A — EvalKit v0.3.0 to v0.4.0 (D-46). The pin is the PyPI release `evalkit-harness[anthropic,openai]==0.4.0` (tag v0.4.0, commit f9cbc8e) and `uv.lock` resolves it from PyPI; the doctor's `EVALKIT_VERSION` is 0.4.0 (the canary's known drift). The pin tests check the exact release. Public imports checked: `value_check`, `ValuePhrase`, `estimate_faithfulness_cost`, `BudgetExceeded`, `Store`, `Executor`, `provider_for`, `interval_from`. `valuecheck` (D-42) stays for prose. No model call was made.

## Open should-fix (carried forward)
- The model calls categories at 6-10% "rare"; P6.1 sets rare at under 5% (for the E2.4 improvement loop).
