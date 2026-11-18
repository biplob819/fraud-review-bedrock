# Write your own policy

The agent follows one file, `policy.md` at the repository root. It holds the rules the agent may cite and the router enforces. The shipped policy is illustrative. Its thresholds were set on simulated data, and a real team would set its own.

## The format

`policy.md` is CommonMark. Each clause is a heading, a plain sentence, and one `yaml` code block.

````markdown
## P7.1 Routine purchase in a usual category
A purchase in a category the card uses often, at a normal amount, may be cleared.

```yaml
id: P7.1
kind: clear
fields: [category_freq, amt_vs_usual]
when: category_freq >= 0.1 and amt_vs_usual < 2
```
````

- `id` is `P`, a number, a dot and a number, such as `P7.1`. It must match the heading.
- `kind` is one of three:
  - `signal`: evidence of fraud. A signal alone does not block a clear.
  - `clear`: a condition under which an alert may be cleared without a person. The agent can only clear by citing a clear clause that holds.
  - `no_clear`: a hard limit. If one holds, the alert always goes to a person.
- `fields` lists the alert fields the clause reads.
- `when` is one test on those fields.

## Fields you can use

| Field | Type |
| --- | --- |
| `amount` | number |
| `amt_vs_usual` | number |
| `category` | one of the 14 category names, in quotes |
| `category_freq` | number from 0 to 1 |
| `hour` | whole number from 0 to 23 |
| `merchant_first_seen` | true or false |
| `card_history_n` | whole number |
| `risk_score` | number from 0 to 1, from the baseline model |

## What a `when` test may be

- Compare a field with a constant or with another field: `amount >= 200`, `hour < 5`.
- Join tests with `and`, `or` and `not`.
- Write numbers as plain decimals with at most six decimal places.
- Keep it on one line, in ASCII, with no comments.

Anything else is refused when the policy loads. The test is parsed against a short allow-list of syntax and is never run with `eval`. A name that is not an alert field, or a comparison of mismatched types, is an error that names the clause.

## Check it before you run

```bash
uv run python -c "from fraudreview.policy import load_policy; load_policy(); print('policy ok')"
```

A bad policy stops with a message that names the clause and the problem. Run this check before every paid run. A live `fraud triage` loads the policy the same way before it makes any call.

## What a change does

- The policy has a version hash. Runs record it, so you can see which policy a result came from.
- Adding a `no_clear` clause can only make the agent clear less. Adding or loosening a `clear` clause can raise the auto-clear rate, and it can let a fraud through. Test a loosening on a development sample first, and read the leakage line in the run summary.
- The router also has two settings in `config.yaml`: `clear_ceiling` (no auto-clear at or above this risk score) and `clear_min_confidence`.
- In the published simulated results, one `no_clear` clause for night purchases is what brought the first failed run to zero leaks. It also cut the auto-clear rate, from 36.8% to 8.8% (simulated data). That is the trade.

See also [Control cost](cost.md) before you rerun after a policy change, since a new policy means new model calls.
