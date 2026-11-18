# Product requirements, one page

*A simulated scenario on simulated data. The full document is [PRD.md](../PRD.md), and how it is built is in the [Tech Spec](../TECH_SPEC.md). The full PRD records the original plan, so this page says what changed.*

## Problem

Fraud analysts spend most of their day on alerts that turn out to be legitimate. Banks want faster triage, but risk teams will not accept a black box: every decision needs a reason and an audit trail.

## Goals

1. A baseline model that turns transactions into alerts, with its recall as the bar.
2. A two-stage agent that clears, holds or escalates each alert, citing policy clauses and the fields it used.
3. No automatic card blocks. A clear is allowed only when a rule in code says so, and anything that looks like fraud reaches a person.
4. An evaluation of fraud leakage, auto-clear rate, analyst time saved, cost and explanation quality.
5. A press release and FAQ, an ROI model, a reference architecture and an analyst review screen.

**Non-goals:** real bank data, streaming, production deployment, replacing the bank's fraud models.

## What shipped

All the goals above, on simulated data, with these results: 0 of 55 frauds auto-cleared on a fresh set of 500 alerts (exact 95% upper bound 5.3%, simulated data), and 8.8% of alerts auto-cleared (simulated data). The first evaluation failed and is kept in the record.

## What changed from the plan

- A late-night rule was added after the first evaluation failed: no auto-clear from 10 p.m. to 6 a.m. (clause P5.9, decision D-40). It cost automation, taking auto-clear from 36.8% to 8.8% (simulated data).
- The published runs use the Claude API. Amazon Bedrock stays a supported path and was not run for the results (decision D-35).
- The demo is a replay that runs on your own machine. The hosted version is planned for v0.2 (D-44).
- There is no video (D-45).
- Judging explanation quality with a second model is only partly done: one judge, not calibrated (D-43).

## Where to look

[Project page](project.md), [case study](case-study.md), [decision log](decisions.md), [data card](../DATA_CARD.md), [build notes](lessons.md).
