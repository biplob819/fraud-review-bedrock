# A2.3 acceptance criteria

**ST1-01 — prompt**
- The system is two blocks with a cache breakpoint on the last; together they are over the models' minimum cacheable length (512 tokens).
- The first block states rules B1–B8 and the stage job; the second is the policy prose with every clause id and no YAML.
- `prompt_hash` changes when the policy, the rules or the output schema change, and is stable otherwise.

**ST1-02 — stage 1 call and hand-off**
- `run_stage(1, …)` returns a `TriageDecision` with a `Stage1Output` or an error kind, recorded with provider, model, effort, prompt hash, policy version, tokens and cost.
- A second run of the same alert is a cache hit with no model call; transport failures are not cached.
- `needs_stage2` is False only for a clear the router accepts; a pass, a refused clear or any error is True.
- Live: 10 dev alerts through `run_stage(1, …)` are recorded with a cache hit on the rerun.

**ST1-03 — prompt safety**
- Every rendered dev and eval case holds exactly the allow-listed keys, and no rendered prompt contains a dropped-column name used as a key, the alert id, the merchant, or a truth field.
- No module in the agent path (`prompts`, `stages`, `llm`, `router`, `policy`, `cost`, `cache`) imports `fraudreview.truth` or `fraudreview.baseline`.
