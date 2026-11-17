# W0.2 — Access pre-flight

**Wave 0. Depends on:** W0.1. **Unblocks:** N1.3 (model client), and through it A2.3 onward.

## Goal
Prove, before any build node spends money, that the configured provider can call both run models, at what limits and price. Since OWNER D-35 the provider is the Claude API; Bedrock in the project's own Region stays supported. Also prove that the data token and the EvalKit pin are in place, and give a one-command `fraud doctor` that says what is missing (PRD risk 1, D-2, D-3, D-27, D-33, D-34, D-35, E5, E10, H8, H9, H10).

## Outputs
- `config.yaml`: provider `anthropic`, model ids `claude-haiku-5-5` and `claude-sonnet-5-5`, and `max_cost_usd: 5` (D-33, D-35). Region `us-east-2` applies when the provider is `bedrock`, with ids `anthropic.claude-haiku-5-5` and `anthropic.claude-sonnet-5-5` (D-34).
- `prices.yaml`: one table per provider, USD per million tokens for both models (input, output, cache read, cache writes): the Claude API rate card, and the Bedrock agreement rate card for us-east-2. It records the date it was checked (PRE-04).
- `pyproject.toml` pins EvalKit to the v0.2.0 commit, and its faithfulness scorer imports (PRE-06).
- `src/fraudreview/doctor.py` and `fraud doctor [--offline] [--smoke] [--record PATH]` (PRE-07):
  - Offline checks: Python, config, prices (complete for the provider, right Region for Bedrock, recent), Kaggle token (never shown), and EvalKit version.
  - Online checks, Claude API: the key is set (never shown), and a free model listing shows each stage model.
  - Online checks, Bedrock: AWS identity, showing only the last four digits of the account; model authorisation per stage model; quotas.
  - `--smoke` adds one tiny call per model. `--record` (Claude API) adds a structured call and a cache pair per model and writes the proof file.
  - The command reads `ANTHROPIC_API_KEY` from a git-ignored `.env` when it is not already set; `.env.example` lists the names.
  - Exit code: 2 for an invalid config, 1 for any failed check, 0 otherwise.
- `docs/measurements/preflight.json`: the recorded output of `fraud doctor --record` (PRE-01, PRE-02, PRE-03). Per model it holds whether the structured reply was valid, the rate limits, the cache pair's usage, and every call's tokens and cost; it holds no key, account or request id.

## Rules
- No check prints a secret: the account shows its last four digits only, and the token shows its kind only.
- Every client is injected, so tests never touch AWS or the Claude API.
- `--smoke` and `--record` are the only checks that spend money: a fraction of a cent, and about half a cent.

## Provider history
The node was first built for Bedrock. The owner's AWS project stayed on the free plan with Claude `NOT_AUTHORIZED` and the use-case form refused, so the owner ruled D-35: run on the Claude API. The Bedrock path and its tests remain.
