# Discovery summary: card-fraud alert review

*Illustrative. This is a simulated scenario for a bank that does not exist, not customer research. Figures marked "simulated" come from `docs/measurements/summary-eval2.json`. Figures marked "assumption" are made up for the example. They are the same inputs as the ROI model (`docs/roi/README.md`).*

## Alert volumes and handling cost

| Item | Value | Source |
| --- | --- | --- |
| Alerts per month | 20,000 | Assumption (illustrative, simulated scenario) |
| Analyst time per alert | 4 minutes | Assumption used in the simulated run |
| Analyst cost per hour | $40 | Assumption (illustrative, simulated scenario) |
| Fraud share of alerts | 11% | Simulated eval, 55 of 500 alerts |

At these inputs the team spends about 1,333 analyst hours a month on alerts (illustrative, simulated scenario).

## Who touches an alert

1. A rules engine raises the alert.
2. An analyst reads it, checks the policy and decides: clear, hold or escalate.
3. A senior analyst takes escalations and anything that looks like fraud.
4. Model risk and compliance review the policy and the audit log. They do not touch single alerts.

This flow is an assumption for the example (simulated scenario).

## Risk and compliance constraints

- Nothing that looks like fraud is closed without a person. The agent has no tool to block a card or contact anyone.
- Every decision cites a numbered policy clause, and every step is written to a hash-chained audit log.
- Model risk review must approve the policy and the leakage bar before real use.
- The bank's data stays inside its own boundary. This project uses simulated data only.

## Success criteria

| Criterion | Target | Source |
| --- | --- | --- |
| Fraud caught | Zero fraud auto-cleared. The simulated run leaked 0 of 55, with an exact 95% upper bound of 5.3% | Simulated eval |
| Hours saved | 8.8% of alerts auto-cleared, about 117 analyst hours a month | Simulated eval, with the assumptions above |
| Leakage bar | Net saving stays positive. At the 5.3% bound (simulated) it does not, so a larger real sample is needed | ROI model, illustrative |

## Open questions

- What is the real alert volume, and how long does an analyst really spend on one?
- What is the average loss per missed fraud (the model assumes $500, illustrative, simulated scenario)?
- How large a labelled sample can the bank provide to tighten the leakage bound?
