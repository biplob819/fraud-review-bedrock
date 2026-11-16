# N1.2 — Data load, minimisation and splits

**Wave 1. Depends on:** N1.1. **Unblocks:** A2.1 (features, baseline, alerts), and through it E2.1.

## Goal
Load the simulated Sparkov data the Fraud Dataset Benchmark uses, keep only what the pipeline needs, and cut it into time-ordered train, validation and test splits whose content is pinned by hash, so every later number traces back to exactly these rows (D-8, D-30, H4, H6, NFR2, NFR5).

## Outputs
- `src/fraudreview/data.py`:
  - `download_source(raw_dir)` downloads the Kaggle archive (`kartik2112/fraud-detection`), extracts only the two pinned files by name, with size limits, and checks their sha256 (DAT-01).
  - `kaggle_auth()` reads the token from `KAGGLE_API_TOKEN`, `~/.kaggle/access_token` or `~/.kaggle/kaggle.json`; the token goes in an unredirected header and is never printed (DAT-01).
  - `minimise(raw)` validates the source columns and values, drops the §2.2 columns, the row index and `unix_time`, and replaces `cc_num` and `trans_num` with opaque keyed-hash ids (DAT-02).
  - `opaque_id` and `alert_id` follow the rule in §2.2: 16 hex characters, with the key bumped past any run of 12 digits (DAT-02).
  - The `fraud_` merchant prefix is checked on every row and stripped (DAT-03).
  - `time_splits` cuts the train file at `VAL_START` (2020-01-01) into train and validation, keeps the test file as test, and sorts each split stably by time (DAT-04).
  - `content_hash`, `write_splits`, `read_split` and `compare_manifests` handle the split hashes (DAT-04):
    - each split's content hash is computed from a fixed text form, so it does not depend on the file format;
    - splits are written as parquet with `splits.json`, which is written last;
    - `read_split` refuses a split whose content no longer matches.
  - `load(raw_dir, data_dir)` runs the steps above.
- `fraud load [--raw-dir] [--data-dir] [--offline]` checks its manifest against the committed `docs/data/splits.json` and exits 1 on any difference.
- `docs/data/splits.json`: counts and hashes only, no data.
- `DATA_CARD.md` covers the source, licence and credit, what is kept, the split table, simulation caveats, and what real data would change (DAT-05). It is listed in `docs/published.txt`, so the honesty gate checks every number in it.

## Rules
- No data file is committed; `data/` is git-ignored.
- Any surprise in the source stops the load with a `DataError` naming the problem:
  - a changed hash or header;
  - an unknown category;
  - a non-positive or non-finite amount;
  - a label other than 0 or 1;
  - a bad timestamp;
  - a repeated transaction number;
  - a prefix that is not on every row;
  - overlapping periods;
  - an empty split;
  - an id collision.
- The id key is public, because the source is public. The data card says what changes with real data.

## Out of scope
Features, the baseline, alerts and the dev and eval samples (A2.1); Bedrock (N1.3).
