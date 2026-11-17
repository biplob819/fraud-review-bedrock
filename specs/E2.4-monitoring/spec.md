# E2.4 — Monitoring and improvement

**Wave 3. Depends on:** E2.1, E2.2, E2.5, A2.5. **Unblocks:** E2.3 (summary and dashboard).

## Goal
Watch the agent over time and change it only on evidence (OWNER D-36): a history of every scored run, alarm bands that fail loudly on any leak, a drift check on new alert batches, and a logged improvement loop on the dev set. EvalKit's own monitoring ships in its v0.4.0, so the local versions here stay until then; adopting it is deferred out of v0.1.0.

## Outputs
- `src/fraudreview/monitor.py` and `fraud monitor --run --truth [--cases --reference]` (MON-01..04): one history line per scored run in `docs/measurements/runs.jsonl` (backfilled from the two eval runs); alarm bands in `config.yaml` where any leak always fails and exits 1, and a rate over no cases raises no alarm; a population-stability drift check per numeric feature; trend data for the dashboard.
- `fraud experiment --before --after --truth --hypothesis --change` (IMP-01, IMP-02): applies the rule, runs EvalKit-based paired bootstrap and Holm comparison (`compare.py`), and appends `docs/measurements/experiments.jsonl`, kept or not. Backfilled with the two real changes so far (the 0.045 risk ceiling and the night rule).
- `freeze_guard` (IMP-03): an eval run whose settings differ from the newest logged eval requires a fresh sample, comparing the settings both freeze records hold so a wider record alone does not force one.

## Rules
- Dev only: the improvement loop never reads eval labels.
- A change is kept only if dev leakage stays 0 or it fixes a leak; the owner's ruling can differ from the rule, and the ledger shows both.
