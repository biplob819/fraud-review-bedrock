# N1.3 — Model client, cost and cache

**Wave 1. Depends on:** N1.1, W0.2. **Unblocks:** A2.3 (stage 1), and through it every node that calls a model.

## Goal
One way to call a model, used by both stages: a guarded, schema-constrained request; retries that follow §2.4; a content-addressed cache so reruns and resumes cost nothing; per-call cost from `prices.yaml`; a pre-run estimate and a running spend cap; and a spend ledger. The provider (Claude API or Bedrock, D-27, D-35) is one switch (D-1, D-2, D-3, D-4, D-16, D-20, E2, E3, H8, H9).

## Outputs
- `src/fraudreview/llm.py`:
  - `make_client(config)`: `AsyncAnthropic` for `anthropic`, `AsyncAnthropicBedrockMantle` in the config Region for `bedrock`. SDK retries are off, so §2.4 is the only retry policy (BRK-08).
  - `build_body(stage, system, user, output_model)`: model, `max_tokens`, adaptive thinking, `output_config` with the stage's effort and the output model's JSON schema (`anthropic.transform_schema`), the system blocks with a cache breakpoint on the last one, and one user message. No sampling parameters and no `tools` (BRK-01, D-4, D-16).
  - `call_model(client, body, output_model)`: runs the body through `router.guard_request` before every send, then calls `messages.create`. The reply text is validated with the pydantic model (so strictness the API drops, such as lengths and ranges, is still enforced). Returns a `CallResult` (output or error kind, attempts, summed tokens, request id, latency, stop reason) (BRK-01, BRK-02).
  - Retries (§2.4, BRK-02, BRK-07): rate limits, overload, 5xx and connection errors back off exponentially with jitter, up to 6 attempts, then `transport`. An invalid reply (bad JSON, schema mismatch or `max_tokens`) is retried once, then `schema`. A refusal is never retried: `refusal`. Authentication, permission, not-found and bad-request errors raise `ProviderConfigError` (exit 2) and are never `transport`.
  - `to_decision(...)` builds a `TriageDecision` from a `CallResult` with the provider, model, effort, hashes and cost (BRK-08).
- `src/fraudreview/cache.py` (BRK-03): `cache_key(...)` is sha256 over canonical JSON of step name, `code_version`, input, model, effort, prompt hash, policy version and seed. `Store` keeps `.fraud/store/<key[:2]>/<key>.json`, written to a temporary file and linked into place so the first writer wins. A read checks that the stored key and `code_version` match. `code_version_problems` fails when a registered step's source changes without a `code_version` bump (EvalKit RUN-07); the registry is `src/fraudreview/code_versions.json`.
- `src/fraudreview/cost.py`:
  - `call_cost(tokens, price)`: input, output (thinking included), cache reads and cache writes at the 5-minute rate (BRK-04).
  - `estimate_run(n, prices, config, assumptions)`: the §9 formula with planning token counts, `s = 1` by default (BRK-05).
  - `SpendCap`: before each call, actual plus reserved plus this call's estimate must stay at or under `max_cost_usd`; `reserve`, `commit` and `release`; `CapReached` maps to exit 3 (BRK-05, EvalKit L-12, L-14).
  - `bounded(items, worker, concurrency)`: an asyncio semaphore limits calls in flight (BRK-07).
- `src/fraudreview/ledger.py`: `append_spend` writes one JSON line per real run to `docs/measurements/spend-ledger.jsonl`, in a single append (BRK-06).
- `fraud triage --dry-run --split dev|eval [--alerts N]`: prints call counts and the estimate and makes no call; it refuses when the estimate exceeds `max_cost_usd` (BRK-05). Live triage arrives in A2.5.
- `docs/measurements/structured-schemas.json`: one live call per stage through `call_model` with `Stage1Output` and `Stage2Output`, showing the API accepts both schemas and the replies validate (a fraction of a cent).

## Rules
- Every request passes `guard_request` immediately before it is sent; no code path sends a body that skipped it.
- Tests inject the client, the sleep and the random source; no test touches the network.
- A failed call never becomes a clear: errors are returned as error kinds for the router to escalate (D-20).
