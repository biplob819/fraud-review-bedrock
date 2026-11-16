"""Data load, minimisation and time splits (Tech Spec §2.2, D-8, D-30, H4, H6).

The source is Sparkov, a simulated card-transaction set: the Kaggle dataset
`kartik2112/fraud-detection`, which is what the Fraud Dataset Benchmark's `sparknov` key
downloads. Its two files are pinned by sha256, so a changed upload is refused rather than
silently giving different numbers.

Minimisation keeps only what the pipeline needs: an opaque transaction id, an opaque card id,
the timestamp, the merchant name with Sparkov's `fraud_` prefix removed, the category, the
amount and the label. Names, addresses, coordinates, dates of birth, jobs, gender, city
population, card numbers and transaction numbers never leave this module.

Splits follow D-8: the source's train file is cut by date into train and validation, and the
source's test file is the test period. Each split's content hash is recorded in
`splits.json`; `read_split` refuses a split whose content no longer matches.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import os
import re
import warnings
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import IO, Any, get_args
from urllib.request import Request, urlopen

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fraudreview.schemas import DIGIT_RUN, Category

SOURCE = "kartik2112/fraud-detection"
SOURCE_URL = f"https://www.kaggle.com/api/v1/datasets/download/{SOURCE}"
SOURCE_SHA256 = {
    "fraudTrain.csv": "fd7139200dbfcbed0b6742bbe05a4f1abce532c4fef20918228a651647a3e75d",
    "fraudTest.csv": "12d553ab19440c752d2531ee1af44bb64f12cc3d3839f1649f19e81c230545f0",
}
MAX_MEMBER_BYTES = 1 << 30  # 1 GiB per extracted file; the real files are 351 MB and 150 MB

RAW_COLUMNS = (
    "",  # the row index Sparkov wrote
    "trans_date_trans_time",
    "cc_num",
    "merchant",
    "category",
    "amt",
    "first",
    "last",
    "gender",
    "street",
    "city",
    "state",
    "zip",
    "lat",
    "long",
    "city_pop",
    "job",
    "dob",
    "trans_num",
    "unix_time",
    "merch_lat",
    "merch_long",
    "is_fraud",
)
# Tech Spec §2.2: personal and location data never kept
DROPPED = (
    "first",
    "last",
    "street",
    "city",
    "state",
    "zip",
    "lat",
    "long",
    "merch_lat",
    "merch_long",
    "dob",
    "job",
    "gender",
    "city_pop",
)
# also not kept: the row index, and unix_time (the same instant as the timestamp, shifted by
# seven years); cc_num and trans_num are replaced by opaque ids
COLUMNS = ("trans_id", "card_id", "ts", "merchant", "category", "amt", "is_fraud")
SCHEMA = pa.schema(
    [
        ("trans_id", pa.string()),
        ("card_id", pa.string()),
        ("ts", pa.timestamp("us")),
        ("merchant", pa.string()),
        ("category", pa.string()),
        ("amt", pa.float64()),
        ("is_fraud", pa.int8()),
    ]
)
SPLITS = ("train", "val", "test")
VAL_START = datetime(2020, 1, 1)  # D-8: the train file's last months are validation
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
TS_SHAPE = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"  # zero-padded, as Sparkov writes it
)
TRANS_ID = r"t-[0-9a-f]{16}"
CARD_ID = r"c-[0-9a-f]{16}"
MERCHANT_PREFIX = "fraud_"
ID_SALT = b"fraud-review-bedrock/ids/v1"  # public: the source is public (see DATA_CARD.md)
MANIFEST = "splits.json"
MANIFEST_VERSION = "1.0"
CATEGORIES = frozenset(get_args(Category))


class DataError(Exception):
    """The source data or a stored split is not what this module expects."""


# ---------------------------------------------------------------- opaque ids


def opaque_id(prefix: str, value: str, salt: bytes = ID_SALT) -> str:
    """`prefix` plus the first 16 hex characters of a keyed sha256 of `value`. When those
    contain a run of 12 or more digits (about 1 in 113), the salt is bumped and the hash taken
    again, so no id can look like a card number."""
    for bump in count():
        key = salt if bump == 0 else salt + b"#" + str(bump).encode("ascii")
        digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        if not DIGIT_RUN.search(digest):
            return prefix + digest
    raise AssertionError("unreachable")  # pragma: no cover


def alert_id(trans_id: str, salt: bytes = ID_SALT) -> str:
    """The alert id for a transaction id (A2.1 uses this when it raises alerts)."""
    return opaque_id("a-", trans_id, salt)


# ---------------------------------------------------------------- download


def kaggle_auth(env: Mapping[str, str] | None = None, home: Path | None = None) -> str | None:
    """The Authorization header value for Kaggle, or None. Looks, in order, at
    KAGGLE_API_TOKEN, ~/.kaggle/access_token (both bearer tokens), then ~/.kaggle/kaggle.json
    (username and key). The value is never printed."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    token = env.get("KAGGLE_API_TOKEN", "").strip()
    if not token:
        path = home / ".kaggle" / "access_token"
        try:
            token = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except UnicodeDecodeError:
            raise DataError(f"{path}: not UTF-8 text") from None
    if token:
        _check_credential(token, "the Kaggle token")
        return f"Bearer {token}"
    legacy = home / ".kaggle" / "kaggle.json"
    if legacy.is_file():
        try:
            creds = json.loads(legacy.read_text(encoding="utf-8"))
            user, key = creds["username"], creds["key"]
            if not (isinstance(user, str) and isinstance(key, str)):
                raise TypeError("username and key must be text")
        except (ValueError, KeyError, TypeError, RecursionError):
            raise DataError(f"{legacy}: expected JSON with text 'username' and 'key'") from None
        _check_credential(user, f"{legacy} username")
        if ":" in user:
            raise DataError(f"{legacy}: the username may not contain ':'")
        _check_credential(key, f"{legacy} key")
        pair = f"{user}:{key}".encode("ascii")
        return "Basic " + base64.b64encode(pair).decode("ascii")
    return None


_CREDENTIAL = re.compile(r"[\x21-\x7e]+")  # visible ASCII: no spaces, newlines or controls


def _check_credential(value: str, what: str) -> None:
    """Refuse a credential that could break a header; the message never includes it."""
    if not _CREDENTIAL.fullmatch(value):
        raise DataError(f"{what} holds spaces, line breaks or other characters a header cannot")


def build_request(url: str, auth: str | None) -> Request:
    """The download request. The credential is an unredirected header, so it goes to Kaggle
    only and never to the storage host Kaggle redirects to."""
    req = Request(url, headers={"User-Agent": "fraud-review-bedrock"})
    if auth:
        req.add_unredirected_header("Authorization", auth)
    return req


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open(mode="rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _have_source(raw_dir: Path, expected: Mapping[str, str]) -> bool:
    return all(
        (raw_dir / name).is_file() and sha256_file(raw_dir / name) == digest
        for name, digest in expected.items()
    )


ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})  # what Kaggle uses


def _open_new(path: Path) -> IO[bytes]:
    """Create `path` for writing; refuse to follow a symlink or reuse an existing file."""
    path.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o644), mode="wb")


def extract_source(archive: IO[bytes], raw_dir: Path, expected: Mapping[str, str]) -> None:
    """Extract exactly the expected files from the archive by name (never by the archive's
    own paths), with a size limit, and check each file's sha256 before keeping it. Only stored
    or deflated, unencrypted members are read; any failure while reading one is a `DataError`
    and leaves no partial file."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(archive)
    except Exception as exc:  # any malformed archive, whatever the zipfile error type
        raise DataError("the download is not a readable zip archive") from exc
    with zf:
        names = set(zf.namelist())
        missing = sorted(set(expected) - names)
        if missing:
            raise DataError(f"the archive lacks {', '.join(missing)}")
        for name, digest in expected.items():
            info = zf.getinfo(name)
            if info.compress_type not in ALLOWED_COMPRESSION or info.flag_bits & 0x1:
                raise DataError(f"{name}: compressed or encrypted in a form this loader refuses")
            if info.file_size > MAX_MEMBER_BYTES:
                raise DataError(f"{name}: larger than {MAX_MEMBER_BYTES} bytes")
            tmp = raw_dir / f".{name}.part"
            h = hashlib.sha256()
            size = 0
            try:
                try:
                    with zipfile.Path(zf, name).open(mode="rb") as src, _open_new(tmp) as dst:
                        for block in iter(lambda: src.read(1 << 20), b""):
                            size += len(block)
                            if size > MAX_MEMBER_BYTES:
                                raise DataError(f"{name}: larger than {MAX_MEMBER_BYTES} bytes")
                            h.update(block)
                            dst.write(block)
                except DataError:
                    raise
                except Exception as exc:  # corrupt data in any form: refuse
                    raise DataError(f"{name}: the archive member is corrupt") from exc
                if h.hexdigest() != digest:
                    raise DataError(
                        f"{name}: sha256 {h.hexdigest()} does not match the pinned {digest} "
                        "(the source changed; see DATA_CARD.md)"
                    )
                os.replace(tmp, raw_dir / name)
            finally:  # also on interrupts and a failed rename: keep nothing partial
                tmp.unlink(missing_ok=True)


def download_source(
    raw_dir: Path,
    *,
    auth: str | Callable[[], str | None] | None = None,
    expected: Mapping[str, str] | None = None,
    fetch: Callable[[Request], IO[bytes]] | None = None,
) -> Path:
    """Make sure the pinned source files are in `raw_dir`, downloading them if needed. `auth`
    may be a function, called only when a download is needed."""
    expected = SOURCE_SHA256 if expected is None else expected
    if _have_source(raw_dir, expected):
        return raw_dir
    req = build_request(SOURCE_URL, auth() if callable(auth) else auth)
    opener = fetch or (lambda r: urlopen(r, timeout=120))
    raw_dir.mkdir(parents=True, exist_ok=True)
    part = raw_dir / ".download.zip.part"
    try:
        try:
            with opener(req) as resp, _open_new(part) as out:
                size = 0
                for block in iter(lambda: resp.read(1 << 20), b""):
                    size += len(block)
                    if size > 2 * MAX_MEMBER_BYTES:
                        raise DataError(f"the download is larger than {2 * MAX_MEMBER_BYTES} bytes")
                    out.write(block)
        except DataError:
            raise
        except Exception as exc:
            # the message is left out: some carry the request's header values
            raise DataError(f"downloading {SOURCE} failed ({type(exc).__name__})") from None
        with part.open(mode="rb") as archive:
            extract_source(archive, raw_dir, expected)
    finally:
        part.unlink(missing_ok=True)
    return raw_dir


# ---------------------------------------------------------------- minimise


def _read_raw(name: str, content: bytes) -> pd.DataFrame:
    """Parse a source file from the very bytes whose hash was checked (no second read)."""
    try:
        header = content.split(b"\n", 1)[0].decode("utf-8").rstrip("\r").split(",")
    except UnicodeDecodeError as exc:
        raise DataError(f"{name}: the header is not UTF-8") from exc
    if tuple(header) != RAW_COLUMNS:
        raise DataError(f"{name}: unexpected columns {header}")
    with warnings.catch_warnings():
        warnings.simplefilter("error", pd.errors.ParserWarning)  # a row with extra fields
        try:
            df = pd.read_csv(
                io.BytesIO(content),
                encoding="utf-8",
                dtype={
                    "cc_num": str,
                    "trans_num": str,
                    "merchant": str,
                    "category": str,
                    "trans_date_trans_time": str,
                    "amt": str,  # checked as text against the source's shape before parsing
                    "is_fraud": str,
                },
                keep_default_na=False,
                na_values=[],
                index_col=False,  # never turn a column into the index when a row has an extra field
            )
        except (pd.errors.ParserWarning, pd.errors.ParserError) as exc:
            raise DataError(f"{name}: a row does not have the header's fields") from exc
    return df.drop(columns=df.columns[0])


# Allow-lists for the source's text fields (lesson L-28): the shapes Sparkov writes, nothing else
RAW_SHAPES = {
    "cc_num": re.compile(r"[0-9]{11,19}"),
    "trans_num": re.compile(r"[0-9a-f]{32}"),
    "amt": re.compile(r"[0-9]{1,7}(\.[0-9]{1,2})?"),  # dollars and cents, as Sparkov writes them
    "is_fraud": re.compile(r"[01]"),
}
# After the prefix: ASCII letters first, then letters, spaces and ' , . & - (no digits at all,
# so no number of any length, and no invisible or lookalike characters)
MERCHANT_SHAPE = re.compile(r"[A-Za-z][A-Za-z ',.&-]{0,99}")
MAX_AMOUNT = 1_000_000.0  # the real maximum is 28,948.90


def minimise(raw: pd.DataFrame, source: str = "source") -> pd.DataFrame:
    """Check a raw source frame and keep only the pipeline's columns (Tech Spec §2.2)."""
    where = source
    text_cols = ("trans_date_trans_time", "cc_num", "merchant", "category", "trans_num")
    for col in (*text_cols, "amt", "is_fraud"):
        if not all(type(v) is str for v in raw[col]):
            raise DataError(f"{where}: {col} values must be text as read from the CSV")
    for col, shape in RAW_SHAPES.items():
        if not all(shape.fullmatch(v) for v in raw[col]):
            raise DataError(f"{where}: empty values in {col}, or values not in the expected form")
    if not raw["trans_date_trans_time"].astype(str).str.fullmatch(TS_SHAPE).all():
        raise DataError(f"{where}: a timestamp is not {TS_FORMAT}")
    try:
        ts = pd.to_datetime(raw["trans_date_trans_time"], format=TS_FORMAT, exact=True)
    except (ValueError, TypeError) as exc:
        raise DataError(f"{where}: a timestamp is not {TS_FORMAT}") from exc
    try:
        amt = pd.Series(pd.to_numeric(raw["amt"], errors="raise"), dtype="float64")
    except (ValueError, TypeError) as exc:
        raise DataError(f"{where}: an amount is not a number") from exc
    if not all(math.isfinite(a) and 0 < a <= MAX_AMOUNT for a in amt):
        raise DataError(f"{where}: amounts must be positive, finite and at most {MAX_AMOUNT:,.0f}")
    label = pd.Series(pd.to_numeric(raw["is_fraud"], errors="raise"), dtype="float64")
    unknown = sorted(set(raw["category"]) - CATEGORIES)
    if unknown:
        raise DataError(f"{where}: unknown categories {unknown}")
    if raw["trans_num"].duplicated().any():
        raise DataError(f"{where}: repeated trans_num")
    merchant = raw["merchant"].astype(str)
    prefixed = merchant.str.startswith(MERCHANT_PREFIX)
    # C4: the prefix must be on every row, fraud or not; a prefix that told the label apart
    # would leak it, so anything else stops the load instead of being stripped
    if not prefixed.all():
        share = {int(k): float(v) for k, v in prefixed.groupby(label).mean().items()}
        raise DataError(f"{where}: the '{MERCHANT_PREFIX}' prefix is not on every row {share}")
    merchant = merchant.str.slice(len(MERCHANT_PREFIX))
    if not all(MERCHANT_SHAPE.fullmatch(m) for m in merchant):
        raise DataError(
            f"{where}: a merchant name is empty after removing the prefix, or not plain text "
            "(ASCII letters first, then letters, spaces and ' , . & -)"
        )
    cards = {c: opaque_id("c-", c) for c in raw["cc_num"].unique()}
    out = pd.DataFrame(
        {
            "trans_id": [opaque_id("t-", t) for t in raw["trans_num"]],
            "card_id": [cards[c] for c in raw["cc_num"]],
            "ts": ts.astype("datetime64[us]"),
            "merchant": merchant,
            "category": raw["category"].astype(str),
            "amt": amt,
            "is_fraud": label.astype("int8"),
        }
    )
    check_one_to_one(raw["trans_num"], out["trans_id"], f"{where}: transactions")
    check_one_to_one(raw["cc_num"], out["card_id"], f"{where}: cards")
    return out.loc[:, list(COLUMNS)]


def check_one_to_one(source: Iterable[Any], ids: Iterable[Any], what: str) -> None:
    """Each source value has one id and each id one source value; a collision of the 64-bit
    ids is refused, never merged."""
    pairs = set(zip(source, ids, strict=True))
    if len({s for s, _ in pairs}) != len(pairs) or len({i for _, i in pairs}) != len(pairs):
        raise DataError(f"{what}: two different values got the same opaque id")


def time_splits(train_file: pd.DataFrame, test_file: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """D-8: train and validation cut from the train file at VAL_START; the test file is the
    test period. Each split is in time order (ties keep file order)."""
    if train_file.empty or test_file.empty:
        raise DataError("a source file has no rows")
    if not pd.Timestamp(test_file["ts"].to_numpy().min()) > pd.Timestamp(
        train_file["ts"].to_numpy().max()
    ):
        raise DataError("the test file overlaps the train file in time")
    if set(train_file["trans_id"]) & set(test_file["trans_id"]):
        raise DataError("a transaction id is in both source files")
    cut = pd.Timestamp(VAL_START)
    parts = {
        "train": train_file[train_file["ts"] < cut],
        "val": train_file[train_file["ts"] >= cut],
        "test": test_file,
    }
    out: dict[str, pd.DataFrame] = {}
    for name, part in parts.items():
        if part.empty:
            raise DataError(f"split {name} is empty")
        out[name] = part.sort_values("ts", kind="mergesort").reset_index(drop=True)
    return out


# ---------------------------------------------------------------- hashes and storage


def content_hash(df: pd.DataFrame) -> str:
    """sha256 over a canonical text form of the split (column names, then one line per row in
    order), independent of the file format and library versions."""
    if tuple(df.columns) != COLUMNS:
        raise DataError(f"columns {list(df.columns)}, expected {list(COLUMNS)}")
    h = hashlib.sha256((json.dumps(list(COLUMNS)) + "\n").encode("ascii"))
    ts = df["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    for row in zip(
        df["trans_id"],
        df["card_id"],
        ts,
        df["merchant"],
        df["category"],
        df["amt"],
        df["is_fraud"],
        strict=True,
    ):
        t, c, when, merchant, cat, amt, label = row
        fields = [str(t), str(c), str(when), str(merchant), str(cat)]
        line = json.dumps([*fields, repr(float(amt)), int(label)], ensure_ascii=True)
        h.update(line.encode("ascii") + b"\n")  # JSON escapes any tab or newline in a value
    return h.hexdigest()


def split_facts(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": len(df),
        "fraud": int(df["is_fraud"].to_numpy().sum()),
        "fraud_rate": round(float(df["is_fraud"].to_numpy().mean()), 6),
        "cards": len(set(df["card_id"])),
        "first_ts": pd.Timestamp(df["ts"].to_numpy().min()).isoformat(),
        "last_ts": pd.Timestamp(df["ts"].to_numpy().max()).isoformat(),
        "sha256": content_hash(df),
    }


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.part")
    try:
        with _open_new(tmp) as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_splits(
    splits: Mapping[str, pd.DataFrame],
    out_dir: Path,
    source: Mapping[str, str],
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one parquet file per split, then `splits.json` with the source hashes and each
    split's facts and content hash (written last, so a manifest only names complete files)."""
    manifest = build_manifest(splits, source) if manifest is None else manifest
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in SPLITS:
        table = pa.Table.from_pandas(
            splits[name], schema=SCHEMA, preserve_index=False
        ).replace_schema_metadata(None)  # the file carries data only
        tmp = out_dir / f".{name}.parquet.part"
        try:
            with _open_new(tmp) as f:
                pq.write_table(table, f)
            os.replace(tmp, out_dir / f"{name}.parquet")
        finally:
            tmp.unlink(missing_ok=True)
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    _atomic_write_bytes(out_dir / MANIFEST, text.encode("ascii"))
    return manifest


def build_manifest(splits: Mapping[str, pd.DataFrame], source: Mapping[str, str]) -> dict[str, Any]:
    """The facts `splits.json` records: source hashes, cut date, id key hash, columns, and each
    split's rows, fraud, rate, cards, period and content hash."""
    facts = {name: split_facts(splits[name]) for name in SPLITS}
    return {
        "schema_version": MANIFEST_VERSION,
        "source": SOURCE,
        "source_sha256": dict(sorted(source.items())),
        "val_start": VAL_START.isoformat(),
        "id_salt_sha256": hashlib.sha256(ID_SALT).hexdigest(),
        "columns": list(COLUMNS),
        "splits": facts,
        "data_label": "simulated",
    }


def read_manifest(data_dir: Path) -> dict[str, Any]:
    path = data_dir / MANIFEST
    if not path.is_file():
        raise DataError(f"{path}: missing; run `fraud load`")
    try:
        manifest = json.loads(path.read_text(encoding="ascii"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise DataError(f"{path}: not valid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_VERSION:
        raise DataError(f"{path}: not a version {MANIFEST_VERSION} manifest")
    return manifest


def read_split(data_dir: Path, name: str) -> pd.DataFrame:
    """Read one split and check it against the content hash in `splits.json` (H6)."""
    if name not in SPLITS:
        raise DataError(f"unknown split {name!r}; expected one of {', '.join(SPLITS)}")
    manifest = read_manifest(data_dir)
    try:
        want = manifest["splits"][name]["sha256"]
    except (KeyError, TypeError) as exc:
        raise DataError(f"{data_dir / MANIFEST}: no hash for split {name}") from exc
    path = data_dir / f"{name}.parquet"
    if not path.is_file():
        raise DataError(f"{path}: missing; run `fraud load`")
    try:
        table = pq.read_table(path)
    except (pa.ArrowException, OSError) as exc:
        raise DataError(f"{path}: not a readable parquet file") from exc
    if table.schema.remove_metadata() != SCHEMA:
        raise DataError(f"{path}: unexpected columns or types")
    if any(table.column(c).null_count for c in COLUMNS):
        raise DataError(f"{path}: empty (null) values")
    if table.schema.metadata or any(field.metadata for field in table.schema):
        raise DataError(f"{path}: unexpected file metadata (splits carry data only)")
    try:
        df = table.to_pandas(ignore_metadata=True)
    except (pa.ArrowException, UnicodeDecodeError, ValueError) as exc:
        raise DataError(f"{path}: a value cannot be read") from exc
    got = content_hash(df)
    if got != want:
        raise DataError(f"{path}: content hash {got} does not match splits.json ({want})")
    rows = manifest["splits"][name].get("rows")
    if type(rows) is not int or len(df) != rows:
        raise DataError(f"{path}: {len(df)} rows, splits.json records a different count")
    if not (
        df["trans_id"].astype(str).str.fullmatch(TRANS_ID).all()
        and df["card_id"].astype(str).str.fullmatch(CARD_ID).all()
        and set(df["category"]) <= CATEGORIES
    ):
        raise DataError(f"{path}: an id or category is not in the expected form")
    return df


# ---------------------------------------------------------------- the whole load


@dataclass(frozen=True)
class LoadResult:
    data_dir: Path
    manifest: dict[str, Any]
    written: bool  # False when the result differed from the reference and nothing was written
    mismatches: tuple[str, ...]  # differences from the reference manifest, if one was given


_ABSENT = object()


def compare_manifests(got: Mapping[str, Any], reference: Mapping[str, Any]) -> list[str]:
    """Every difference between two manifests, at any depth: a key present in only one of them
    (even with a null value), an extra or missing split, or a value that differs in content or
    JSON type (so 1, 1.0 and true all differ)."""
    problems: list[str] = []

    def diff(a: Any, b: Any, path: str) -> None:
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            for key in sorted(set(a) | set(b), key=str):
                diff(
                    a.get(key, _ABSENT), b.get(key, _ABSENT), f"{path}.{key}" if path else str(key)
                )
            return
        if (
            a is _ABSENT
            or b is _ABSENT
            or json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)
        ):
            show = lambda v: "(absent)" if v is _ABSENT else repr(v)  # noqa: E731
            problems.append(f"{path}: {show(a)} != {show(b)}")

    diff(got, reference, "")
    return [p.removeprefix("splits.") for p in problems]


def _check_reference(reference: Any) -> None:
    ok = isinstance(reference, Mapping) and isinstance(reference.get("splits"), Mapping)
    if not ok or not all(isinstance(reference["splits"].get(n), Mapping) for n in SPLITS):
        raise DataError("the reference manifest is not a splits.json with all three splits")


def load(
    raw_dir: Path,
    data_dir: Path,
    *,
    expected: Mapping[str, str] | None = None,
    reference: Mapping[str, Any] | None = None,
) -> LoadResult:
    """Check the pinned source files, minimise, split, write and record (`fraud load`)."""
    expected = SOURCE_SHA256 if expected is None else expected
    names = list(expected)
    if len(names) != 2:
        raise DataError("expected exactly two source files (train, test)")
    contents: dict[str, bytes] = {}
    for name, digest in expected.items():
        path = raw_dir / name
        if not path.is_file():
            raise DataError(f"{path}: missing; download it first")
        content = path.read_bytes()  # hashed and parsed from this one read
        got = hashlib.sha256(content).hexdigest()
        if got != digest:
            raise DataError(f"{path}: sha256 {got} does not match the pinned {digest}")
        contents[name] = content
    train_raw, test_raw = (_read_raw(n, contents[n]) for n in names)
    if set(train_raw["trans_num"]) & set(test_raw["trans_num"]):
        raise DataError("a trans_num is in both source files")
    train, test = minimise(train_raw, names[0]), minimise(test_raw, names[1])
    both = "both source files"
    check_one_to_one(
        [*train_raw["cc_num"], *test_raw["cc_num"]],
        [*train["card_id"], *test["card_id"]],
        f"{both}: cards",
    )
    check_one_to_one(
        [*train_raw["trans_num"], *test_raw["trans_num"]],
        [*train["trans_id"], *test["trans_id"]],
        f"{both}: transactions",
    )
    splits = time_splits(train, test)
    manifest = build_manifest(splits, expected)
    if reference is not None:
        _check_reference(reference)
    mismatches = tuple(compare_manifests(manifest, reference)) if reference is not None else ()
    if mismatches:  # leave any earlier, matching splits in place
        return LoadResult(data_dir, manifest, False, mismatches)
    write_splits(splits, data_dir, expected, manifest)
    return LoadResult(data_dir, manifest, True, mismatches)
