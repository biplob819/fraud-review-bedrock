# E2.1 acceptance criteria

**MET-01 — leakage and end-to-end recall**
- `leakage` counts the frauds with an `auto_cleared` disposition out of all frauds in the scored alerts. `recall_end_to_end` counts the frauds that went to a queue (hold or escalate). The two counts add up to the number of frauds.
- With leakage above 0, the interval is Wilson 95%. With zero leaked, `lo` is 0 and `hi` is the exact Clopper–Pearson one-sided 95% upper bound, `1 − 0.05^(1/n)`. For 79 frauds that is about 3.72%.
- `clopper_pearson_upper(k, n)` matches the beta-quantile definition for every k, and the closed form at k = 0.
- `baseline_comparison` reports the baseline's recall on the same alerts, which is 1 by construction, the system's recall, and their paired difference (≤ 0, equal to −leakage). With no frauds both recalls are unavailable (None), the difference is 0, and a recall threshold is left unevaluated.

**MET-02 — auto-clears, stages and queues**
- `auto_clear_rate` is auto-cleared ÷ alerts.
- `auto_clear_precision` is legitimate auto-clears ÷ auto-clears. With no auto-clears it is n = 0, point 0, interval 0 to 1.
- `stage1_clear_rate` counts alerts cleared at stage 1. `stage2_reach_rate` counts alerts decided at stage 2. Only an accepted clear ends at stage 1, so the two add up to the alerts.
- `queue_mix` counts holds and escalations.
- `outcomes` refuses each of these:
  - an alert with no disposition, or with more than one;
  - a disposition for an alert without a label, or a label without a disposition;
  - a decision id that a disposition names but that does not exist;
  - a decision whose alert or stage does not match its disposition;
  - a decision that no disposition names (so cost and latency cover exactly the scored alerts);
  - a disposition the router could not reach from its replies: a stage-1 ending whose reply is not a clear, an escalation whose stage-2 reply is a hold, an auto-clear at stage 2 without a stage-2 clear, or a hold without a stage-2 hold.

**MET-03 — minutes and cost with bootstrap intervals**
- `minutes_saved_per_1000` has the point 1000 × m × auto_clear_rate. `cost_per_1000_usd` has the point 1000 × mean per-alert cost, where an alert's cost is the sum of its decisions' costs.
- Each interval comes from 10,000 seeded bootstrap resamples of alerts, at percentiles 2.5 and 97.5. The same seed gives the same interval, and lo ≤ point ≤ hi.
- Both intervals are non-negative and fit `NonNegInterval`.
- `m` is a required input, never a hidden default, and must be a finite, non-negative number (at most 1,000,000).

**MET-04 — latency**
- p50 and p95 per stage use nearest rank over that stage's decisions that are not cache hits, all of them named by a disposition. Cached replays never count as latency.
- A stage with no live decisions is left out. p50 ≤ p95, and the result fits `LatencySummary`.

**MET-05 — thresholds and exit code**
- Each configured threshold is evaluated against its metric's value with its operator. A threshold whose metric is not available yet gets `passed=None`, except leakage, whose missing value is refused.
- `exit_code` is 1 when any threshold fails, and 0 otherwise.
- Planted check: a fraud that is auto-cleared makes leakage > 0, so the default `leakage == 0` threshold fails and the exit code is 1.
- With no frauds auto-cleared, the leakage threshold passes.
