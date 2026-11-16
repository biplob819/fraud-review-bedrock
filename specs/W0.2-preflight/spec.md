# W0.2 — Access pre-flight

**Wave 0. Depends on:** W0.1. **Unblocks:** N1.3 (Bedrock client), and through it A2.3 onward.

## Goal
Prove, before any build node spends money, that the owner's AWS project can call both run models in its own Region, at what quota and price. Also prove that the data token and the EvalKit pin are in place, and give a one-command `fraud doctor` that says what is missing (PRD risk 1, D-2, D-3, D-33, D-34, E5, E10, H8, H9, H10).

## Outputs
- `config.yaml`: provider `bedrock`, region `us-east-2`, in-Region model ids `anthropic.claude-haiku-5-5` and `anthropic.claude-sonnet-5-5`, and `max_cost_usd: 5` (D-33, D-34).
- `prices.yaml`: USD per million tokens for both models in us-east-2 (input, output, cache read, cache writes), taken from the Bedrock agreement rate card, with the date it was checked (PRE-04).
- `pyproject.toml` pins EvalKit to the v0.2.0 commit, and its faithfulness scorer imports (PRE-06).
- `src/fraudreview/doctor.py` and `fraud doctor [--offline] [--smoke]` (PRE-07):
  - Offline checks: Python, config, prices (complete, right Region, recent), Kaggle token (never shown), and EvalKit version.
  - Online checks: AWS identity, showing only the last four digits of the account; model authorisation per stage model; quotas.
  - `--smoke` adds one tiny call per model.
  - Exit code: 2 for an invalid config, 1 for any failed check, 0 otherwise.
- `docs/measurements/preflight.json`: the recorded output of `fraud doctor --smoke` once access exists (PRE-01, PRE-02, PRE-03). It holds model, authorisation, quotas, smoke tokens and cost, and never the account id.

## Rules
- No check prints a secret: the account shows its last four digits only, and the token shows its kind only.
- AWS clients are injected, so tests never touch AWS.
- `--smoke` is the only check that spends money, a fraction of a cent.

## Blocked
PRE-01 (access), PRE-02 (quotas and concurrency) and PRE-03 (structured-output smoke call with a cache read on repeat) need the owner's project to be authorised for both models with a quota above 0. Until then the doctor reports `NOT_AUTHORIZED`, with quotas at 0.
