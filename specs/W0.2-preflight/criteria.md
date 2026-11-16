# W0.2 acceptance criteria

**PRE-01 — model access**
- For each configured stage model, `fraud doctor` reports `ok` only when Bedrock gives `authorizationStatus` AUTHORIZED and the Region is AVAILABLE. Otherwise it reports `fail` with the statuses.
- `docs/measurements/preflight.json` records both models as authorised in us-east-2.

**PRE-02 — quotas and concurrency**
- The doctor lists every Bedrock quota naming the model, with its value, and fails when none is above 0.
- `preflight.json` records the quotas, and `config.yaml`'s `concurrency` is set no higher than the requests-per-minute quota allows.

**PRE-03 — smoke call**
- One structured-output call per model through the Mantle client returns a schema-valid reply.
- A repeat of a cacheable prompt shows a cache read.
- Tokens and cost are recorded in `preflight.json`.

**PRE-04 — prices**
- `prices.yaml` has input, output, cache read and both cache write prices for each configured model, in USD per million tokens, for the config's Region, with the date it was checked.
- The doctor fails for a missing or out-of-range price or a Region mismatch, and warns when the prices are over 90 days old.

**PRE-05 — Kaggle token**
- The doctor reports a token as present without showing it.
- With no credential it warns rather than fails, because the token is only needed to download again.
- A malformed credential fails, with a message that leaves the credential out.
- The pinned source downloads (N1.2, green).

**PRE-06 — EvalKit pin**
- `pyproject.toml` pins `evalkit-harness` to the exact v0.2.0 commit.
- `evalkit.__version__` is 0.2.0, and `evalkit.scorers.faithfulness` (`FaithfulnessItem`, `faithfulness`) imports.

**PRE-07 — `fraud doctor`**
- `--offline` makes no AWS call.
- Online, it checks identity, access and quotas per model. `--smoke` adds the calls.
- No output contains the account id beyond its last four digits, or any token value.
- Exit codes: 2 for an invalid config, 1 for any failed check, 0 otherwise.
