# Security

## How credentials are handled

- The code never contains a key. API keys and tokens are read from the environment only.
- Put keys in a `.env` file at the repository root, or export them in your shell. `.env` is in `.gitignore`. Never commit it, paste it into an issue or print it.
- `.env.example` lists the variable names with empty values. Copy it to `.env` and fill it in.
- The Claude API key is read from `ANTHROPIC_API_KEY`. Amazon Bedrock uses your AWS profile and the AWS credential chain instead, so no key sits in the project.
- The data download reads a Kaggle token from `KAGGLE_API_TOKEN`, `~/.kaggle/access_token` or `~/.kaggle/kaggle.json`. It is never printed, and it is sent to Kaggle only.
- `fraud doctor` says whether a credential is present. It never shows the value.
- `scripts/secret_scan.py` runs in CI and in the pre-commit hook. It fails the build if a tracked file looks like it holds a key.
- The replay demo reads no key and reaches no network.
- The spend ledger (`docs/measurements/spend-ledger.jsonl`) is public by design. It holds run ids, call counts and costs, and never a key, an account id or a request id.

If you think a key has leaked, revoke it at the provider first. Then tell us.

## What the agent can and cannot do

The agent has no tool. It cannot block a card, contact a customer or change a record. It can only recommend, and a rule written in code decides. A request guard refuses any model call that carries tool definitions.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

1. Go to the repository on GitHub and open the Security tab.
2. Choose "Report a vulnerability". This opens a private security advisory that only the maintainers can see.
3. Say what you found, the steps to reproduce it, and what you think the impact is.

We aim to reply within a few days. We will tell you when it is fixed and, if you want, credit you in the changelog.

## Supported versions

Only the latest release gets fixes. See `docs/versioning.md`.

## Out of scope

This project runs on simulated data and is not validated for real use. Reports about running it on real card data, without your own review, are not security issues for this project.
