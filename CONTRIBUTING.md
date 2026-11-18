# Contributing

Thanks for looking at this project. It is a fraud-review agent that runs on simulated data. Small fixes, clearer docs and new checks are all welcome.

## Before you start

- Open an issue first for anything larger than a fix, so we can agree on the shape of it.
- This is a demonstration on simulated data. It is not validated for real use, and a change must not suggest that it is.
- Read `docs/decisions.md` and `PRD.md`. Some rules are fixed on purpose. The agent has no tool that can block a card or contact a customer, and a change that adds one will not be merged.

## Set up

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/biplob819/fraud-review-bedrock
cd fraud-review-bedrock
uv sync --locked --all-extras
```

No key is needed to build, test or read the docs. The tests make no model calls.

## Checks that must pass

Run these before you open a pull request. CI runs the same ones on Linux and macOS.

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
uv run python scripts/secret_scan.py
uv run python scripts/honesty_check.py
uv build
```

## Rules for changes

- Every number in a published file says "simulated" on the same line. `scripts/honesty_check.py` checks this. The files it covers are listed in `docs/published.txt`.
- Do not put calendar dates in public docs.
- Read and write text files with `encoding="utf-8"`. A test checks this.
- Do not commit data files, keys, tokens or `.env`. See `SECURITY.md`.
- Do not make a paid model call in a test. Tests use the committed replay and fixtures.
- Keep prose short and plain. Say what a thing does and where it stops.
- Add or change a test with the code it covers.

## Pull requests

- Keep each pull request to one change.
- Fill in the pull request template. Say how you checked the change.
- If you changed behaviour, add a line to `changelog.md` under the newest heading.

## Reporting problems

- A bug or a feature idea: use an issue template.
- A security problem: do not open an issue. Follow `SECURITY.md`.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are licensed under the MIT licence in `LICENSE`.
