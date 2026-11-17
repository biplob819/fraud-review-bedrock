# W0.2 acceptance criteria

**PRE-01 — model access**
- Claude API (D-35): `fraud doctor` reports `ok` for a stage model only when the key is set and a model listing shows the model; otherwise `fail`, naming only the error type.
- Bedrock: `ok` only when `authorizationStatus` is AUTHORIZED and the Region is AVAILABLE; otherwise `fail` with the statuses.
- `docs/measurements/preflight.json` records a valid live reply from both configured models.

**PRE-02 — limits and concurrency**
- Claude API: the `anthropic-ratelimit-*` request and token limits from a live reply are recorded per model, with the most workers the requests-per-minute limit allows at one call per worker every 10 seconds.
- Bedrock: the doctor lists every quota naming the model, with its value, and fails when none is above 0.
- `config.yaml`'s `concurrency` is no higher than the recorded limit allows.

**PRE-03 — structured call and cache**
- One schema-constrained call per model returns a reply that matches the schema; a reply that does not is an error.
- A repeat of a cacheable prompt (the policy file as the system prompt) shows a cache read.
- Tokens and cost of every call are recorded in `preflight.json`.

**PRE-04 — prices**
- `prices.yaml` has one table per provider with input, output, cache read and both cache write prices for each configured model, in USD per million tokens, with the date it was checked. The Bedrock table names its Region.
- The doctor fails for a missing provider table, a missing or out-of-range price, or (Bedrock) a Region mismatch, and warns when the prices are over 90 days old.

**PRE-05 — Kaggle token**
- The doctor reports a token as present without showing it.
- With no credential it warns rather than fails, because the token is only needed to download again.
- A malformed credential fails, with a message that leaves the credential out.
- The pinned source downloads (N1.2, green).

**PRE-06 — EvalKit pin**
- `pyproject.toml` pins `evalkit-harness` to the exact v0.2.0 commit.
- `evalkit.__version__` is 0.2.0, and `evalkit.scorers.faithfulness` (`FaithfulnessItem`, `faithfulness`) imports.

**PRE-07 — `fraud doctor`**
- `--offline` makes no network call.
- Online, it checks the provider's access per model (and, on Bedrock, identity and quotas). `--smoke` adds the calls.
- No output contains the account id beyond its last four digits, or any key or token value; `.env` fills only names that are not already set.
- Exit codes: 2 for an invalid config, 1 for any failed check, 0 otherwise.
