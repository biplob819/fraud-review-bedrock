# N1.2 acceptance criteria

**DAT-01 — loader**
- Credentials:
  - `kaggle_auth` returns a bearer header from `KAGGLE_API_TOKEN`, else from `~/.kaggle/access_token`, else a basic header from `~/.kaggle/kaggle.json`, else None.
  - A malformed `kaggle.json` (including one nested too deeply to parse), or one whose username or key is not text, raises `DataError`, as does a token, username or key with spaces, line breaks or control characters. The message never includes the credential.
  - A failed download is reported as a `DataError` naming only the error type, never the error text, which could carry header values.
  - The credential is an unredirected header, so a redirect to the storage host does not carry it.
- Download:
  - `download_source` downloads only when the pinned files are missing or changed.
  - It streams the archive to disk with a size limit and always removes the partial download.
  - It extracts exactly the pinned file names, never the archive's own paths.
  - Each file must match its pinned sha256, or nothing is kept for it; a file that matches is kept even if the other one fails, so the next run downloads again.
  - It refuses a body that is not a zip, a missing member, a member that is encrypted or compressed other than stored or deflated, a corrupt member (any failure while reading it), or an oversized member or download. No partial file is left behind.
- Every partial file `fraud load` writes (the download, extracted members, parquet files, the manifest) is created new and never follows a symlink.
- Credentials are read only when a download is needed, and any download error is reported by its type only.
- Loading:
  - `load` refuses source files that are missing, whose sha256 differs from the pin, or whose header differs from Sparkov's.
  - The pins equal those in the committed `docs/data/splits.json`.

**DAT-02 — minimisation**
- Stored splits hold exactly `trans_id, card_id, ts, merchant, category, amt, is_fraud`.
- No value is carried over from a dropped column, and no card or transaction number appears, in any stored value, the parquet metadata or the manifest (sha256 hashes in the manifest are hex digests, not data, and are outside the digit-run rule). A merchant name can contain a word that is also a surname, because Sparkov's merchant names are generated company names. Nothing that looks like a card number (12 to 19 digits in a run) appears either.
- Ids:
  - Ids are `t-` / `c-` plus 16 lowercase hex characters, stable across runs, one card id per card, and unique per transaction.
  - No id contains a run of 12 or more digits; the key is bumped and the hash taken again when the first hash has one.
  - `alert_id` matches the schema's `ALERT_ID`.
  - An id collision stops the load: two transactions or two cards with the same id, within a file or across both files.
- `minimise` checks every source field it keeps or hashes against the shape Sparkov writes (an allow-list):
  - card numbers are 11 to 19 ASCII digits;
  - transaction numbers are 32 lowercase hex characters;
  - merchant names after the prefix are ASCII letters first, then only letters, spaces and `' , . & -`, at most 100 characters. So a name holds no digit, no invisible or lookalike character, and is never blank;
  - timestamps are zero-padded `%Y-%m-%d %H:%M:%S` in ASCII digits;
  - categories are one of the 14 known ones;
  - amounts are dollars and cents as text (`[0-9]{1,7}` with up to two decimals), positive, and at most 1,000,000;
  - labels are the text `0` or `1`;
  - each of those fields is read as text from the CSV before it is checked;
  - each source file is hashed and parsed from the same bytes, read once.
  Anything else, and repeated transaction numbers, stop the load.

**DAT-03 — merchant prefix**
- When every row's merchant starts with `fraud_`, the prefix is removed from all of them.
- If the prefix is missing from any row, the load stops with the share of prefixed rows per label. That includes a prefix found only on fraud rows, and data with no prefix at all.
- A name that is not plain text after removing the prefix, including a blank one, stops the load (see DAT-02's merchant shape).
- On the real data the prefix is on every row (recorded in RUN-STATE and the data card).

**DAT-04 — splits and hashes**
- Splits:
  - train = rows of the train file before 2020-01-01; val = the rest of the train file; test = the test file.
  - Each split is sorted by time, and ties keep file order.
  - Overlapping periods, a transaction in both files, or an empty split stop the load.
- Hashes:
  - The content hash changes with any cell, the row order or a dropped row. Every text field is JSON-escaped, so a tab or newline inside a value cannot imitate a row boundary.
  - It is computed from the data, not the file bytes.
- Manifest and reading:
  - `splits.json` records the source hashes, the cut date, the id-key hash, the columns, and each split's rows, fraud count, fraud rate, cards, first and last time, and content hash. It is written after the parquet files.
  - No temporary files are left behind.
  - `read_split` refuses each of: a changed split, a changed recorded hash, a row count other than the recorded one (which must be a JSON integer), a null cell, any schema or field metadata (splits are written with none), an id or category not in its expected form, unexpected columns or types, a missing file or manifest, and an unknown split name.
- Reruns:
  - A rerun reproduces the manifest.
  - `compare_manifests` names every difference at any depth (a differing list is named as a whole): a key in only one manifest (even with a null value), an extra or missing split, and a value that differs in content or JSON type. `load` refuses a malformed reference.
  - `fraud load` exits 1, and writes no splits or manifest, when its result differs from the committed reference. It also exits 1 when a source hash differs.

**DAT-05 — data card**
- `DATA_CARD.md` names the Kaggle source, its CC0 licence and the Fraud Dataset Benchmark.
- It lists every dropped column and what is kept.
- It gives a split table whose period, rows, fraud count, fraud rate, cards and short hash match `docs/data/splits.json`, along with both source hashes.
- It has sections on simulation caveats and on what real data would change.
- It is listed in `docs/published.txt`, and the honesty gate passes on it.
- No `.csv`, `.parquet` or `.zip` file is tracked by git.
