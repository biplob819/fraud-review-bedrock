# A2.3 — Stage 1 triage

**Wave 2. Depends on:** A2.1, A2.2, N1.3 (and OWNER D-37, the approved alert set). **Unblocks:** A2.4 (stage 2).

## Goal
Haiku 5.5 screens each alert: it may recommend `clear` for an alert that is clearly routine under a clear clause, and `pass` for everything else. Only a clear the router accepts ends an alert at stage 1; a pass, a refused clear or an error goes to stage 2 (FR5, D-5, D-7, D-16, NFR2).

## Outputs
- `src/fraudreview/prompts.py` (ST1-01, §4.2):
  - `system_blocks(stage, policy)`: two cached blocks. The first holds the role, the rules B1–B8 in plain words, "you recommend; people decide", the stage's job and a glossary of the citable fields. The second is the policy prose and clause ids from `Policy.prompt_text()`, without YAML.
  - `alert_payload(case)` and `render_alert(case)`: the alert as JSON from an allow-list (`ALERT_KEYS`, `FEATURE_KEYS`). The alert id, merchant, timestamp, label and SHAP drivers never enter a prompt. `check_payload` refuses any other key at any level.
  - `prompt_hash(stage, policy)`: sha256 over the cached blocks and the stage's output schema.
- `src/fraudreview/stages.py` (ST1-02):
  - `run_stage(stage, case, ctx)`: renders the prompt and looks it up in the cache. The key covers step, `STAGE_CODE_VERSION`, the allow-listed alert, model, effort, prompt hash, policy version and seed. On a miss it calls `llm.settled_call` and builds the decision with `to_decision`, which records provider, model, effort, hashes, tokens and cost. It stores every decision except transport failures, so a rerun retries those.
  - `needs_stage2(stage1, case, ctx)`: the hand-off. Only a clear that `router.check_clear` accepts ends the alert.
- `code_versions.json` registers `stage1` and `stage2` (the `stages` module) and `prompts`, so a change to either needs a version bump.
- `docs/measurements/stage1-sample.json`: a live stage-1 run on the first 10 dev alerts through `run_stage`, showing actions, router acceptance, tokens, cost and a cache hit on a rerun (under $0.02).

## Rules
- Prompts hold only allow-listed keys and no value from a dropped column (NFR2, ST1-03).
- No agent module imports `fraudreview.truth` or `fraudreview.baseline` (labels and SHAP drivers stay out of the agent's reach).
- A stage-1 error is never a clear: it goes to stage 2.
