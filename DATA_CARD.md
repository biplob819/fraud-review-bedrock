# Data card

Every number on this page describes simulated data. The data is not from a bank, a card network or any real person.

## Source

- **Dataset:** Sparkov credit-card transactions, published on Kaggle as [`kartik2112/fraud-detection`](https://www.kaggle.com/datasets/kartik2112/fraud-detection) under the CC0 public-domain licence. It was generated with the Sparkov Data Generation tool by Brandon Harris.
- **Why this one:** it is the `sparknov` dataset in Amazon's [Fraud Dataset Benchmark](https://github.com/amazon-science/fraud-dataset-benchmark) (FDB), so results can be compared with published baselines.
- **How it is loaded:** `fraud load` downloads the same Kaggle files FDB uses, directly. The FDB package is not installed. FDB shifts every timestamp forward by 20 months, which is cosmetic, and this project keeps the original times (D-30).
- **Pinned files:** the two source files are checked against these sha256 hashes before anything is read. If Kaggle ever serves different bytes, the load stops instead of quietly producing different numbers.
  - `fraudTrain.csv`: `fd7139200dbfcbed0b6742bbe05a4f1abce532c4fef20918228a651647a3e75d`
  - `fraudTest.csv`: `12d553ab19440c752d2531ee1af44bb64f12cc3d3839f1649f19e81c230545f0`
- **Credentials:** the Kaggle token is read from `KAGGLE_API_TOKEN`, `~/.kaggle/access_token` or `~/.kaggle/kaggle.json`. It is never printed, and it is sent to Kaggle only, never to the storage host Kaggle redirects to.
- **Nothing is committed:** no data file is committed to this repository; `data/` is git-ignored. `docs/data/splits.json` holds only counts and hashes.

## What is kept

| Column | Meaning |
| --- | --- |
| `trans_id` | Opaque transaction id: `t-` plus 16 hex characters of a keyed sha256 of Sparkov's `trans_num` |
| `card_id` | Opaque card id: `c-` plus 16 hex characters of a keyed sha256 of `cc_num` |
| `ts` | Transaction time as simulated (no time zone; read as the card holder's local time) |
| `merchant` | Merchant name with Sparkov's `fraud_` prefix removed |
| `category` | One of 14 spending categories |
| `amt` | Amount in US dollars |
| `is_fraud` | The simulated label, 1 for fraud |

**Dropped and never stored:** `first`, `last`, `street`, `city`, `state`, `zip`, `lat`, `long`, `merch_lat`, `merch_long`, `dob`, `job`, `gender`, `city_pop`. Also not stored:
- the card and transaction numbers, which are replaced by the ids above;
- Sparkov's row index;
- `unix_time`, which is the same instant as `ts` moved back seven years.

**Ids:**
- The id key is public because the source is public. It makes ids repeat exactly across machines, but it does not hide anything from someone who has the source file. The ids exist so prompts and screens never show a card number.
- When a hash's first 16 characters hold a run of 12 or more digits, the key is bumped, so no id can look like a card number.
- Alert ids (`a-...`) are built the same way from the transaction id.

**The `fraud_` prefix:** Sparkov puts `fraud_` in front of every merchant name, fraud or not. That was checked on all 1,852,394 simulated rows. Left in place, the word would push a language model toward "fraud". The prefix is removed. The load stops if any row lacks it, because a prefix that appeared on only some rows could give the label away.

## Splits

The splits are cut by time (D-8):
- the source's train file is cut at 1 January 2020 into **train** and **validation**;
- the source's test file is the **test** period.

| Split | Period | Rows | Fraud | Fraud rate | Cards | Content hash |
| --- | --- | --- | --- | --- | --- | --- |
| train | 2019-01-01 to 2019-12-31 | 924,850 | 5,220 | 0.564% simulated | 960 | `ba7bc247a964` |
| val | 2020-01-01 to 2020-06-21 | 371,825 | 2,286 | 0.615% simulated | 931 | `bc79cfe41893` |
| test | 2020-06-21 to 2020-12-31 | 555,719 | 2,145 | 0.386% simulated | 924 | `9bcbd8083c4d` |

**How the splits are checked:**
- Each content hash is a sha256 over a fixed text form of the split, row by row in time order, so it does not depend on the file format or library versions. The full hashes are in `docs/data/splits.json`.
- `fraud load` refuses to finish if its splits differ from that file.
- Every later step reads data through `read_split`, which refuses a split whose content no longer matches its hash.

**How later steps use the splits:**
- The baseline model trains on train and picks its alert threshold on validation.
- Prompt and policy tuning use 100 alerts from the validation period.
- Reported results use 500 alerts from the test period.

## Simulation caveats

- **Generated behaviour:** the transactions come from a simulator with a fixed set of customer profiles, not from real spending. Patterns are cleaner and more regular than in real card data, so models score better here than they would in production.
- **Fixed fraud pattern:** the simulator injects fraud in recognisable bursts. In train, a compromised card's fraud usually lasts under two days and about ten transactions; in-store grocery and online shopping together make up 46.0% of simulated fraud in train. 84.6% of simulated fraud in train falls between 22:00 and 03:59, against 23.5% of all simulated transactions. Real fraud adapts to the controls that catch it; this data does not.
- **Small, closed card population:** about 1,000 simulated cards. 908 of the 924 cards in the test period also appear in train, so the test measures drift over time, not new customers.
- **Rate drift:** the fraud rate moves between periods; see the table. Test has a lower rate than train and validation, so precision on test is likely to be lower at the same threshold.
- **No disputes or delays:** labels are known immediately and are always right. Real labels arrive weeks later through chargebacks and are sometimes wrong.
- **Few columns:** there is no device, channel, IP, merchant risk or authorisation data, which real fraud models depend on.

## What real data would change

- **Privacy:** card numbers would be tokenised by the card processor before reaching this system, and the id key would be a secret held in a key manager, never in code. Location and demographic fields would need a documented purpose and a fairness review before any use.
- **Labels:** results would need a label-maturity window (for example 60 to 90 days for chargebacks) and a plan for analyst-decided labels, which are partly shaped by the model being evaluated.
- **Features and thresholds:** the feature set, the baseline model and the policy thresholds would all be re-tuned on the institution's own data, and the router ceilings re-checked against its loss tolerance.
- **Monitoring:** fraud patterns shift. Drift checks on the alert mix and the auto-clear rate would run continuously, not once.
- **Approvals:** a model-risk review, data-sharing agreements and a written human-in-the-loop policy would come before any pilot.
