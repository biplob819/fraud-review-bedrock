# Fraud-review agent: case study

*Status: results pending the final evaluation (explanation judging and the dashboard). Every figure below comes from `docs/measurements/eval-runs.jsonl` and is on simulated data.*

Card-fraud teams drown in alerts, and most of them are legitimate. This project is a two-stage Claude agent that triages flagged transactions. Claude Haiku 5.5 screens each alert, and Claude Sonnet 5.5 reviews whatever Haiku will not clear. A router written in code, not the model, decides whether a clear is allowed, by checking the cited policy clause against the alert. Nothing that looks like fraud is closed without a person: the agent has no tool to block a card or contact anyone, and every step lands in a hash-chained audit log.

The safety bar is zero fraud auto-cleared. The first frozen evaluation failed it, leaking 2 of 79 frauds (simulated data). That run stays in the log. After one policy change, a fresh set of 500 alerts the agent had never seen leaked 0 of 55 frauds; the exact 95% upper bound is 5.3% (simulated data). The cost was automation: 8.8% of alerts were auto-cleared, down from 36.8%, all of them correctly (simulated data).

## My role

I set the goals and the hard rules. Those were zero fraud leakage, a spend cap with my approval for any run over five dollars, and evaluation through my own EvalKit harness. I made the calls at each fork: moving from Amazon Bedrock to the Claude API when the AWS project could not enable Claude, the risk ceiling and night rule after the leaks, and approving the over-budget eval run. Claude Code built the code, tests and measurements, working under a build harness ported from my EvalKit project. That harness uses a graph of nodes with converge gates and fresh-context verifier passes before any node is marked done, and every run is recorded in a public spend ledger.

## Key decisions

**The model recommends; code decides (D-5, D-6).** The policy is written as numbered clauses, each with a machine-checked condition. A "clear" from either model is only a proposal, which the router accepts when a cited clearing clause holds and no never-auto-clear clause does.
*Trade-off:* the policy has to be expressible as conditions on a few allow-listed fields, so nuance the model sees but the rules cannot express never clears an alert on its own.

**A failed evaluation stays in the record, and the fix is tested on fresh alerts (D-39, D-40).** When the first frozen eval leaked two frauds, both small purchases at 11 p.m., the fix (never auto-clear at night) was chosen on the dev set. It was then proven on a second, disjoint sample drawn in advance, never on the eval that exposed the problem.
*Trade-off:* auto-clear fell from 36.8% to 8.8% (simulated data), and a fresh sample costs another paid run. Without both, the zero-leakage claim would not be honest.

**Claude API instead of Amazon Bedrock for the published runs (D-35).** The AWS project stayed on the free plan with Claude not authorised, which blocked every node that calls a model. The request shape is identical on both providers, so the switch is one config line and Bedrock remains a supported path.
*Trade-off:* the published results do not exercise Bedrock, which the architecture diagram marks as "supported, not run".
