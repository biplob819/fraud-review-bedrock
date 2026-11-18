# B3.2 — PR/FAQ

**Wave 4. Depends on:** E2.3, B3.1.

## Goal
A two-page press release with a customer FAQ and an internal FAQ that tells the story in plain words and stays honest about the limits (US7, Trust architecture, NFR5).

## Outputs
- `docs/pr-faq.md`, a published file under the honesty gate, with `tests/test_prfaq.py` tying its numbers to `summary-eval2.json` and `eval-runs.jsonl`.

## Rules
- Every number carries the simulated or illustrative label. The failed first evaluation and the cost of the safety rule are stated.
