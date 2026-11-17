# E2.2 — Explanation checks

**Wave 3. Depends on:** A2.4, A2.1, N1.4, E2.5. **Unblocks:** E2.3 (summary and dashboard) and E2.4.

## Goal
Check that the agent's reasons are valid, true to the alert, free of protected attributes, aligned with the baseline's drivers and grounded in the evidence, and pick the examples that most need a person's eye (US4, B1, B2, B7, B8, C3, D-11, D-36, D-38, D-42, D-43).

## Outputs
- `src/fraudreview/explain.py` (EXP-01, 03, 04, 06): validity rates, protected-attribute scan, driver agreement, and a fixed mismatch-pick rule. Clears that cite a no-clear clause whose condition is false are counted as suspicious, not failures.
- `src/fraudreview/valuecheck.py` (EXP-02, D-42): numbers and comparisons in reasons as verified, contradicted or unverifiable. A false claim is never verified.
- `src/fraudreview/judging.py` and `fraud judge` (EXP-05): EvalKit two-judge faithfulness with a dry-run estimate, a spend cap and a ledger line. The 465-explanation run was approved up to $15 and spent about $28.79: $28.59 on the Opus judge and $0.20 on claim extraction (OWNER D-43); `scripts/judge_cache_summary.py` summarises the cached verdicts with no model calls into `docs/measurements/faithfulness-cache-summary.json`: an Opus-only partial (75% of claims judged), with the measurement's two known defects recorded.
- `src/fraudreview/scorers.py` (EXP-07): our checks registered as EvalKit scorers through the `evalkit.scorers` entry-point group.
- `src/fraudreview/probe.py` (EXP-08): the prompt-injection probe. The only string reaching a prompt is the closed `category`, so there is no free-text injection surface today; a test fails if one is ever added without the probe covering it.
- `src/fraudreview/compare.py` (EXP-10): paired bootstrap and Holm comparison of two runs on the same alerts; a gap is claimed only when it survives both, and any rise in leakage is "worse".
- EXP-09 (judge calibration on the owner's labels) moves out of v0.1.0 (D-43); the report marks the judge as not calibrated.

## Rules
- Nothing here calls a model except `fraud judge`, and it refuses to run over its cap.
- Every published figure carries the simulated label and is an aggregate.
