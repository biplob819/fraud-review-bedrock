# fraud-review-bedrock PRD

@Biplob Chakraborty

A fraud-review agent on Amazon Bedrock triages flagged card transactions, explains each decision against a written policy and sends uncertain cases to a person, packaged with a discovery summary, a PR/FAQ, an ROI model, a reference architecture and a page of reusable patterns.

This is the product view. How it is built, the design decisions and the loophole review of this PRD are in the [Tech Spec](TECH_SPEC.md).

## Overview and deliverables

This is the pack a team would bring to a bank pilot: a working agent, proof it does not miss fraud, the money it saves, an architecture the bank's engineers can reuse, and the discovery notes that scoped it. Each of those has its own deliverable below, so a bank can check the agent, the numbers behind it, and what it would take to run a pilot.

**Problem.** Fraud analysts spend most of their day on alerts that turn out to be legitimate. Banks want faster triage, but risk teams and regulators will not accept a black box: every decision needs a reason and an audit trail.

**Goals.**

1. A baseline classifier that turns transactions into alerts, with its recall and precision recorded as the bar.
2. A two-stage agent on Bedrock that clears, escalates or holds each alert, citing policy clauses and the fields it used.
3. No automatic card blocks: every hold reaches a person.
4. Evaluation of fraud recall, auto-clear rate, analyst minutes saved, cost per 1,000 alerts and explanation faithfulness.
5. A PR/FAQ, an ROI spreadsheet and a reference architecture.
6. An analyst review screen a reviewer can click through.

**Non-goals.** Real bank data; real-time streaming; production deployment; replacing the bank's fraud models.

**Deliverables.**

| Deliverable | What it is | Where it lives |
| --- | --- | --- |
| GitHub repo `fraud-review-bedrock` | Loader, features, baseline, triage agent, policy, queue, audit, faithfulness check, tests | GitHub, public |
| PR/FAQ | Press release, customer FAQ and internal FAQ in Amazon's working-backwards format, two pages | PDF, linked from the project page |
| ROI model | A spreadsheet with marked input cells a bank can change to its own numbers | Shareable spreadsheet, linked from the project page |
| Reference architecture | The AWS diagram from this PRD, exported for the site | Project page |
| Discovery summary | One page for the bank's sponsor: alert volumes and handling cost, who touches an alert, risk and compliance constraints, and success criteria tied to fraud caught and analyst hours saved | PDF, linked from the project page |
| Reusable patterns page | What the next bank engagement could reuse: the policy-as-code format, the human-review queue, the faithfulness check and the cost model, each with its limits | docs/patterns.md in the repo, and the project page |
| Results dashboard | Fraud leakage and recall against the baseline, auto-clear rate, minutes saved, cost, explanation checks | GitHub Pages |
| Demo page | The analyst review screen, hosted in replay mode | Streamlit Community Cloud |
| Project page and video | A write-up of the build; a 90-second video walking one alert from score to decision | Project site |
| Supporting artifacts | One-page PRD, decision log, data card, build notes | Repo and project page |

## Personas, user stories and flows

Four personas drive nine stories; the seven marked Must make up the core scope.

**Personas.**

- **Fraud operations lead at a mid-size bank (the customer).** Owns alert backlog, analyst headcount and the risk sign-off.
- **Fraud analyst (the end user).** Works the queue; needs to see why a case was held and decide quickly.
- **Model risk reviewer at the bank.** Must approve any automated decision and its audit trail.
- **Executive sponsor at the bank (the buyer).** Funds a pilot; reads the PR/FAQ, the discovery summary and the business case.

**User stories.**

| ID | As a | I want to | So that | Acceptance criteria | Priority |
| --- | --- | --- | --- | --- | --- |
| US1 | Fraud ops lead | have obvious false positives cleared automatically | analysts spend time on real risk | Auto-clear rate and its precision reported at the chosen threshold | Must |
| US2 | Fraud ops lead | be sure no more fraud slips through than today | I can sign off the change | No fraud in the 500 evaluation alerts is auto-cleared; the 95% upper bound on leakage and end-to-end recall beside the baseline are reported | Must |
| US3 | Analyst | see each held case with its reasons, policy clauses and the fields used | I decide in seconds, not minutes | The review screen shows reasons, clauses and fields for every hold | Must |
| US4 | Model risk reviewer | see that explanations match what drove the score | the reasons are not invented | Faithfulness rate reported, with three mismatched examples | Must |
| US5 | Model risk reviewer | have every decision logged with inputs, model and reasons | I can audit any case later | One audit entry per alert | Must |
| US6 | Fraud ops lead | change the ROI inputs to my own numbers on a call | the business case is mine, not a vendor's | The spreadsheet recalculates from marked input cells | Must |
| US7 | Executive sponsor | read a two-page PR/FAQ | I can decide whether to fund a pilot | Press release, customer FAQ, internal FAQ on two pages | Must |
| US8 | Analyst | accept, override or comment on the agent's call | my judgement trains the policy | Override and comment buttons write to the audit log | Should |
| US9 | Bank engineer | see how this maps onto AWS services | I can plan a pilot | Reference architecture with named services | Should |

**Flow A. Triage a batch (US1 to US5).**

1. `fraud load` pulls the Sparkov data through the benchmark loader and drops names and street addresses.
2. `fraud baseline` trains the classifier, sets the alert threshold and stores the top five SHAP drivers per alert.
3. `fraud triage --alerts 500` runs stage 1 on Claude Haiku 5.5 and stage 2 on Claude Sonnet 5.5 through Bedrock.
4. Holds and escalations go to the queue; every decision goes to the audit log.
5. `fraud evaluate` scores recall, auto-clear rate, minutes saved, cost and faithfulness.

**Flow B. Review a held case (US3, US8).**

1. The analyst opens the queue and picks the top case.
2. The screen shows the transaction, the risk score, the agent's reasons with policy clauses, and the score drivers side by side.
3. The analyst confirms fraud, clears the case, or overrides with a comment; the action is logged.

## Requirements

Twelve functional requirements and six non-functional bars; the hardest bar is that no fraud is auto-cleared.

**Functional.**

| ID | Requirement | Stories |
| --- | --- | --- |
| FR1 | Load `sparknov` through Amazon's Fraud Dataset Benchmark loader with a Kaggle token; drop names and street addresses | US5 |
| FR2 | Features: amount against the customer's usual spend, hour of day, category frequency, first purchase at a merchant | US1 |
| FR3 | A gradient-boosted baseline with recall and precision at the alert threshold recorded | US2 |
| FR4 | Top five SHAP drivers stored per alert | US4 |
| FR5 | Stage 1 on Claude Haiku 5.5: clear or pass on, citing the policy | US1 |
| FR6 | Stage 2 on Claude Sonnet 5.5: structured output `{action, reasons, policy_clauses, fields_used, confidence}` | US1, US3 |
| FR7 | Invalid output retries once, then escalates | US2 |
| FR8 | No automatic blocks: hold always routes to the queue | US2 |
| FR9 | An audit entry per decision: inputs, model, reasons, timestamp, analyst action | US5, US8 |
| FR10 | Explanation checks: cited clauses and fields exist and reasons match the alert's values (EvalKit faithfulness, both judges), plus agreement between `fields_used` and the SHAP drivers, which the agent never sees | US4 |
| FR11 | PR/FAQ, ROI spreadsheet and reference architecture | US6, US7, US9 |
| FR12 | Analyst review screen with confirm, clear and override actions | US3, US8 |

**Non-functional.**

| ID | Area | Bar |
| --- | --- | --- |
| NFR1 | Safety | No fraud in the 500 evaluation alerts is auto-cleared, reported with the 95% upper bound on leakage and end-to-end recall beside the baseline on the same alerts |
| NFR2 | Privacy | No name or street address in any prompt, checked by a test |
| NFR3 | Cost | About $5 per 500 alerts if all reach Sonnet; global inference profile, no regional premium |
| NFR4 | Auditability | 100% of decisions logged with inputs and reasons |
| NFR5 | Honesty | "On simulated data" beside every number |
| NFR6 | Speed | Stage 2 under 10 seconds per alert at p95 |

## Behavioral spec

The agent recommends; people decide anything that affects a customer. Every rule has eval cases drawn from the 500 alerts.

| ID | Rule | Type | Eval cases |
| --- | --- | --- | --- |
| B1 | Return one action (clear, escalate, hold) with at least one reason, a policy clause and the fields used | Must | All 500 alerts, schema check |
| B2 | Cite only fields present in the transaction and its features | Must | Faithfulness check |
| B3 | Escalate when confidence is low or reasons conflict | Must | Alerts near the threshold |
| B4 | Clear an alert only when the policy names a clearing condition that applies | Must | All cleared alerts, policy check |
| B5 | Compare against the customer's usual behaviour when explaining | May | Sampled reasons |
| B6 | Block a card or contact a customer | Never | No such tool exists; checked in code |
| B7 | Use protected attributes (gender, age, location as a proxy) as a reason | Never | Reasons scanned for these fields |
| B8 | Invent policy clauses | Never | Clause IDs checked against `policy.md` |

**Good output.** `{action: "hold", reasons: ["Amount is 9x this customer's usual spend", "First purchase at this merchant"], policy_clauses: ["P3.1", "P4.2"], fields_used: ["amt_vs_usual", "merchant_first_seen"], confidence: 0.62}`

**Bad output.** `{action: "block", reasons: ["Looks suspicious"]}`. It uses an action that does not exist, gives no clause and names no fields.

## Trust architecture

A bank trusts this because the agent cannot act on customers, every decision is explained against policy and logged, and its effect on fraud recall is measured before rollout.

| Layer | What it does | Risk it handles | Evidence |
| --- | --- | --- | --- |
| Baseline guardrail | No fraud is auto-cleared; recall is reported against the existing model | Missing more fraud than today | Leakage with its upper bound, and recall beside the baseline, on the same 500 alerts |
| No-action design | The agent has no tool to block cards or contact customers | Harm from automated decisions | Tool list in code |
| Human review | Holds and escalations go to an analyst, who can override | Wrong calls on edge cases | Queue and override log |
| Policy grounding | Every reason cites a clause in `policy.md` | Arbitrary or unexplainable decisions | Clause check in the eval |
| Faithfulness | Cited fields compared with the score drivers | Plausible but invented explanations | Faithfulness rate and mismatched examples |
| Data minimisation | Names and addresses never reach a prompt | Privacy exposure | Prompt test |
| Fairness check | Protected attributes barred from reasons | Discriminatory reasoning | Reason scan |
| Audit | Every decision and analyst action logged | Disputes and regulator questions | One entry per alert |

In a real bank this pack would go to model risk review before any pilot; the PR/FAQ's internal FAQ names that step.

## Wireframe: analyst review screen

Layout: the transaction and risk score on the left, the agent's action, reasons, policy clauses and fields in the middle, the score drivers on the right, and Confirm fraud, Clear and Override with comment below (Tech Spec §7).

The screen puts the agent's reasons next to what actually drove the risk score, so an analyst can spot an invented explanation at a glance. There is no block button: the strongest action is confirming fraud, which in a real deployment hands the case to the bank's existing process.

## Data flow

Transaction → baseline score → alert → stage 1 → router → stage 2 → router → queue or auto-clear → audit (Tech Spec §2.2 and §2.3).

The top row is the bank's existing detection, rebuilt as a baseline. The agent sits after it and only changes what happens to alerts: cheap stage 1 clears the obvious ones, stage 2 explains the rest against the policy, and the router sends anything not cleared to a person.

## Technical architecture

The reference architecture is drawn in the Tech Spec §7 and exported as a deliverable.

The drawing is both the reference architecture deliverable and the map of what was built locally, so it is clear which parts ran and which are proposed.

- **Local build.** Python 3.12 with uv; the benchmark loader for data; LightGBM and SHAP for the baseline; the anthropic SDK's Bedrock client calling Claude through a global inference profile; pydantic for the structured output; JSONL files for decisions and audit; EvalKit pinned for faithfulness. On the Bedrock runtime the Batch API and server-side tools are not available, so tools run client-side.
- **AWS pilot (proposed).** Alerts land in S3; Step Functions runs the two stages with retries and timeouts; DynamoDB stores decisions and audit entries; the analyst queue app reads held cases; CloudWatch tracks volumes, cost and model errors. IAM gives the workflow no permission to touch card status.
- **Demo.** The analyst review screen runs on a Streamlit Community Cloud page in replay mode, from the cached run, with no Bedrock calls.

## Data model, interfaces and the ROI model

Three schemas, one policy file and a five-input ROI model carry the whole pack.

```python
class Alert(BaseModel):
    alert_id: str
    trans_ts: datetime
    merchant: str
    category: str
    amount: float
    features: dict[str, float]       # amt_vs_usual, hour, category_freq, merchant_first_seen
    risk_score: float
    shap_top5: list[tuple[str, float]]
    label: bool                      # ground truth, never shown to the agent

class TriageDecision(BaseModel):
    alert_id: str
    stage: Literal[1, 2]
    model: str                       # Bedrock inference profile ID
    action: Literal["clear", "escalate", "hold"]
    reasons: list[str]
    policy_clauses: list[str]        # must exist in policy.md
    fields_used: list[str]           # must exist in features
    confidence: float
    tokens: dict[str, int]
    cost_usd: float

class AuditEntry(BaseModel):
    alert_id: str
    at: datetime
    decision: TriageDecision
    analyst_action: Literal["confirm_fraud", "clear", "override"] | None
    analyst_comment: str | None
```

**Policy file.** `policy.md` holds numbered clauses (for example P3.1 for spend far above a customer's norm, P4.2 for first purchase at a merchant) and the conditions under which an alert may be cleared. The agent must cite clause IDs from this file.

**ROI model.** Five inputs in marked cells: alerts per month (A), minutes per alert (m), analyst cost per hour (c), auto-clear rate (r) and agent cost per alert (k), plus the build cost (B) for payback.

```latex
\text{Hours saved per month} = \frac{A \cdot r \cdot m}{60}
```

```latex
\text{Net saving per month} = \text{Hours saved} \cdot c - A \cdot k
```

```latex
\text{Payback (months)} = \frac{B}{\text{Net saving per month}}
```

**CLI.** `fraud load`, `fraud baseline`, `fraud triage --alerts 500`, `fraud evaluate`, `fraud report`.

**Demo functions.** `queue()` returns held cases from the cached run; `review(alert_id, action, comment)` records an analyst action in the demo's own audit log. Replay mode only; there is no live mode, so no Bedrock calls from the demo.

## Success metrics and risks

Success is a higher auto-clear rate with recall held at the baseline, explanations that match the score drivers, and a business case a fraud lead can rerun with their own numbers.

**Success metrics.**

| Metric | Definition | Target |
| --- | --- | --- |
| Fraud leakage | Share of true frauds in the alerts that were auto-cleared | Zero, with its 95% upper bound |
| End-to-end recall | Share of true frauds escalated or held, beside the baseline's | Reported |
| Auto-clear rate | Share of alerts cleared without a person | Reported at the precision you set |
| Analyst minutes saved | Per 1,000 alerts, from the ROI model | Reported |
| Explanation checks | Grounded reasons (EvalKit faithfulness, per judge) and agreement with the SHAP drivers | Reported, with mismatches shown |
| Cost per 1,000 alerts | Bedrock tokens | Reported |

**Risks.**

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Bedrock model access or quota delays | Medium | High | Request access before the first Bedrock node |
| Simulated data flatters the agent | High | Medium | Label every number; say what real data would change |
| Explanations do not match drivers | Medium | Medium | Report faithfulness honestly; tighten the prompt and rerun |

## Sources

- [Amazon Fraud Dataset Benchmark](https://www.github.com/amazon-science/fraud-dataset-benchmark)
- [Claude on Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock.md)
- [Claude API pricing](https://platform.claude.com/docs/en/about-claude/pricing)
