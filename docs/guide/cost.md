# Control cost

A triage run makes paid model calls. Three things keep that under your control: the dry run, the spend cap and the ledger. The figures below come from the simulated test runs and are only a guide.

## 1. Dry run

```bash
uv run fraud triage --split dev --alerts 100 --dry-run
```

A dry run makes no model call and needs no key. It prints the number of calls for each stage and an estimate of the cost, then stops. For a 100-alert run in the simulated set the estimate is about $1.34 (simulated data), and the real figure for a published 500-alert run was about $6.24 per 1,000 alerts (simulated data). Your figure depends on your models, prices and alert text.

Prices come from `prices.yaml`. Check them against your provider's price page, since they change.

## 2. Spend cap

The cap is `max_cost_usd` in `config.yaml`. The shipped value is $5 per run (simulated runs; set your own).

- If the estimate is over the cap, the run refuses to start and tells you to lower `--alerts` or raise the cap.
- While running, calls are reserved against the cap before they are sent. If the cap is reached, the run stops cleanly, prints a message and exits with code 3.
- Finished calls are cached, so a rerun resumes from the cache and pays only for what is left.

There is no `--max-cost-usd` flag. Change the number in `config.yaml`, which also keeps the cap in version control. A lower cap is the safe way to try a new provider or policy.

`fraud judge`, the paid faithfulness check, has its own `--cap-usd` flag. Its cap is a total across every judging run, read from the ledger, so a renamed run folder cannot start a fresh cap. Use `fraud judge --dry-run` first.

## 3. Ledger

Every real run appends one line to `docs/measurements/spend-ledger.jsonl`: run id, provider, split, number of calls, the estimate and the actual cost. It holds no key, account or request id. Add up the actual cost column to see what you have spent. The repository's own ledger is committed, so you can see what the published runs cost.

## Habits that save money

- Start with 20 or 100 alerts, not the whole file.
- Dry run after every change to the policy, the prompts or the model.
- Keep the cache folder (`.fraud/`). A repeat of an identical call costs nothing.
- Run the replay demo and `fraud report --example` while you read the code. They call nothing.
- Set a spend limit with your provider as well. The cap here is one safeguard, not the only one.
