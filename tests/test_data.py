"""N1.2: data load, minimisation and time splits, on a small synthetic source in Sparkov's
exact format (the real files are pinned by hash and never needed in CI)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fraudreview import cli, data
from fraudreview.data import (
    COLUMNS,
    DROPPED,
    MANIFEST,
    RAW_COLUMNS,
    SCHEMA,
    SOURCE_SHA256,
    VAL_START,
    DataError,
    alert_id,
    build_request,
    compare_manifests,
    content_hash,
    download_source,
    extract_source,
    kaggle_auth,
    load,
    minimise,
    opaque_id,
    read_manifest,
    read_split,
    time_splits,
)
from fraudreview.schemas import ALERT_ID, DIGIT_RUN

ROOT = Path(__file__).resolve().parent.parent
FIRSTS = ["Jennifer", "Stephanie", "Edward", "Jeremy", "Tyler"]
LASTS = ["Banks", "Gill", "Sanchez", "White", "Garcia"]
STREETS = ["561 Perry Cove", "43039 Riley Greens Suite 393", "594 White Dale Suite 530"]
CARDS = ["2703186189652095", "630423337322", "38859492057661", "3534093764340240"]
CATS = ["misc_net", "grocery_pos", "entertainment", "gas_transport", "shopping_net"]
MERCHANTS = [
    "Rippin, Kub and Mann",
    "Heller, Gutmann and Zieme",
    "Lind-Buckridge",
    "Kirlin and Sons",
]


def raw_rows(start: datetime, n: int, offset: int = 0, prefix: str = "fraud_") -> list[list[Any]]:
    rows = []
    for i in range(n):
        k = i + offset
        when = start + timedelta(hours=7 * i, seconds=k % 50)
        rows.append(
            [
                k,
                when.strftime("%Y-%m-%d %H:%M:%S"),
                CARDS[k % len(CARDS)],
                prefix + MERCHANTS[k % len(MERCHANTS)],
                CATS[k % len(CATS)],
                round(1 + (k * 37 % 5000) / 7, 2),
                FIRSTS[k % 5],
                LASTS[k % 5],
                "F" if k % 2 else "M",
                STREETS[k % 3],
                "Moravian Falls",
                "NC",
                28654,
                36.0788,
                -81.1781,
                3495,
                "Psychologist, counselling",
                "1988-03-09",
                hashlib.md5(f"t{k}".encode()).hexdigest(),
                1325376018 + k,
                36.011293,
                -82.048315,
                1 if k % 9 == 0 else 0,
            ]
        )
    return rows


def write_csv(path: Path, rows: list[list[Any]], header: tuple[str, ...] = RAW_COLUMNS) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path


def make_source(tmp: Path, train_rows=None, test_rows=None) -> tuple[Path, dict[str, str]]:
    raw = tmp / "raw"
    raw.mkdir(exist_ok=True)
    if train_rows is None:
        train_rows = raw_rows(datetime(2019, 10, 1), 1000)  # crosses VAL_START
    if test_rows is None:
        test_rows = raw_rows(datetime(2020, 9, 1), 300, offset=5000)
    write_csv(raw / "fraudTrain.csv", train_rows)
    write_csv(raw / "fraudTest.csv", test_rows)
    expected = {n: data.sha256_file(raw / n) for n in ("fraudTrain.csv", "fraudTest.csv")}
    return raw, expected


@pytest.fixture
def loaded(tmp_path: Path):
    raw, expected = make_source(tmp_path)
    result = load(raw, tmp_path / "data", expected=expected)
    return raw, expected, result


def write_split(df: pd.DataFrame, path: Path) -> None:
    """Write a split as the loader does: data only, no metadata."""
    table = pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False)
    pq.write_table(table.replace_schema_metadata(None), path)


def frame(rows: list[list[Any]]) -> pd.DataFrame:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(RAW_COLUMNS)
    w.writerows(rows)
    path_df = pd.read_csv(
        io.StringIO(buf.getvalue()),
        dtype={
            "cc_num": str,
            "trans_num": str,
            "merchant": str,
            "category": str,
            "trans_date_trans_time": str,
            "amt": str,
            "is_fraud": str,
        },
        keep_default_na=False,
    )
    return path_df.drop(columns=path_df.columns[0])


# ------------------------------------------------------------------ DAT-01 loader


def test_kaggle_auth_order_and_forms(tmp_path: Path):
    home = tmp_path
    assert kaggle_auth({}, home) is None
    (home / ".kaggle").mkdir()
    (home / ".kaggle" / "kaggle.json").write_text('{"username": "u", "key": "k"}', "utf-8")
    assert kaggle_auth({}, home) == "Basic dTpr"
    (home / ".kaggle" / "access_token").write_text("KGAT_file\n", "utf-8")
    assert kaggle_auth({}, home) == "Bearer KGAT_file"
    assert kaggle_auth({"KAGGLE_API_TOKEN": " KGAT_env "}, home) == "Bearer KGAT_env"
    (home / ".kaggle" / "access_token").unlink()
    (home / ".kaggle" / "kaggle.json").write_text("{bad", "utf-8")
    with pytest.raises(DataError, match="username"):
        kaggle_auth({}, home)


def test_credential_is_never_forwarded_on_redirect():
    req = build_request(data.SOURCE_URL, "Bearer secret")
    assert req.get_header("Authorization") == "Bearer secret"
    assert "Authorization" not in req.headers  # only in the unredirected headers
    handler = urllib.request.HTTPRedirectHandler()
    new = handler.redirect_request(
        req,
        cast(Any, None),
        302,
        "Found",
        cast(Any, {}),
        "https://storage.googleapis.com/archive.zip",
    )
    assert new is not None
    assert new.get_header("Authorization") is None
    assert all("secret" not in str(v) for v in new.header_items())
    assert build_request(data.SOURCE_URL, None).get_header("Authorization") is None


def zip_of(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return buf.getvalue()


def test_extract_keeps_only_pinned_files_by_name(tmp_path: Path):
    good = {"a.csv": b"alpha\n", "b.csv": b"beta\n"}
    expected = {n: hashlib.sha256(b).hexdigest() for n, b in good.items()}
    archive = zip_of({**good, "../evil.csv": b"x", "extra.csv": b"y"})
    extract_source(io.BytesIO(archive), tmp_path / "raw", expected)
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == ["a.csv", "b.csv"]
    assert not (tmp_path / "evil.csv").exists()


def test_extract_refuses_missing_changed_oversized_or_non_zip(tmp_path: Path, monkeypatch):
    expected = {"a.csv": hashlib.sha256(b"alpha\n").hexdigest()}
    with pytest.raises(DataError, match=r"lacks a\.csv"):
        extract_source(io.BytesIO(zip_of({"b.csv": b"x"})), tmp_path / "r1", expected)
    with pytest.raises(DataError, match="does not match the pinned"):
        extract_source(io.BytesIO(zip_of({"a.csv": b"changed\n"})), tmp_path / "r2", expected)
    assert list((tmp_path / "r2").iterdir()) == []  # nothing half-kept
    monkeypatch.setattr(data, "MAX_MEMBER_BYTES", 3)
    with pytest.raises(DataError, match="larger than"):
        extract_source(io.BytesIO(zip_of({"a.csv": b"alpha\n"})), tmp_path / "r3", expected)
    assert list((tmp_path / "r3").iterdir()) == []
    with pytest.raises(DataError, match="not a readable zip"):
        extract_source(io.BytesIO(b"<html>login</html>"), tmp_path / "r4", expected)


def test_download_fetches_once_then_reuses_verified_files(tmp_path: Path):
    good = {"fraudTrain.csv": b"t\n", "fraudTest.csv": b"s\n"}
    expected = {n: hashlib.sha256(b).hexdigest() for n, b in good.items()}
    seen: list[urllib.request.Request] = []

    def fetch(req):
        seen.append(req)
        return io.BytesIO(zip_of(good))

    download_source(tmp_path, auth="Bearer tok", expected=expected, fetch=fetch)
    assert len(seen) == 1 and seen[0].full_url == data.SOURCE_URL
    assert seen[0].get_header("Authorization") == "Bearer tok"

    def no_fetch(req):
        raise AssertionError("should not download again")

    download_source(tmp_path, expected=expected, fetch=no_fetch)
    (tmp_path / "fraudTest.csv").write_bytes(b"tampered\n")
    download_source(tmp_path, expected=expected, fetch=fetch)  # refetched and replaced
    assert len(seen) == 2 and (tmp_path / "fraudTest.csv").read_bytes() == b"s\n"
    assert not (tmp_path / ".download.zip.part").exists()


def test_download_refuses_an_oversized_body_and_cleans_up(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(data, "MAX_MEMBER_BYTES", 10)
    expected = {"fraudTrain.csv": "0" * 64, "fraudTest.csv": "1" * 64}
    with pytest.raises(DataError, match="download is larger"):
        download_source(tmp_path, expected=expected, fetch=lambda r: io.BytesIO(b"x" * 100))
    assert list(tmp_path.iterdir()) == []


def test_load_refuses_missing_or_changed_source(tmp_path: Path):
    raw, expected = make_source(tmp_path)
    with pytest.raises(DataError, match="does not match the pinned"):
        load(raw, tmp_path / "d", expected={**expected, "fraudTest.csv": "0" * 64})
    (raw / "fraudTest.csv").unlink()
    with pytest.raises(DataError, match="missing"):
        load(raw, tmp_path / "d", expected=expected)


def test_load_refuses_a_changed_header(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    header = tuple("ssn" if c == "job" else c for c in RAW_COLUMNS)
    write_csv(raw / "fraudTrain.csv", raw_rows(datetime(2019, 10, 1), 50), header)
    write_csv(raw / "fraudTest.csv", raw_rows(datetime(2020, 9, 1), 20, 500))
    expected = {n: data.sha256_file(raw / n) for n in ("fraudTrain.csv", "fraudTest.csv")}
    with pytest.raises(DataError, match="unexpected columns"):
        load(raw, tmp_path / "d", expected=expected)


@pytest.mark.contract
def test_pinned_source_matches_the_committed_reference():
    ref = json.loads((ROOT / "docs" / "data" / "splits.json").read_text(encoding="ascii"))
    assert ref["source"] == data.SOURCE == "kartik2112/fraud-detection"
    assert ref["source_sha256"] == SOURCE_SHA256
    assert ref["val_start"] == VAL_START.isoformat()
    assert ref["id_salt_sha256"] == hashlib.sha256(data.ID_SALT).hexdigest()
    assert ref["columns"] == list(COLUMNS)
    assert ref["data_label"] == "simulated"
    assert set(ref["splits"]) == {"train", "val", "test"}


# ------------------------------------------------------------------ DAT-02 minimisation


def test_minimised_splits_keep_only_the_pipeline_columns(loaded):
    _, _, result = loaded
    for name in ("train", "val", "test"):
        df = read_split(result.data_dir, name)
        assert tuple(df.columns) == COLUMNS
        assert not set(DROPPED) & set(df.columns)
        names = pq.read_schema(result.data_dir / f"{name}.parquet").names
        assert names == list(COLUMNS)


@pytest.mark.safety
def test_no_dropped_value_survives_anywhere(loaded):
    _, _, result = loaded
    rows = raw_rows(datetime(2019, 10, 1), 1000) + raw_rows(datetime(2020, 9, 1), 300, 5000)
    secrets = {str(r[2]) for r in rows} | {r[18] for r in rows}  # card and transaction numbers
    secrets |= set(FIRSTS) | set(LASTS) | set(STREETS)
    secrets |= {"Moravian Falls", "Psychologist", "1988-03-09", "36.0788", "-81.1781", "28654"}
    texts: list[str] = [(result.data_dir / MANIFEST).read_text(encoding="ascii")]
    for name in ("train", "val", "test"):
        df = read_split(result.data_dir, name)
        texts += df.astype(str).to_numpy().ravel().tolist()
        texts.append(repr(pq.read_metadata(result.data_dir / f"{name}.parquet").metadata))
    blob = "\n".join(texts)
    leaked = sorted(s for s in secrets if s in blob)
    assert leaked == []
    assert not re.search(r"(?<!\d)\d{12,19}(?!\d)", blob)  # nothing that looks like a card


def test_ids_are_opaque_stable_and_one_per_card(loaded):
    _, _, result = loaded
    df = pd.concat([read_split(result.data_dir, n) for n in ("train", "val", "test")])
    assert df["trans_id"].str.fullmatch(r"t-[0-9a-f]{16}").all()
    assert df["card_id"].str.fullmatch(r"c-[0-9a-f]{16}").all()
    assert cast(pd.Series, df["trans_id"]).is_unique
    assert len(set(df["card_id"])) == len(CARDS)
    assert df["card_id"].iloc[0] == opaque_id("c-", CARDS[0])  # same card, same id, every run
    assert not df["trans_id"].str.contains(DIGIT_RUN).any()


def test_opaque_id_bumps_the_salt_past_digit_runs():
    found = None
    for k in range(20000):  # about 1 in 113 first hashes has a run of 12 digits
        import hmac

        first = hmac.new(data.ID_SALT, str(k).encode(), hashlib.sha256).hexdigest()[:16]
        if DIGIT_RUN.search(first):
            found = (str(k), first)
            break
    assert found is not None
    value, first = found
    got = opaque_id("a-", value)
    assert got != "a-" + first and not DIGIT_RUN.search(got)
    assert re.fullmatch(ALERT_ID, alert_id(value)) and alert_id(value) == got
    assert opaque_id("a-", value, salt=b"other") != got


def test_minimise_refuses_bad_values():
    good = raw_rows(datetime(2019, 10, 1), 20)
    cases = {
        "unknown categories": lambda r: r.__setitem__(4, "crypto"),
        "positive, finite": lambda r: r.__setitem__(5, 0),
        "is_fraud, or values not in the expected form": lambda r: r.__setitem__(22, 2),
        "is not": lambda r: r.__setitem__(1, "2019/10/01 00:00:00"),
        "empty values in cc_num": lambda r: r.__setitem__(2, ""),
    }
    for message, edit in cases.items():
        rows = [list(r) for r in good]
        edit(rows[3])
        with pytest.raises(DataError, match=message):
            minimise(frame(rows))
    rows = [list(r) for r in good]
    rows[4][18] = rows[3][18]
    with pytest.raises(DataError, match="repeated trans_num"):
        minimise(frame(rows))
    rows = [list(r) for r in good]
    rows[3][5] = "nan"
    with pytest.raises(DataError, match=r"amt, or values not"):
        minimise(frame(rows))


def test_an_id_collision_is_refused(monkeypatch):
    monkeypatch.setattr(data, "opaque_id", lambda prefix, value, salt=b"": prefix + "0" * 4)
    with pytest.raises(DataError, match="same opaque id"):
        minimise(frame(raw_rows(datetime(2019, 10, 1), 5)))


# ------------------------------------------------------------------ DAT-03 merchant prefix


def test_prefix_is_stripped_from_every_row(loaded):
    _, _, result = loaded
    for name in ("train", "val", "test"):
        merchants = read_split(result.data_dir, name)["merchant"]
        assert not merchants.str.contains("fraud_").any()
        assert set(merchants) <= set(MERCHANTS)


def test_prefix_on_some_rows_only_stops_the_load():
    rows = raw_rows(datetime(2019, 10, 1), 40)
    for r in rows:
        if r[22] == 0:
            r[3] = r[3].removeprefix("fraud_")  # prefix now marks the fraud rows
    with pytest.raises(DataError, match=r"not on every row \{0: 0\.0, 1: 1\.0\}"):
        minimise(frame(rows))
    rows = raw_rows(datetime(2019, 10, 1), 10, prefix="")
    with pytest.raises(DataError, match="not on every row"):
        minimise(frame(rows))
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][3] = "fraud_  "
    with pytest.raises(DataError, match="empty after removing"):
        minimise(frame(rows))


# ------------------------------------------------------------------ DAT-04 splits and hashes


def test_splits_cut_by_date_and_keep_time_order(loaded):
    _, _, result = loaded
    train, val, test = (read_split(result.data_dir, n) for n in ("train", "val", "test"))
    cut = pd.Timestamp(VAL_START)
    assert bool((train["ts"] < cut).to_numpy().all()) and bool((val["ts"] >= cut).to_numpy().all())
    assert pd.Timestamp(test["ts"].to_numpy().min()) > pd.Timestamp(val["ts"].to_numpy().max())
    for df in (train, val, test):
        assert cast(pd.Series, df["ts"]).is_monotonic_increasing
    assert len(train) + len(val) == 1000 and len(test) == 300
    facts = result.manifest["splits"]
    assert facts["train"]["rows"] == len(train)
    assert facts["val"]["fraud"] == int(val["is_fraud"].to_numpy().sum())


def test_ties_keep_file_order_and_unsorted_input_is_sorted():
    rows = raw_rows(datetime(2019, 12, 31), 6)
    rows[1][1] = rows[2][1] = "2020-02-01 00:00:00"  # a tie, in file order 1 then 2
    rows.reverse()
    out = time_splits(minimise(frame(rows)), minimise(frame(raw_rows(datetime(2020, 9, 1), 3, 50))))
    val = out["val"]
    tie = val[val["ts"] == pd.Timestamp("2020-02-01")]
    assert list(tie["trans_id"]) == [opaque_id("t-", rows[3][18]), opaque_id("t-", rows[4][18])]
    assert out["train"]["ts"].is_monotonic_increasing and val["ts"].is_monotonic_increasing


def test_overlapping_or_empty_periods_are_refused():
    train = minimise(frame(raw_rows(datetime(2019, 10, 1), 400)))
    early_test = minimise(frame(raw_rows(datetime(2019, 11, 1), 10, 5000)))
    with pytest.raises(DataError, match="overlaps"):
        time_splits(train, early_test)
    test = minimise(frame(raw_rows(datetime(2020, 9, 1), 10, 5000)))
    only_2019 = minimise(frame(raw_rows(datetime(2019, 1, 1), 10)))
    with pytest.raises(DataError, match="split val is empty"):
        time_splits(only_2019, test)
    shared = minimise(frame(raw_rows(datetime(2020, 9, 1), 10, 0)))
    with pytest.raises(DataError, match="both source files"):
        time_splits(minimise(frame(raw_rows(datetime(2019, 10, 1), 10))), shared)


def test_content_hash_is_stable_and_sees_any_change(loaded):
    _, _, result = loaded
    df = read_split(result.data_dir, "val")
    h = content_hash(df)
    assert h == result.manifest["splits"]["val"]["sha256"] == content_hash(df.copy())
    for col, value in [("amt", 1e-9), ("is_fraud", 1), ("merchant", "x"), ("card_id", "c-0")]:
        other = df.copy()
        other.loc[5, col] = other.loc[5, col] + value if col == "amt" else value
        assert content_hash(other) != h, col
    assert content_hash(df.iloc[::-1].reset_index(drop=True)) != h
    assert content_hash(df.iloc[:-1]) != h


def test_read_split_refuses_changed_or_missing_files(loaded):
    _, _, result = loaded
    d = result.data_dir
    df = read_split(d, "test")
    df.loc[0, "is_fraud"] = 1 - df.loc[0, "is_fraud"]
    write_split(df, d / "test.parquet")
    with pytest.raises(DataError, match="content hash"):
        read_split(d, "test")
    df.astype({"is_fraud": "int64"}).to_parquet(d / "test.parquet", index=False)
    with pytest.raises(DataError, match="columns or types"):
        read_split(d, "test")
    with pytest.raises(DataError, match="unknown split"):
        read_split(d, "eval")
    manifest = json.loads((d / MANIFEST).read_text(encoding="ascii"))
    manifest["splits"]["val"]["sha256"] = "0" * 64
    (d / MANIFEST).write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(DataError, match=r"does not match splits\.json"):
        read_split(d, "val")
    (d / "train.parquet").unlink()
    with pytest.raises(DataError, match="missing"):
        read_split(d, "train")
    (d / MANIFEST).write_text("{", encoding="ascii")
    with pytest.raises(DataError, match="not valid JSON"):
        read_manifest(d)
    (d / MANIFEST).unlink()
    with pytest.raises(DataError, match="run `fraud load`"):
        read_split(d, "val")


def test_manifest_records_sources_and_reruns_reproduce_it(loaded, tmp_path: Path):
    raw, expected, result = loaded
    m = result.manifest
    assert m["source_sha256"] == expected and m["val_start"] == "2020-01-01T00:00:00"
    assert m["data_label"] == "simulated" and m["columns"] == list(COLUMNS)
    again = load(raw, tmp_path / "again", expected=expected, reference=m)
    assert again.mismatches == ()
    changed = json.loads(json.dumps(m))
    changed["splits"]["test"]["fraud"] += 1
    changed["val_start"] = "2020-02-01T00:00:00"
    problems = compare_manifests(m, changed)
    assert any(p.startswith("test.fraud") for p in problems)
    assert any(p.startswith("val_start") for p in problems)
    assert not list(result.data_dir.glob(".*.part"))  # no temporary files left


def test_cli_load_offline_reports_and_checks_the_reference(tmp_path: Path, monkeypatch, capsys):
    raw, expected = make_source(tmp_path)
    monkeypatch.setattr(data, "SOURCE_SHA256", expected)
    monkeypatch.setattr(cli, "REFERENCE_MANIFEST", tmp_path / "none.json")
    out_dir = tmp_path / "data"
    args = ["load", "--offline", "--raw-dir", str(raw), "--data-dir", str(out_dir)]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "train" in out and "(simulated)" in out
    ref = json.loads((out_dir / MANIFEST).read_text(encoding="ascii"))
    ref["splits"]["val"]["rows"] = 1
    (tmp_path / "ref.json").write_text(json.dumps(ref), encoding="ascii")
    monkeypatch.setattr(cli, "REFERENCE_MANIFEST", tmp_path / "ref.json")
    assert cli.main(args) == 1
    assert "val.rows" in capsys.readouterr().err
    monkeypatch.setattr(data, "SOURCE_SHA256", {**expected, "fraudTest.csv": "0" * 64})
    assert cli.main(args) == 1
    assert "pinned" in capsys.readouterr().err


# ------------------------------------------------------------------ DAT-05 data card


def card_text() -> str:
    return (ROOT / "DATA_CARD.md").read_text(encoding="utf-8")


@pytest.mark.contract
def test_data_card_numbers_match_the_reference_manifest():
    ref = json.loads((ROOT / "docs" / "data" / "splits.json").read_text(encoding="ascii"))
    text = card_text()
    for name, facts in ref["splits"].items():
        row = next(line for line in text.splitlines() if line.startswith(f"| {name} |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[1] == f"{facts['first_ts'][:10]} to {facts['last_ts'][:10]}", name
        assert cells[2] == f"{facts['rows']:,}", name
        assert cells[3] == f"{facts['fraud']:,}", name
        assert cells[4] == f"{facts['fraud_rate']:.3%} simulated", name
        assert cells[5] == f"{facts['cards']:,}", name
        assert cells[6] == f"`{facts['sha256'][:12]}`", name
    for digest in ref["source_sha256"].values():
        assert digest in text


@pytest.mark.contract
def test_data_card_covers_source_caveats_and_real_data():
    text = card_text()
    for needle in (
        "kartik2112/fraud-detection",
        "Fraud Dataset Benchmark",
        "CC0",
        "## Simulation caveats",
        "## What real data would change",
        "## Splits",
        "## What is kept",
        "fraud_",
    ):
        assert needle in text, needle
    for col in DROPPED:
        assert f"`{col}`" in text, col
    listed = (ROOT / "docs" / "published.txt").read_text(encoding="utf-8").split()
    assert "DATA_CARD.md" in listed  # the honesty gate checks its numbers carry the label


@pytest.mark.safety
def test_no_data_files_are_committed():
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert not [f for f in files if f.endswith((".csv", ".parquet", ".zip"))]
    assert "/data/" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()


# ------------------------------------------------------------------ verifier pass 1 regressions


def test_a_credential_that_could_break_a_header_is_refused_unprinted(tmp_path: Path):
    for env in ({"KAGGLE_API_TOKEN": "FAKE-SECRET-1\nsecond"}, {"KAGGLE_API_TOKEN": "a b"}):
        with pytest.raises(DataError) as exc:
            kaggle_auth(env, tmp_path)
        assert "FAKE-SECRET" not in str(exc.value) and "line breaks" in str(exc.value)
    (tmp_path / ".kaggle").mkdir()
    (tmp_path / ".kaggle" / "access_token").write_text("FAKE-SECRET-2\nline2\n", "utf-8")
    with pytest.raises(DataError) as exc:
        kaggle_auth({}, tmp_path)
    assert "FAKE-SECRET" not in str(exc.value)
    (tmp_path / ".kaggle" / "access_token").unlink()
    (tmp_path / ".kaggle" / "kaggle.json").write_text(
        '{"username": "u", "key": "FAKE-SECRET-3\\r\\nX-Evil: 1"}', "utf-8"
    )
    with pytest.raises(DataError) as exc:
        kaggle_auth({}, tmp_path)
    assert "FAKE-SECRET" not in str(exc.value)


def test_a_failed_download_never_echoes_the_error_text(tmp_path: Path):
    def fetch(req):
        raise ValueError("Invalid header value b'Bearer FAKE-SECRET-4'")

    with pytest.raises(DataError) as exc:
        download_source(tmp_path, auth="Bearer FAKE-SECRET-4", expected={"a": "0"}, fetch=fetch)
    assert "FAKE-SECRET" not in str(exc.value) and "ValueError" in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert list(tmp_path.iterdir()) == []


def test_a_card_id_collision_is_refused_within_and_across_files(tmp_path: Path, monkeypatch):
    real = data.opaque_id

    def colliding(prefix: str, value: str, salt: bytes = data.ID_SALT) -> str:
        return real(prefix, CARDS[0] if prefix == "c-" and value == CARDS[1] else value, salt)

    monkeypatch.setattr(data, "opaque_id", colliding)
    with pytest.raises(DataError, match="cards: two different values"):
        minimise(frame(raw_rows(datetime(2019, 10, 1), 8)))
    train = [r for r in raw_rows(datetime(2019, 10, 1), 400) if r[2] != CARDS[1]]
    test = raw_rows(datetime(2020, 9, 1), 40, 5000)
    for r in test:
        r[2] = (
            CARDS[1] if r[2] == CARDS[0] else r[2]
        )  # CARDS[1] only in test, CARDS[0] only in train
    train = [r for r in train if r[2] != CARDS[1]]
    test = [r for r in test if r[2] != CARDS[0]]
    raw, expected = make_source(tmp_path, train, test)
    with pytest.raises(DataError, match="both source files: cards"):
        load(raw, tmp_path / "d", expected=expected)


def test_a_trans_num_in_both_files_is_named_as_such(tmp_path: Path):
    train = raw_rows(datetime(2019, 10, 1), 400)
    test = raw_rows(datetime(2020, 9, 1), 40, 5000)
    test[3][18] = train[5][18]
    raw, expected = make_source(tmp_path, train, test)
    with pytest.raises(DataError, match="trans_num is in both source files"):
        load(raw, tmp_path / "d", expected=expected)


def test_a_forged_row_boundary_cannot_hide_a_dropped_row(loaded):
    _, _, result = loaded
    d = result.data_dir
    df = read_split(d, "val")
    h = content_hash(df)
    forged = df.iloc[1:].reset_index(drop=True)
    forged.loc[0, "trans_id"] = "\t".join(map(str, df.loc[0])) + "\n" + str(df.loc[1, "trans_id"])
    assert content_hash(forged) != h
    # the same forgery against the current canonical form: the value would have to carry the
    # previous row's JSON line and a newline, which JSON escaping turns into `\\n`
    line0 = json.dumps(
        [
            *map(str, df.loc[0, ["trans_id", "card_id"]]),
            df.loc[0, "ts"].strftime("%Y-%m-%dT%H:%M:%S.%f"),
            *map(str, df.loc[0, ["merchant", "category"]]),
            repr(float(df.loc[0, "amt"])),
            int(df.loc[0, "is_fraud"]),
        ]
    )
    forged.loc[0, "trans_id"] = line0 + "\n" + str(df.loc[1, "trans_id"])
    assert content_hash(forged) != h
    write_split(forged, d / "val.parquet")
    with pytest.raises(DataError):
        read_split(d, "val")


def test_read_split_checks_rows_and_id_forms(loaded):
    _, _, result = loaded
    d = result.data_dir
    df = read_split(d, "test")
    df.loc[0, "card_id"] = "c-NOT-AN-ID"
    write_split(df, d / "test.parquet")
    manifest = json.loads((d / MANIFEST).read_text(encoding="ascii"))
    manifest["splits"]["test"]["sha256"] = content_hash(df)  # a consistent forgery
    (d / MANIFEST).write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(DataError, match="expected form"):
        read_split(d, "test")
    manifest["splits"]["val"]["rows"] = 1
    (d / MANIFEST).write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(DataError, match="rows"):
        read_split(d, "val")


def test_corrupt_member_unpadded_time_and_blank_ids_are_refused(tmp_path: Path):
    body = b"alpha" * 2000
    archive = bytearray(zip_of({"a.csv": body}))
    i = archive.find(b"alpha")
    archive[i : i + 5] = b"omega"  # stored uncompressed: the CRC no longer matches
    with pytest.raises(DataError, match="corrupt"):
        extract_source(
            io.BytesIO(bytes(archive)), tmp_path / "r", {"a.csv": hashlib.sha256(body).hexdigest()}
        )
    assert list((tmp_path / "r").iterdir()) == []
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][1] = "2019-10-1 0:0:18"
    with pytest.raises(DataError, match="timestamp"):
        minimise(frame(rows))
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][18] = "   "
    with pytest.raises(DataError, match="empty values in trans_num"):
        minimise(frame(rows))


def test_a_mismatched_load_writes_nothing(loaded, tmp_path: Path):
    raw, expected, result = loaded
    before = {p.name: p.read_bytes() for p in result.data_dir.iterdir()}
    ref = json.loads(json.dumps(result.manifest))
    ref["splits"]["train"]["rows"] += 1
    again = load(raw, result.data_dir, expected=expected, reference=ref)
    assert again.mismatches and not again.written
    assert {p.name: p.read_bytes() for p in result.data_dir.iterdir()} == before


# ------------------------------------------------------------------ verifier pass 2 regressions


def test_a_deflate_corrupted_member_is_refused_without_a_partial_file(tmp_path: Path):
    body = bytes(range(256)) * 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.csv", body)
    archive = bytearray(buf.getvalue())
    start = 30 + len("a.csv")  # local header, then the compressed data
    for k in range(start + 5, start + 45):
        archive[k] ^= 0xFF
    expected = {"a.csv": hashlib.sha256(body).hexdigest()}
    with pytest.raises(DataError, match="corrupt"):
        extract_source(io.BytesIO(bytes(archive)), tmp_path / "r", expected)
    assert list((tmp_path / "r").iterdir()) == []
    with pytest.raises(DataError, match="corrupt"):
        download_source(
            tmp_path / "d", expected=expected, fetch=lambda r: io.BytesIO(bytes(archive))
        )
    assert list((tmp_path / "d").iterdir()) == []


def test_an_undecodable_token_file_is_a_data_error(tmp_path: Path):
    (tmp_path / ".kaggle").mkdir()
    (tmp_path / ".kaggle" / "access_token").write_bytes(b"KGAT_\xff\xfe")
    with pytest.raises(DataError, match="not UTF-8"):
        kaggle_auth({}, tmp_path)


def test_a_null_cell_or_a_boolean_label_is_refused(loaded):
    _, _, result = loaded
    d = result.data_dir
    df = read_split(d, "val")
    table = pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False)
    labels = table.column("is_fraud").to_pylist()
    labels[0] = None
    table = table.set_column(6, "is_fraud", pa.array(labels, type=pa.int8()))
    pq.write_table(table, d / "val.parquet")
    with pytest.raises(DataError, match="null"):
        read_split(d, "val")
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[1][22] = "True"
    with pytest.raises(DataError, match="is_fraud, or values not"):
        minimise(frame(rows))


# ------------------------------------------------------------------ verifier pass 3 regressions


def _zip_with(method: int, body: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=method) as zf:
        zf.writestr("a.csv", body)
    return buf.getvalue()


@pytest.mark.parametrize("method", [zipfile.ZIP_LZMA, zipfile.ZIP_BZIP2])
def test_compression_outside_the_allow_list_is_refused(tmp_path: Path, method: int):
    body = b"alpha\n" * 100
    expected = {"a.csv": hashlib.sha256(body).hexdigest()}
    with pytest.raises(DataError, match="refuses"):
        extract_source(io.BytesIO(_zip_with(method, body)), tmp_path / "r", expected)
    assert list((tmp_path / "r").iterdir()) == []


def test_any_failure_inside_a_member_is_a_data_error_and_keeps_nothing(tmp_path: Path, monkeypatch):
    body = b"alpha\n" * 100
    expected = {"a.csv": hashlib.sha256(body).hexdigest()}

    class Boom(Exception):
        pass

    def broken_read(self, n=-1):
        raise Boom("an error type nobody listed")

    monkeypatch.setattr(zipfile.ZipExtFile, "read", broken_read)
    with pytest.raises(DataError, match="corrupt"):
        extract_source(io.BytesIO(_zip_with(zipfile.ZIP_DEFLATED, body)), tmp_path / "r", expected)
    assert list((tmp_path / "r").iterdir()) == []


def test_encrypted_members_are_refused(tmp_path: Path):
    body = b"alpha\n"
    archive = bytearray(_zip_with(zipfile.ZIP_STORED, body))
    archive[6] |= 0x1  # local header flag
    cd = archive.rfind(b"PK\x01\x02")
    archive[cd + 8] |= 0x1  # central directory flag
    with pytest.raises(DataError, match="encrypted"):
        extract_source(
            io.BytesIO(bytes(archive)), tmp_path / "r", {"a.csv": hashlib.sha256(body).hexdigest()}
        )


def test_the_partial_file_never_follows_a_symlink(tmp_path: Path):
    body = b"alpha\n"
    raw = tmp_path / "r"
    raw.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    (raw / ".a.csv.part").symlink_to(victim)
    extract_source(
        io.BytesIO(_zip_with(zipfile.ZIP_DEFLATED, body)),
        raw,
        {"a.csv": hashlib.sha256(body).hexdigest()},
    )
    assert victim.read_text(encoding="utf-8") == "keep me"
    assert (raw / "a.csv").read_bytes() == body and not (raw / "a.csv").is_symlink()


def test_kaggle_json_values_must_be_text(tmp_path: Path):
    (tmp_path / ".kaggle").mkdir()
    for creds in ('{"username": "u", "key": null}', '{"username": 1, "key": "k"}', "[1, 2]"):
        (tmp_path / ".kaggle" / "kaggle.json").write_text(creds, "utf-8")
        with pytest.raises(DataError, match="text 'username' and 'key'"):
            kaggle_auth({}, tmp_path)


def test_unreadable_parquet_and_reference_are_data_errors(
    loaded, tmp_path: Path, monkeypatch, capsys
):
    raw, expected, result = loaded
    (result.data_dir / "val.parquet").write_bytes(b"not parquet")
    with pytest.raises(DataError, match="readable parquet"):
        read_split(result.data_dir, "val")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="ascii")
    monkeypatch.setattr(data, "SOURCE_SHA256", expected)
    monkeypatch.setattr(cli, "REFERENCE_MANIFEST", bad)
    args = ["load", "--offline", "--raw-dir", str(raw), "--data-dir", str(tmp_path / "o")]
    assert cli.main(args) == 1
    assert "not valid ASCII JSON" in capsys.readouterr().err


# ------------------------------------------------------------------ verifier pass 4 regressions


def test_no_partial_file_follows_a_symlink(tmp_path: Path, loaded):
    raw = tmp_path / "raw2"
    raw.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    (raw / ".download.zip.part").symlink_to(outside)
    with pytest.raises(DataError):
        download_source(
            raw,
            auth="Bearer FAKE",
            expected={"a.csv": "0" * 64},
            fetch=lambda r: io.BytesIO(b"PK-not-really-a-zip"),
        )
    assert outside.read_text(encoding="utf-8") == "keep"
    _, _, result = loaded
    splits = {n: read_split(result.data_dir, n) for n in ("train", "val", "test")}
    out = tmp_path / "out"
    out.mkdir()
    for name in (".train.parquet.part", ".splits.json.part"):
        (out / name).symlink_to(outside)
    data.write_splits(splits, out, {"x": "y"})
    assert outside.read_text(encoding="utf-8") == "keep"
    assert read_split(out, "train").equals(splits["train"])


def test_read_split_refuses_any_schema_or_field_metadata(loaded):
    _, _, result = loaded
    d = result.data_dir
    df = read_split(d, "val")
    assert pq.read_schema(d / "val.parquet").metadata is None  # written with none
    with_pandas = pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False)
    pq.write_table(with_pandas, d / "val.parquet")
    with pytest.raises(DataError, match="metadata"):
        read_split(d, "val")
    plain = with_pandas.replace_schema_metadata(None)
    fields = [
        f.with_metadata({"note": "4111111111111111"}) if f.name == "merchant" else f
        for f in plain.schema
    ]
    pq.write_table(plain.cast(pa.schema(fields)), d / "val.parquet")
    with pytest.raises(DataError, match="metadata"):
        read_split(d, "val")
    write_split(df, d / "val.parquet")
    assert read_split(d, "val").equals(df)


def test_minimise_refuses_digit_runs_non_ascii_digits_and_non_text():
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][3] = "fraud_4532015112830366"
    with pytest.raises(DataError, match="not plain text"):
        minimise(frame(rows))
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][1] = "٢٠١٩-10-01 00:00:18"
    with pytest.raises(DataError, match="timestamp"):
        minimise(frame(rows))
    raw = frame(raw_rows(datetime(2019, 10, 1), 10))
    raw.loc[3, "cc_num"] = None
    with pytest.raises(DataError, match="must be text"):
        minimise(raw)
    raw = frame(raw_rows(datetime(2019, 10, 1), 10))
    raw["amt"] = True
    with pytest.raises(DataError, match="must be text"):
        minimise(raw)


def test_a_malformed_reference_is_refused_not_skipped(tmp_path: Path):
    raw, expected = make_source(tmp_path)
    for ref in ({}, {"splits": []}, {"splits": {"train": {}}}):
        with pytest.raises(DataError, match="reference manifest"):
            load(raw, tmp_path / "d", expected=expected, reference=ref)
        assert not (tmp_path / "d").exists()


def test_any_opener_error_is_reported_by_type_only(tmp_path: Path):
    def fetch(req):
        raise RuntimeError("Bearer FAKE-SECRET-5")

    with pytest.raises(DataError) as exc:
        download_source(tmp_path, auth="Bearer FAKE-SECRET-5", expected={"a": "0"}, fetch=fetch)
    assert "FAKE-SECRET" not in str(exc.value) and "RuntimeError" in str(exc.value)


def test_credentials_are_read_only_when_a_download_is_needed(tmp_path: Path):
    good = {"fraudTrain.csv": b"t\n", "fraudTest.csv": b"s\n"}
    expected = {n: hashlib.sha256(b).hexdigest() for n, b in good.items()}
    for n, b in good.items():
        (tmp_path / n).write_bytes(b)

    def auth():
        raise AssertionError("credentials read although nothing was downloaded")

    download_source(tmp_path, auth=auth, expected=expected)


# ------------------------------------------------------------------ verifier pass 5 regressions


def test_object_booleans_wide_digits_and_zero_width_ids_are_refused():
    import numpy as np

    raw = frame(raw_rows(datetime(2019, 10, 1), 10))
    raw["amt"] = pd.Series([True, *raw["amt"][1:]], dtype=object)
    with pytest.raises(DataError, match="must be text"):
        minimise(raw)
    raw = frame(raw_rows(datetime(2019, 10, 1), 10))
    raw["amt"] = pd.Series([np.True_, *raw["amt"][1:]], dtype=object)
    with pytest.raises(DataError, match="must be text"):
        minimise(raw)
    for digits in ("\u0661" * 13, "\uff11" * 12):
        rows = raw_rows(datetime(2019, 10, 1), 10)
        rows[2][3] = "fraud_A" + digits
        with pytest.raises(DataError, match="not plain text"):
            minimise(frame(rows))
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][18] = "\u200b"
    with pytest.raises(DataError, match="empty values in trans_num"):
        minimise(frame(rows))


def test_compare_manifests_covers_version_and_label(loaded):
    _, _, result = loaded
    other = json.loads(json.dumps(result.manifest))
    other["data_label"] = "real"
    other["schema_version"] = "2.0"
    problems = compare_manifests(result.manifest, other)
    assert any(p.startswith("data_label") for p in problems)
    assert any(p.startswith("schema_version") for p in problems)


# ------------------------------------------------------------------ verifier pass 6 regressions


def test_an_invisible_merchant_name_or_split_digit_run_is_refused(tmp_path: Path):
    for name, message in (
        ("fraud_​", "empty after removing"),
        ("fraud_...", "empty after removing"),
        ("fraud_Acme 123456​789012", "not plain text"),
    ):
        rows = raw_rows(datetime(2019, 10, 1), 10)
        rows[2][3] = name
        raw, expected = make_source(tmp_path, rows, raw_rows(datetime(2020, 9, 1), 5, 500))
        with pytest.raises(DataError, match=message):
            load(raw, tmp_path / "d", expected=expected)


def test_a_kaggle_username_with_a_colon_is_refused(tmp_path: Path):
    (tmp_path / ".kaggle").mkdir()
    (tmp_path / ".kaggle" / "kaggle.json").write_text('{"username": "a:b", "key": "k"}', "utf-8")
    with pytest.raises(DataError, match="may not contain"):
        kaggle_auth({}, tmp_path)


# ------------------------------------------------------------------ verifier pass 7 regressions


@pytest.mark.parametrize(
    "name",
    [
        "fraud_" + "️".join("4111111111111111"),  # variation selectors between digits
        "fraud_" + "͏".join("4111111111111111"),  # combining grapheme joiner
        "fraud_" + "".join(d + "⃣" for d in "411111111111"),  # keycaps
        "fraud_ㅤ",  # Hangul filler: a letter by category, blank on screen
        "fraud_Acme 7",  # any digit at all
        "fraud_Café",  # non-ASCII
    ],
)
def test_merchant_names_must_be_plain_ascii_text(name: str):
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][3] = name
    with pytest.raises(DataError, match="not plain text"):
        minimise(frame(rows))


def test_ids_and_amounts_must_have_the_source_shape():
    for col, value in ((2, "4111-1111-1111-1111"), (2, "1234567890"), (18, "ABCDEF")):
        rows = raw_rows(datetime(2019, 10, 1), 10)
        rows[2][col] = value
        with pytest.raises(DataError, match="expected form"):
            minimise(frame(rows))
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][5] = 4111111111111111
    with pytest.raises(DataError, match="amt, or values not"):
        minimise(frame(rows))


def test_a_failed_rename_leaves_no_partial_file(tmp_path: Path):
    body = b"alpha\n"
    raw = tmp_path / "r"
    (raw / "a.csv").mkdir(parents=True)  # the target name is a directory
    with pytest.raises(IsADirectoryError):
        extract_source(
            io.BytesIO(_zip_with(zipfile.ZIP_DEFLATED, body)),
            raw,
            {"a.csv": hashlib.sha256(body).hexdigest()},
        )
    assert sorted(p.name for p in raw.iterdir()) == ["a.csv"]


def test_cli_names_an_unexpected_error_without_a_traceback(tmp_path: Path, monkeypatch, capsys):
    raw, expected = make_source(tmp_path)
    monkeypatch.setattr(data, "SOURCE_SHA256", expected)
    monkeypatch.setattr(cli, "REFERENCE_MANIFEST", tmp_path / "none.json")

    def boom(*a, **k):
        raise ValueError("pandas could not parse")

    monkeypatch.setattr(data, "_read_raw", boom)
    args = ["load", "--offline", "--raw-dir", str(raw), "--data-dir", str(tmp_path / "o")]
    assert cli.main(args) == 1
    assert "unexpected ValueError" in capsys.readouterr().err


# ------------------------------------------------------------------ verifier pass 8 regressions


@pytest.mark.parametrize(
    "amount", ["0.000630423337322", "0.1234567890123456", "1e3", " 5", "12345678"]
)
def test_amounts_must_be_dollars_and_cents(amount: str, tmp_path: Path):
    rows = raw_rows(datetime(2019, 10, 1), 10)
    rows[2][5] = amount
    raw, expected = make_source(tmp_path, rows, raw_rows(datetime(2020, 9, 1), 5, 500))
    with pytest.raises(DataError, match="amt, or values not"):
        load(raw, tmp_path / "d", expected=expected)


def test_compare_manifests_names_extra_keys(loaded):
    _, _, result = loaded
    other = {**json.loads(json.dumps(result.manifest)), "extra": 1}
    assert any(p.startswith("extra") for p in compare_manifests(result.manifest, other))


# ------------------------------------------------------------------ verifier pass 9 regressions


def test_compare_manifests_sees_extra_splits_null_keys_and_type_changes(loaded):
    _, _, result = loaded
    m = result.manifest

    def changed(edit):
        other = json.loads(json.dumps(m))
        edit(other)
        return compare_manifests(m, other)

    assert changed(lambda o: o["splits"].update(holdout={"rows": 5}))
    assert changed(lambda o: o.update(extra=None))
    assert changed(lambda o: o["splits"]["train"].update(note=None))
    assert changed(lambda o: o["splits"]["train"].update(rows=float(o["splits"]["train"]["rows"])))
    assert (
        changed(lambda o: o["splits"]["val"].update(fraud=True)) or m["splits"]["val"]["fraud"] != 1
    )
    assert compare_manifests(m, json.loads(json.dumps(m))) == []
    assert any(
        p.startswith("train.rows")
        for p in changed(
            lambda o: o["splits"]["train"].update(rows=o["splits"]["train"]["rows"] + 1)
        )
    )


def test_a_row_with_an_extra_field_is_not_read_as_an_index(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    rows = [[*r, "extra"] for r in raw_rows(datetime(2019, 10, 1), 20)]
    write_csv(raw / "fraudTrain.csv", rows)
    write_csv(raw / "fraudTest.csv", raw_rows(datetime(2020, 9, 1), 5, 500))
    expected = {n: data.sha256_file(raw / n) for n in ("fraudTrain.csv", "fraudTest.csv")}
    with pytest.raises(DataError, match="header's fields"):
        load(raw, tmp_path / "d", expected=expected)


# ------------------------------------------------------------------ verifier pass 10 regressions


def test_deeply_nested_json_is_a_data_error(tmp_path: Path, loaded):
    deep = "[" * 200_000 + "]" * 200_000
    (tmp_path / ".kaggle").mkdir()
    (tmp_path / ".kaggle" / "kaggle.json").write_text(deep, "utf-8")
    with pytest.raises(DataError, match="username"):
        kaggle_auth({}, tmp_path)
    _, _, result = loaded
    (result.data_dir / MANIFEST).write_text(deep, encoding="ascii")
    with pytest.raises(DataError, match="not valid JSON"):
        read_manifest(result.data_dir)


def test_the_source_is_parsed_from_the_bytes_that_were_hashed(tmp_path: Path, monkeypatch):
    raw, expected = make_source(tmp_path)
    real_read_raw = data._read_raw

    def swap_then_read(name: str, content: bytes):
        (raw / name).write_bytes(b"swapped after hashing\n")  # changing the file is too late
        return real_read_raw(name, content)

    monkeypatch.setattr(data, "_read_raw", swap_then_read)
    result = load(raw, tmp_path / "d", expected=expected)
    assert result.written and result.manifest["splits"]["train"]["rows"] > 0


def test_the_recorded_row_count_must_be_an_integer(loaded):
    _, _, result = loaded
    d = result.data_dir
    manifest = json.loads((d / MANIFEST).read_text(encoding="ascii"))
    manifest["splits"]["test"]["rows"] = float(manifest["splits"]["test"]["rows"])
    (d / MANIFEST).write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(DataError, match="rows"):
        read_split(d, "test")
