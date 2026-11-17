# A2.5 — Batch triage run

**Wave 2. Depends on:** A2.4. **Unblocks:** E2.3 (summary), E2.4 (monitoring and improvement), B3.3 (architecture).

## Goal
Run the agent over the dev and eval samples with progress, running cost, the spend cap and resume. Log every dev run with its result. Freeze the settings before the eval run, and make the eval run refuse anything else. Record a 20-alert golden fixture that replays with no model (US1, US2, C2, E2, NFR3).

## Outputs
- `src/fraudreview/run.py`:
  - `run_triage(cases, ctx, out_dir, run_id)`: triages with at most `concurrency` in flight and keeps input order. The spend cap stops scheduling (exit 3), and finished work stays cached. A rerun is the resume. Files are written in one place after the workers finish: `decisions.jsonl`, `dispositions.jsonl`, the hash-chained `audit.jsonl` and `run.json` (counts, billed spend and calls, audit head, freeze record, error counts) (RUN-01).
  - `freeze_record` and `freeze_problems`. `fraud freeze` writes `docs/measurements/freeze.json`: provider, policy version, prompt hashes, models, efforts, output limits, router settings, seed, code versions and `logic_sources` (source hashes of the router, the clause evaluator and the record validators), with a digest. `fraud triage --split eval` exits 2 if anything differs or nothing is frozen (RUN-03).
  - `dev_log_line`: after every dev run, one line in `docs/measurements/dev-runs.jsonl` holds the settings digest, leakage, auto-clear rate and precision, queue mix and cost (RUN-02).
  - `record_golden`: a finished run becomes `tests/fixtures/golden/`. Recorded replies go into a store under their real cache keys, with request ids dropped. The fixture also holds the cases, their labels and the expected dispositions (RUN-05).
- `fraud triage --split dev|eval [--alerts N] [--dry-run] [--record-golden DIR]` prints the plan, progress every 10 alerts with spend so far, and a summary. It writes one spend-ledger line per run, even when the run raises.
- `stages.stage_key` exposes the cache key so the golden fixture uses the same keys.
- Live:
  - the 100-alert dev run (estimate $1.34, under the $5 rule);
  - the 20-alert golden fixture, taken from the dev run's cache at no cost;
  - the freeze.
- The 500-alert eval run (RUN-04) needs the owner's approval first. Its estimate (~$6.68) is over the $5 rule and over `max_cost_usd`.

## Rules
- Workers write only cache entries; one writer writes the run folder after the run.
- A rerun after a cap stop reuses every finished call from the cache.
- The golden replay runs with no client: any model call fails it.
