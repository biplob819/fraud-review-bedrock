# Bring your own alert data

The agent reads alerts from a JSONL file, one alert per line. It sees only the fields below. Names, addresses, card numbers, dates of birth and locations are not fields, and the loader refuses them.

## The alert format

```json
{"alert_id": "a-0001", "ts_hour": 23, "ts_dow": 3, "category": "health_fitness", "amount": 19.64, "features": {"amt_vs_usual": 0.51, "hour": 23, "category_freq": 0.075, "merchant_first_seen": false, "card_history_n": 1436}, "risk_score": 0.043}
```

- `alert_id` is an opaque id you choose. Do not use a card or account number. Ids must be unique in a file.
- `ts_hour` is the local hour, 0 to 23. `ts_dow` is the day of the week, 0 to 6. `features.hour` must equal `ts_hour`.
- `category` is one of the 14 category names in `DATA_CARD.md`. Map your own merchant categories onto them, or extend the schema in `src/fraudreview/schemas.py` and the policy fields together.
- `amount` is the purchase amount.
- `features` holds `amt_vs_usual` (the amount divided by the card's usual amount, or null), `hour`, `category_freq`, `merchant_first_seen` and `card_history_n`. Compute them from past activity only, never from anything after the alert. Leaking the future makes any result look better than it is.
- `risk_score` is your model's score from 0 to 1. The shipped baseline is a LightGBM model (`fraud baseline`). You can supply your own.

The committed example is `tests/fixtures/golden/cases.jsonl`.

## Run on it

The triage command looks for `cases_<split>.jsonl` in the folder given by `--cases`. The split name is one of `dev`, `eval` or `eval2`.

```bash
mkdir my-alerts
cp my-alerts.jsonl my-alerts/cases_dev.jsonl
uv run fraud triage --split dev --cases my-alerts --dry-run
uv run fraud triage --split dev --cases my-alerts
```

Always do the dry run first. See [Control cost](cost.md).

Each run writes a folder under `results/` with the decisions, the queue assignments and a hash-chained audit log. Every alert ends in exactly one place: auto-cleared, held or escalated.

## Labels, if you have them

To measure how often the agent clears a real fraud, add `truth_<split>.jsonl` beside the cases, with the label and the baseline's top drivers for each alert (see `tests/fixtures/golden/truth.jsonl`). Then:

```bash
uv run fraud evaluate --run results/<run> --cases my-alerts/cases_dev.jsonl --truth my-alerts/truth_dev.jsonl
```

Labels are kept apart from what the model sees. They are used only for scoring after the run.

## Caution

The shipped policy and thresholds were tuned on simulated data. On your data they will be wrong in ways you cannot predict. Write your own policy (see [Write your own policy](policy.md)), test on a development sample, and treat every result as a draft until people who know your alerts have reviewed it. This project is not validated for real use.

If you use real alert data, read the [privacy page](privacy.md) first: the alert fields go to the model provider you configure.
