# A2.1 acceptance criteria

**BAS-01 — point-in-time features**
- For every transaction, `amt_vs_usual`, `category_freq`, `merchant_first_seen` and `card_history_n` are computed from that card's earlier transactions only. "Earlier" means earlier in the documented `(ts, split, row)` order, so of two transactions on one card in the same second, the one stored first counts as history for the other (44 such pairs in the real data). `hour` is the transaction's own hour.
- Truncation property: computing features on any prefix of the ordered history gives exactly the same values for the rows in that prefix. So changing, adding or removing a later transaction changes no earlier row's features.
- Exact values on hand-made histories:
  - `amt_vs_usual` is null with fewer than 3 prior transactions, and otherwise amount ÷ median of the prior amounts;
  - `category_freq` is 0 with no history;
  - the first transaction at a merchant has `merchant_first_seen` true, and later ones have it false;
  - `card_history_n` counts the prior transactions.
- Every built case validates as a `Features` model.

**BAS-02 — baseline model**
- LightGBM is trained on the train split with early stopping on validation.
- Its inputs are exactly `MODEL_FEATURES`, all of which are allow-listed field names. Neither the label nor any id is an input.
- Training twice with the same seed and data gives byte-identical model text, and that text's sha256 is recorded.

**BAS-03 — threshold (D-9)**
- `choose_threshold` returns the highest score threshold whose validation recall is ≥ 0.98.
- If alert precision at that threshold is above 0.5, it lowers the threshold until precision is ≤ 0.5, and records that, why, and the threshold before lowering. The case where precision is already ≤ 0.5 is recorded too.
- `baseline.json` records the threshold together with validation recall and precision, and test recall and precision as `Rate`s with Wilson 95% intervals. It also records the test alert rate.

**BAS-04 — alerts and samples**
- Alerts are exactly the transactions whose score is ≥ the threshold. Their ids are `alert_id(trans_id)` and each is unique.
- The dev sample is 100 alerts from the validation period and the eval sample is 500 alerts from the test period. Both are drawn with the config seed: the same seed gives the same samples, and a different seed gives different ones.
- The two samples share no alert.
- `baseline.json` reports the alert mix (count and fraud share) for each sample and for all alerts in each period.
- Each line of `cases_*.jsonl` is an `AlertCase`:
  - `risk_score` is the model score;
  - `ts_hour` and `ts_dow` come from the transaction time;
  - it holds no label, SHAP value, merchant, card or transaction id.

**BAS-05 — SHAP top five, apart from cases**
- Each sampled alert's `AlertTruth` holds its label and the top five features by absolute SHAP value. Each is stored with its sign (positive pushes toward fraud) and in descending order of absolute value.
- The drivers agree with LightGBM's own contribution output for that row.
- Truth and analyst files are written separately from the case files. No case file contains `label`, `shap` or `contribution`.
- `fraudreview.schemas` and `fraudreview.config` do not import `fraudreview.truth`, which a test checks. The ban on agent code importing `fraudreview.truth` or `fraudreview.baseline` is added with the agent code in A2.3.
