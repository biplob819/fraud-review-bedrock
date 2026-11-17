# fraud-review-bedrock build graph

Source of truth for build-node dependencies. Parsed by `scripts/harness_lib.py`.
A node may start only when every node in its `Depends on` cell is green (all its rows in `catalog.tsv` are green).
Edge rules: no cycles; a node inherits the constraints of what it depends on; `Verified by` must pass before a node flips green.
Generated from Tech Spec §11.2; edit here from now on and keep the Tech Spec in sync.

## Wave 0 — harness and access

| Node | Title | Depends on | Verified by | Deliverable | Stories |
| --- | --- | --- | --- | --- | --- |
| **W0.1** | Repo and harness | — | node_converge + verifier | Repo, uv project, CI, ported harness scripts with self-tests, records files, honesty-check stub | — |
| **W0.2** | Access pre-flight | W0.1 | node_converge + verifier | `fraud doctor`; Bedrock access and quotas confirmed for both profiles; one structured-output smoke call per model; `prices.yaml` from Bedrock pricing; Kaggle token; EvalKit pinned | — |

## Wave 1 — core

| Node | Title | Depends on | Verified by | Deliverable | Stories |
| --- | --- | --- | --- | --- | --- |
| **N1.1** | Schemas and config | W0.1 | node_converge + verifier | `fraudreview.schemas` (§3), `RunConfig` loader, results format v1 fixtures | US5 |
| **N1.2** | Data load, minimisation and splits | N1.1 | node_converge + verifier | FDB loader, minimisation, prefix strip, time splits with hashes, `DATA_CARD.md` | US5 |
| **N1.3** | Bedrock client, cost and cache | N1.1, W0.2 | node_converge + verifier | Mantle client wrapper, structured output, retries, cache, cost estimate, spend cap, ledger | US1 |
| **N1.4** | Policy as code | N1.1 | node_converge + verifier | `policy.md` v1, YAML clause loader, predicate parser, `policy_version`, clause validator | US3, US4 |

## Wave 2 — baseline and agent

| Node | Title | Depends on | Verified by | Deliverable | Stories |
| --- | --- | --- | --- | --- | --- |
| **A2.1** | Features, baseline, alerts and SHAP | N1.2 | node_converge + verifier | Point-in-time features, LightGBM, D-9 threshold, alerts, dev and eval samples, SHAP top 5 | US1, US2, US4 |
| **A2.2** | Router, queue and audit log | N1.1, N1.4 | node_converge + verifier | Router rules 1–6, dispositions, queues, hash-chained audit events, no-tools guard | US2, US5 |
| **A2.3** | Stage 1 triage | A2.1, A2.2, N1.3 | node_converge + verifier | Haiku 5.5 stage, allow-list prompt, `Stage1Output`, router hand-off | US1 |
| **A2.4** | Stage 2 triage | A2.3 | node_converge + verifier | Sonnet 5.5 stage, `Stage2Output`, retry-once-then-escalate, refusal handling | US1, US3 |
| **A2.5** | Batch triage run | A2.4 | node_converge + verifier | `fraud triage`, concurrency within quota, dev tuning runs, frozen eval run of 500, golden recording | US1, US2 |

## Wave 3 — evaluation

| Node | Title | Depends on | Verified by | Deliverable | Stories |
| --- | --- | --- | --- | --- | --- |
| **E2.1** | Metrics and intervals | A2.1, A2.2 | node_converge + verifier | Leakage, recall versus baseline, auto-clear rate and precision, minutes, cost, latency, intervals, thresholds | US1, US2 |
| **E2.2** | Explanation checks | A2.4, A2.1, N1.4 | node_converge + verifier | Validity checks, value checks, protected scan, driver agreement, EvalKit faithfulness with two judges, our checks as EvalKit scorers, prompt-injection probe, judge calibration, paired comparisons, mismatch picks | US4 |
| **E2.4** | Monitoring and improvement | E2.1, E2.2, A2.5 | node_converge + verifier | Run history, alarm bands, drift check, trend data, experiment ledger, dev-set improvement loop, freeze guard (OWNER D-36) | US1, US4 |
| **E2.3** | Summary and dashboard | E2.1, E2.2, E2.4, A2.5 | node_converge + verifier | `fraud evaluate`, `summary.json`, `fraud report`, deployed dashboard, honesty gate live | US1–US4 |

## Wave 4 — business artifacts

| Node | Title | Depends on | Verified by | Deliverable | Stories |
| --- | --- | --- | --- | --- | --- |
| **B3.1** | ROI model | E2.3 | node_converge + verifier | Spreadsheet with marked inputs, defaults from `summary.json`, leakage-loss term, formula test | US6 |
| **B3.2** | PR/FAQ | E2.3, B3.1 | node_converge + verifier | Two-page PDF: press release, customer FAQ, internal FAQ naming model risk review | US7 |
| **B3.3** | Reference architecture | A2.5 | node_converge + verifier | AWS diagram with built-versus-proposed legend, exported for the site | US9 |
| **B3.4** | Discovery summary | B3.1 | node_converge + verifier | One-page PDF for the sponsor, labelled illustrative, inputs shared with the ROI model | — |
| **B3.5** | Reusable patterns page | E2.3, B3.1 | node_converge + verifier | `docs/patterns.md`: policy-as-code, review queue, explanation checks, cost model, each with limits | — |

## Wave 4 (continued) — demo, release and publishing

| Node | Title | Depends on | Verified by | Deliverable | Stories |
| --- | --- | --- | --- | --- | --- |
| **D4.1** | Analyst review screen | E2.3, A2.2 | node_converge + verifier | `fraud demo-cache`, Streamlit Community Cloud page, replay only, session audit log | US3, US8 |
| **D4.3** | Open-source release | E2.3, D4.1, B3.5 | node_converge + verifier | Community files, quickstart (keyless replay, then own keys on Bedrock or the Anthropic API), docs, fresh-clone CI smoke, v0.1.0 GitHub release | — |
| **D4.2** | Project page, video and supporting artifacts | D4.1, D4.3, B3.2, B3.3, B3.4, B3.5 | node_converge + verifier | Project write-up, 90-second video, one-page PRD, decision log, data card, build notes | — |
