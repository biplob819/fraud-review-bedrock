# N1.2 run state

**Now:** green

## History
- 2026-11-16 — source profiled:
  - Kaggle `kartik2112/fraud-detection`, CC0.
  - `fraudTrain.csv` has 1,296,675 rows (2019-01-01 to 2020-06-21); `fraudTest.csv` has 555,719 rows (2020-06-21 to 2020-12-31).
  - No nulls and no repeated `trans_num`; 14 categories.
  - The `fraud_` merchant prefix is on 100% of rows for both labels (simulated data), so stripping it is safe.
  - FDB's `sparknov` preprocessor reads the same two files and shifts timestamps by 20 months; this project keeps the original times (D-30).
- 2026-11-16 — spec, criteria and tasks written. Tests first, then `data.py` and `fraud load`.
- 2026-11-16 — real run:
  - A fresh download plus `fraud load` reproduces `docs/data/splits.json` byte for byte. Train 924,850 rows, val 371,825, test 555,719 (simulated).

- 2026-11-16 — converge passed. Verifier pass 1 confirmed DAT-03 and disproved the other four rows:
  - DAT-01: a token holding a line break reached `http.client`, whose error printed it.
  - DAT-02: card-id collisions were not checked.
  - DAT-04: unescaped tabs and newlines in id fields let a crafted row stand in for a dropped row under the same hash.
  - DAT-05: the fraud-span caveat said "about one day"; the real median is 1.88 days.
- 2026-11-16 — fixes:
  - Credentials with spaces, line breaks or control characters are refused, and the message leaves the credential out. A failed download is reported by error type only.
  - Ids are checked one-to-one, for cards and transactions, within each file and across both.
  - The content hash JSON-encodes every field. `read_split` also checks the row count and the forms of ids and categories.
  - A corrupt archive member and every failed write leave no partial files.
  - Timestamps must be zero-padded, and blank ids are refused.
  - `fraud load` writes nothing when its result differs from the reference.
  - The data card now says "under two days".
  - The split hashes were regenerated (new canonical form; the same rows).

- 2026-11-16 — verifier pass 2 confirmed DAT-02 to DAT-05 and disproved DAT-01: a member whose deflate stream was corrupt raised `zlib.error` and left a `.part` file. Fixed: decompression errors are a `DataError` and the partial file is removed. Also applied:
  - an undecodable token file is a `DataError`;
  - `read_split` refuses null cells;
  - labels must be the text 0 or 1;
  - the data card gives the 46.0% grocery-plus-online share (simulated) and scopes the night-time figures to train.

- 2026-11-16 — verifier pass 3 confirmed DAT-02 to DAT-05 and disproved DAT-01: a corrupt LZMA member raised `LZMAError` and left a `.part` file. This was the same class as pass 2, so the class is now closed rather than the one type:
  - Only stored or deflated, unencrypted members are read; that is what Kaggle serves, confirmed by a fresh real download.
  - Any exception while reading a member becomes a `DataError`, and the partial file is removed.
  - The partial file is created with `O_EXCL|O_NOFOLLOW`, so a planted symlink is never followed.
  - `kaggle.json` values must be text.
  - A corrupt parquet file or reference manifest is a `DataError`.
  - The harness encoding check now skips `os.open`, which returns a file descriptor and has no text layer.

- 2026-11-16 — verifier pass 4 confirmed DAT-02 to DAT-05 and disproved DAT-01: the download's own partial file was written with a plain open and could follow a symlink. Fixed: every partial file (download, members, parquet files, manifest) is created with `O_EXCL|O_NOFOLLOW`. Also applied:
  - `read_split` ignores the file's pandas metadata (index and types) and refuses any other metadata key;
  - `minimise` refuses a merchant name with a 12-digit run, non-ASCII digits in timestamps, ids that are not text, and boolean amounts or labels;
  - a malformed reference manifest is refused, not skipped;
  - any download error is reported by type only;
  - the Kaggle credentials are read only when a download is needed.

- 2026-11-16 — verifier pass 5 confirmed every row's behaviour on the real data. Three criteria sentences were false in contrived cases (field-level metadata, booleans in an object column, non-ASCII digit runs in merchant names). Fixed:
  - splits are written with no metadata, and `read_split` refuses any schema or field metadata;
  - booleans are refused whatever the column type;
  - digit runs are checked in any script after NFKC normalisation;
  - blank means no letter or digit, so zero-width characters alone count as blank;
  - `compare_manifests` also compares the version and data label;
  - `fraud load` says when there is no reference to compare with.

- 2026-11-17 — the repo moved to a new local folder at the owner's request; the environment was rebuilt and all gates pass there.
- 2026-11-17 — verifier pass 6 confirmed all five rows on the real data. One borderline must-fix: a merchant name made only of invisible characters after the prefix was accepted. Fixed:
  - the letter-or-digit rule now applies to the name after the prefix is removed;
  - digit runs are checked with invisible format characters removed;
  - a `kaggle.json` username holding `:` is refused;
  - the data card's test-precision sentence now says "likely".

- 2026-11-17 — verifier pass 7 confirmed DAT-01, 03, 04 and 05. On DAT-02, combining marks and variation selectors could still split a digit run in a merchant name: the third finding of this class.
  - Closed it per L-28: every source text field is now checked against an allow-list of the shape Sparkov writes. Merchant names allow ASCII letters, spaces and `' , . & -` only; the real data uses exactly letters, spaces and `' , -`.
  - Amounts are capped at 1,000,000 (the real maximum is 28,948.90).
  - A member's partial file is removed in a `finally`, so interrupts and a failed rename are covered.
  - An undecodable split value is a `DataError`.
  - `fraud load` names any unexpected error without a traceback.

- 2026-11-17 — verifier pass 8 confirmed DAT-01, 03, 04 and 05. On DAT-02, a crafted amount such as `0.000630423337322` could carry a card number. Fixed per L-28:
  - amounts and labels are read as text and allow-listed: dollars and cents, and `0` or `1`;
  - `compare_manifests` names every differing key;
  - the whitespace card-number item is closed, since the allow-list refuses whitespace.

- 2026-11-17 — verifier pass 9 confirmed DAT-01, 02, 03 and 05; all the allow-list fixes held. On DAT-04, `compare_manifests` missed an extra split and keys whose value is null. Fixed:
  - the comparison is now a recursive diff over key sets, type-strict through JSON;
  - a CSV row with more fields than the header is a `DataError` instead of becoming a shifted index.

- 2026-11-17 — verifier pass 10 confirmed DAT-02 to DAT-05. On DAT-01, a deeply nested `kaggle.json` raised `RecursionError`. Fixed:
  - every JSON read (`kaggle.json`, `splits.json`, the reference) catches `RecursionError` as a `DataError`;
  - each source file is now hashed and parsed from one read, which closes the gap between checking and reading;
  - the criteria wording now says "fields it keeps or hashes" and "`load` refuses a malformed reference".

- 2026-11-17 — verifier pass 11: no MUST-FIX; all five rows confirmed on the real data (byte-identical manifest, every data-card figure recomputed). Applied its cheap should-fixes: the recorded row count must be a JSON integer, and the criteria wording now covers hashes, lists and keeping one good file. Green.

## Open should-fix (carried forward)
- A wheel install has no `docs/data/splits.json`, so `fraud load` skips the reference check (with a notice); the README should say so (D4.3).
- `read_split` checks the run's own `splits.json`, not the committed reference. A deliberate rewrite of both a split and its manifest is caught only by `fraud load` against `docs/data/splits.json`. A2.1 should record the split hashes it trained on in its own outputs.
