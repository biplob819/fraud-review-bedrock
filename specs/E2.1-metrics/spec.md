# E2.1 — Metrics and intervals

**Wave 3. Depends on:** A2.1, A2.2. **Unblocks:** E2.3 (summary and dashboard).

## Goal
Score a triage run the way a fraud lead would question it. The headline is fraud leakage with its exact upper bound, with end-to-end recall beside the baseline's. Next come what the agent saves (auto-clear rate and precision, stage shares, queue mix, modelled minutes) and what it costs (per 1,000 alerts, latency). Every rate has an interval, and the configured thresholds decide the exit code (D-10, D-19, E7, H7, NFR3, NFR6, US1, US2).

## Outputs
- `src/fraudreview/stats.py`:
  - `wilson(k, n)`;
  - `clopper_pearson_upper(k, n)`, the exact one-sided 95% upper bound;
  - `bootstrap_mean(values, seed)`, 10,000 seeded resamples with percentile 2.5/97.5;
  - `percentile(values, q)`, nearest rank.
- `src/fraudreview/metrics.py`:
  - `outcomes(dispositions, decisions, truths)` joins one disposition, its decisions and its label per alert, and refuses anything inconsistent. Every alert needs exactly one disposition (`router.reconcile`). Every decision a disposition names must exist and belong to that alert. Labels must cover exactly the disposed alerts.
  - Rates for MET-01 and MET-02:
    - `leakage` is frauds auto-cleared ÷ frauds. Its interval is Wilson, and with zero leaked the upper bound is replaced by the exact Clopper–Pearson one-sided 95% bound.
    - `recall_end_to_end` is frauds that reach a person ÷ frauds.
    - `baseline_comparison` gives the paired difference against the baseline on the same alerts. The baseline flagged every alert, so its recall there is 1, and the difference is −leakage.
    - Also `auto_clear_rate`, `auto_clear_precision`, `stage1_clear_rate`, `stage2_reach_rate` and `queue_mix`.
  - Modelled value and cost (MET-03):
    - `minutes_saved_per_1000(outs, minutes_per_alert, seed)`: per alert, `m` if auto-cleared, else 0, shown per 1,000 alerts with a bootstrap interval. `m` is an ROI input passed by the caller; E2.3 takes it from the ROI inputs.
    - `cost_per_1000_usd(outs, seed)`: the summed decision costs per alert, per 1,000, with a bootstrap interval.
  - `latency(decisions)` (MET-04): p50 and p95 per stage, nearest rank, over decisions that are not cache hits.
  - `evaluate_thresholds(values, thresholds)` and `exit_code(results)` (MET-05). Each configured threshold is compared with its metric (`==`, `>=`, `<=`), and a metric that has no value yet is left unevaluated (None). Any failure gives exit 1; leakage > 0 fails the default `leakage == 0`.
  - `compute(...)` gathers the run fields of `RunSummary` that E2.1 owns.

## Rules
- The functions are pure: no model calls, and seeded randomness only.
- A disposition's alert must be in the label set, and the reverse. No alert is counted twice.

## Out of scope
Explanation checks (E2.2), assembling and writing `summary.json` and the dashboard (E2.3), the ROI spreadsheet (B3.1).
