# Press release and FAQ: fraud-review agent

*A simulated scenario. The bank in it does not exist, and every number comes from the simulated evaluation in `docs/measurements/summary-eval2.json` or is marked illustrative.*

## Press release

**A fraud-review agent that clears the easy alerts and, in the simulated test, closed no fraud on its own**

A card-fraud team spends most of its day on alerts that turn out to be legitimate. The fraud-review agent reads each alert first. It clears only what a written policy allows, and sends everything else to a person with its reasons and the evidence beside them.

In a simulated test on 500 fresh alerts, the agent auto-cleared 8.8% of them (44 of 500, simulated data). All 44 were legitimate. It auto-cleared none of the 55 frauds in the test (0 of 55, with an exact 95% upper bound of 5.3%, simulated data). The agent has no way to block a card or contact a customer. People decide, and every step is written to a tamper-evident log.

The design rule is simple: the model recommends, a rule written in code decides, and a clear needs a policy clause that holds.

The first test did not pass. It leaked 2 of 79 frauds (simulated data). That result stays in the record. One policy change, never auto-clear a purchase made from 10 p.m. to 6 a.m., was chosen on the 100-alert dev set, informed by that first run, and then checked on a fresh, separate set of 500 alerts.

The project is open source. It runs on simulated data, has a replay demo of the analyst screen, and publishes its costs, about $6.24 per 1,000 alerts in the simulated test (simulated data).

## Customer FAQ

**What does it do for an analyst?** It reads each alert and either clears it under the policy or queues it for a person, with the reasons, the policy clauses and the evidence side by side. Held alerts go to a priority queue, the rest to a standard queue.

**Can it close a fraud case?** No. It can auto-clear an alert only when a cited clearing clause holds, no never-clear clause holds, and the risk score and confidence are within the limits. In the simulated test, every fraud went to a person.

**How much work does it save?** In the simulated test it auto-cleared 8.8% of alerts. The ROI model turns that into hours with inputs you change. Its defaults are illustrative.

**Are the reasons trustworthy?** Every reason cites a real policy clause and an allowed field, and the checkable numbers in them were true in the simulated test (1,098 of 1,098). Most reasons contain claims the checker cannot verify, and the faithfulness check is a single-judge partial result that is not calibrated (simulated data).

**What does it cost?** About $6.24 per 1,000 alerts in model spend in the simulated test (simulated data).

## Internal FAQ

**What would model risk review ask?** Who approved the policy, how a clear is decided, what the audit log proves, and how the model is monitored. The answers are in the policy file, the router code, the hash-chained log and the monitoring history. A reviewer would still need to test the policy on the bank's own data.

**What is the leakage bar, and did it hold?** The bar is zero frauds auto-cleared. It held on the fresh test (0 of 55, simulated data), but 55 frauds is a small sample. The 95% upper bound is 5.3%, and the ROI model shows that a leakage rate that high would cost more than the agent saves (illustrative). A real sample would need to be much larger to show the bar holds.

**What would real data change?** Everything that is illustrative: alert volume, analyst time and cost, and the loss per missed fraud. The alert model, the policy and the thresholds would be retrained or retuned on the bank's labelled alerts, and the evaluation repeated on a held-out sample.

**What does it not do?** It does not block cards, contact customers, learn from analyst decisions, or use personal or location data. It is not validated for real use.

**What is the cost of the safety rule?** The late-night rule cut auto-clear from 36.8% to 8.8% (simulated data). That is the price of the zero-leakage bar on this data.
