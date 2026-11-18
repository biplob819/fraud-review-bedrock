## What this changes

<!-- One change per pull request. Link the issue if there is one. -->

## How you checked it

- [ ] `uv run ruff format --check .` and `uv run ruff check .`
- [ ] `uv run pyright`
- [ ] `uv run pytest -q`
- [ ] `uv run python scripts/honesty_check.py`

## Checklist

- [ ] Every number in a published file says "simulated" on the same line
- [ ] No keys, tokens, `.env` or data files are added
- [ ] No calendar dates in public docs
- [ ] The agent still has no tool that can block a card or contact a customer
- [ ] I added or updated a test, or the change needs none
- [ ] I added a line to `changelog.md` if behaviour changed
