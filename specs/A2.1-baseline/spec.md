# A2.1 — Features, baseline, alerts and SHAP

**Wave 2. Depends on:** N1.2. **Unblocks:** A2.3 (stage 1 needs alert cases), E2.1 (metrics need the baseline), E2.2 (driver agreement needs SHAP).

## Goal
Turn the splits into what the agent triages. Each transaction gets point-in-time card features. A LightGBM baseline scores every transaction, and the D-9 threshold turns scores into alerts. Seeded samples of 100 validation alerts (dev) and 500 test alerts (eval) are drawn, and each sampled alert gets its top five SHAP drivers, kept apart from anything a model sees (FR2–FR4, D-7, D-8, D-9, C2, C4, H5, H6).

## Outputs
- `src/fraudreview/features.py`
  - `build_features(splits)` computes, for every transaction and from that card's earlier transactions only (BAS-01):
    - `amt_vs_usual`: amount ÷ median of prior amounts; null under 3 prior;
    - `hour`;
    - `category_freq`: share of prior transactions in this category; 0 with no history;
    - `merchant_first_seen`: no prior transaction by this card at this merchant;
    - `card_history_n`: number of prior transactions.
  - "Earlier" is the order of `(ts, split order, row order)`: train, then val, then test, each in time order. History therefore carries across splits, which is legitimate because validation and test come later in time.
- `src/fraudreview/stats.py`: `wilson(k, n)` gives a 95% interval as a `Rate`; E2.1 extends this module.
- `src/fraudreview/baseline.py`
  - Model inputs (`MODEL_FEATURES`): `amount`, `category`, `hour`, `amt_vs_usual`, `category_freq`, `merchant_first_seen`, `card_history_n`. All are allow-listed `FeatureName`s.
  - `train(...)`: LightGBM, deterministic and single-threaded, seeded from config. It trains on train with early stopping on val, and the model text is hashed (BAS-02).
  - `choose_threshold(scores, labels)` applies D-9 on validation. It picks the highest threshold that keeps recall ≥ 98%. If precision there is above 50%, it lowers the threshold until at most half of alerts are fraud, and logs that it did (BAS-03).
  - Inside `run_baseline`, alerts are rows where score ≥ threshold. Ids come from `alert_id(trans_id)`. `sample` draws seeded samples: dev is 100 from val alerts, eval is 500 from test alerts (BAS-04).
  - `shap_values` and `drivers` compute exact TreeSHAP contributions from LightGBM's `pred_contrib`, which a test checks against SHAP's `TreeExplainer`, on the sampled alerts. They keep the top five features by absolute value, with sign, into `AlertTruth` only (BAS-05).
  - `run_baseline(data_dir, out_dir, seed)` writes:
    - `model.txt`;
    - `baseline.json`, holding the `BaselineSummary`, the threshold choice and its reason, the split hashes it trained on, the model hash, and the alert mix;
    - per sample, three files: `cases_{dev,eval}.jsonl` (`AlertCase`, the only thing agent code reads), `truth_{dev,eval}.jsonl` (`AlertTruth`) and `alerts_{dev,eval}.jsonl` (the analyst `Alert` view).
- `fraud baseline [--data-dir] [--out]` runs the steps above.

## Rules
- No feature reads a later transaction. A property test checks that cutting the history at any point leaves every earlier row's features unchanged.
- Alerts and samples are deterministic for a given config seed. Each case's `risk_score` is the model score, and `ts_hour` and `ts_dow` come from the transaction time.
- Split hashes are checked through `read_split`, and recorded in `baseline.json`.
- `results/` is git-ignored. Recorded numbers carry the simulated label wherever they are published.

## Out of scope
Model calls (A2.3/A2.4), metrics and intervals beyond the baseline (E2.1), the owner sign-off of threshold and alert mix (asked after this node).
