"""A2.1: point-in-time features, baseline, D-9 threshold, alerts, samples and SHAP drivers, on
small synthetic splits in N1.2's stored format (the real data is not needed in CI)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import numpy as np
import pandas as pd
import pytest

from fraudreview import baseline as bl
from fraudreview.baseline import (
    MODEL_FEATURES,
    BaselineError,
    choose_threshold,
    drivers,
    model_frame,
    model_text,
    run_baseline,
    score,
    shap_values,
    train,
)
from fraudreview.data import COLUMNS, SPLITS, alert_id, opaque_id, write_splits
from fraudreview.features import add_features, build_features, ordered_history
from fraudreview.schemas import AlertCase, FeatureName, Features
from fraudreview.stats import wilson
from fraudreview.truth import Alert, AlertTruth

CATS = ["grocery_pos", "shopping_net", "gas_transport", "misc_net", "travel"]


def make_split(start: datetime, n: int, seed: int, offset: int) -> pd.DataFrame:
    """A split with learnable fraud: night-time, large, online purchases are riskier."""
    rng = np.random.default_rng(seed)
    hours = rng.integers(0, 24, n)
    ts = [start + timedelta(minutes=37 * i, hours=int(h)) for i, h in enumerate(hours)]
    cats = rng.choice(CATS, n)
    amt = np.round(rng.gamma(2.0, 40.0, n) + 1, 2)
    risk = 0.02 + 0.25 * (hours < 4) + 0.2 * (amt > 200) + 0.1 * np.isin(cats, ["shopping_net"])
    fraud = (rng.random(n) < risk).astype("int8")
    amt = np.where(fraud == 1, np.round(amt * 3 + 50, 2), amt)
    frame = pd.DataFrame(
        {
            "trans_id": [opaque_id("t-", f"syn{offset + i}") for i in range(n)],
            "card_id": [opaque_id("c-", f"card{rng.integers(0, 40)}") for _ in range(n)],
            "ts": pd.to_datetime(ts).astype("datetime64[us]"),
            "merchant": [f"Shop {chr(65 + int(k))}" for k in rng.integers(0, 12, n)],
            "category": cats,
            "amt": amt.astype("float64"),
            "is_fraud": fraud,
        }
    )
    return frame.sort_values("ts", kind="mergesort").reset_index(drop=True).loc[:, list(COLUMNS)]


def make_splits() -> dict[str, pd.DataFrame]:
    return {
        "train": make_split(datetime(2019, 1, 1), 4000, 1, 0),
        "val": make_split(datetime(2020, 1, 1), 2000, 2, 10_000),
        "test": make_split(datetime(2020, 7, 1), 2000, 3, 20_000),
    }


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("a21")
    splits = make_splits()
    write_splits(splits, root / "data", {"syn": "0"})
    report = run_baseline(root / "data", root / "out", 7, n_dev=10, n_eval=20)
    return {"root": root, "splits": splits, "report": report}


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------ BAS-01 features


def history_of(rows: list[tuple[str, str, str, float]]) -> pd.DataFrame:
    """rows: (card, category, merchant, amount) in time order, one minute apart."""
    t0 = datetime(2019, 3, 1, 9)
    frame = pd.DataFrame(
        {
            "trans_id": [f"t{i}" for i in range(len(rows))],
            "card_id": [r[0] for r in rows],
            "ts": pd.to_datetime([t0 + timedelta(minutes=i) for i in range(len(rows))]),
            "merchant": [r[2] for r in rows],
            "category": [r[1] for r in rows],
            "amt": [r[3] for r in rows],
            "is_fraud": [0] * len(rows),
            "split": "train",
        }
    )
    frame["order"] = np.arange(len(frame))
    return add_features(frame)


def test_feature_values_on_a_hand_made_history():
    h = history_of(
        [
            ("A", "travel", "M1", 10.0),
            ("A", "travel", "M1", 20.0),
            ("B", "home", "M9", 99.0),
            ("A", "home", "M2", 30.0),
            ("A", "travel", "M1", 40.0),  # 3 prior on card A: median(10, 20, 30) = 20
            ("A", "travel", "M3", 50.0),  # median(10, 20, 30, 40) = 25
        ]
    )
    a = h[h["card_id"] == "A"].reset_index(drop=True)
    assert list(a["card_history_n"]) == [0, 1, 2, 3, 4]
    assert list(a["merchant_first_seen"]) == [True, False, True, False, True]
    assert list(a["category_freq"]) == [0.0, 1.0, 0.0, 2 / 3, 3 / 4]
    assert pd.isna(np.asarray(a["amt_vs_usual"], dtype="float64")).tolist() == [
        True,
        True,
        True,
        False,
        False,
    ]
    assert a.loc[3, "amt_vs_usual"] == pytest.approx(40 / 20)
    assert a.loc[4, "amt_vs_usual"] == pytest.approx(50 / 25)
    b = h[h["card_id"] == "B"].iloc[0]
    assert (b["card_history_n"], b["merchant_first_seen"], b["category_freq"]) == (0, True, 0.0)
    assert list(h["hour"]) == [9] * 6


@pytest.mark.safety
def test_no_feature_reads_a_later_transaction():
    rng = np.random.default_rng(11)
    for trial in range(25):
        n = int(rng.integers(5, 60))
        rows = [
            (
                f"c{rng.integers(0, 4)}",
                str(rng.choice(CATS)),
                f"m{rng.integers(0, 5)}",
                float(rng.integers(1, 500)),
            )
            for _ in range(n)
        ]
        full = history_of(rows)
        cut = int(rng.integers(1, n))
        prefix = history_of(rows[:cut])
        cols = ["amt_vs_usual", "category_freq", "merchant_first_seen", "card_history_n", "hour"]
        pd.testing.assert_frame_equal(
            pd.DataFrame(full.loc[: cut - 1, cols]).reset_index(drop=True),
            pd.DataFrame(prefix.loc[:, cols]).reset_index(drop=True),
            obj=f"trial {trial}",
        )
        changed = [*rows[:cut], *[(c, k, m, a * 7 + 1) for c, k, m, a in rows[cut:]]]
        pd.testing.assert_frame_equal(
            history_of(changed).loc[: cut - 1, cols], full.loc[: cut - 1, cols]
        )


def test_history_runs_train_then_val_then_test_and_features_validate():
    splits = make_splits()
    h = build_features(splits)
    assert h["order"].is_monotonic_increasing
    order, split = np.asarray(h["order"]), np.asarray(h["split"])
    first = {k: int(order[split == k].min()) for k in SPLITS}
    assert first["train"] < first["val"] < first["test"]
    sampled: list[dict[str, Any]] = h.sample(200, random_state=1).to_dict("records")  # pyright: ignore[reportAssignmentType]
    for row in sampled:
        usual = row["amt_vs_usual"]
        Features(
            amt_vs_usual=None if pd.isna(usual) else float(usual),
            hour=int(row["hour"]),
            category_freq=float(row["category_freq"]),
            merchant_first_seen=bool(row["merchant_first_seen"]),
            card_history_n=int(row["card_history_n"]),
        )
    assert list(ordered_history(splits).columns)[-2:] == ["split", "order"]


# ------------------------------------------------------------------ BAS-02 model


@pytest.mark.contract
def test_model_inputs_are_allow_listed_and_exclude_label_and_ids():
    assert set(MODEL_FEATURES) <= set(get_args(FeatureName))
    assert "risk_score" not in MODEL_FEATURES
    x = model_frame(build_features(make_splits()))
    assert tuple(x.columns) == MODEL_FEATURES
    assert not {"is_fraud", "trans_id", "card_id", "merchant", "ts"} & set(x.columns)


def test_training_is_deterministic_and_hashed(built):
    h = build_features(built["splits"])
    a, b = model_text(train(h, 7)), model_text(train(h, 7))
    assert a == b
    import hashlib

    stored = (built["root"] / "out" / "model.txt").read_text(encoding="utf-8")
    assert stored == a
    assert built["report"]["model_sha256"] == hashlib.sha256(a.encode("utf-8")).hexdigest()
    assert built["report"]["split_sha256"]["train"]  # trained on the recorded splits


# ------------------------------------------------------------------ BAS-03 threshold


def naive_threshold(s: np.ndarray, y: np.ndarray) -> tuple[float, bool]:
    cands = sorted(set(s.tolist()), reverse=True)

    def rp(t: float) -> tuple[float, float]:
        hit = s >= t
        return (y[hit].sum() / y.sum(), y[hit].sum() / hit.sum())

    i = next(k for k, t in enumerate(cands) if rp(t)[0] >= 0.98)
    if rp(cands[i])[1] <= 0.5:
        return cands[i], False
    j = next(k for k in range(i, len(cands)) if rp(cands[k])[1] <= 0.5)
    return cands[j], True


def test_threshold_matches_a_brute_force_reading_of_d9():
    rng = np.random.default_rng(5)
    for _ in range(200):
        n = int(rng.integers(20, 200))
        s = np.round(rng.random(n), int(rng.integers(1, 4)))  # rounding makes ties
        y = (rng.random(n) < rng.uniform(0.05, 0.9)).astype(int)
        if y.sum() == 0:
            continue
        try:
            want = naive_threshold(s, y)
        except StopIteration:
            with pytest.raises(BaselineError):
                choose_threshold(s, y)
            continue
        got = choose_threshold(s, y)
        assert (got.value, got.lowered) == want
        hit = s >= got.value
        assert got.recall == pytest.approx(y[hit].sum() / y.sum())
        assert got.precision == pytest.approx(y[hit].sum() / hit.sum())


def test_threshold_lowering_is_recorded_with_a_reason():
    s = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3])
    y = np.array([1, 1, 1, 0, 0, 0, 0])
    got = choose_threshold(s, y)
    assert got.lowered and got.value == 0.4 and got.precision <= 0.5 and got.first == 0.7
    assert "lowered" in got.reason and "D-9" in got.reason
    plain = choose_threshold(np.array([0.9, 0.8, 0.1]), np.array([0, 1, 0]))
    assert not plain.lowered and plain.value == 0.8 and plain.precision == 0.5
    assert "lowered" not in plain.reason
    with pytest.raises(BaselineError, match="no fraud"):
        choose_threshold(np.array([0.5]), np.array([0]))


def test_baseline_summary_records_val_and_test_with_intervals(built):
    s = built["report"]["summary"]
    th = built["report"]["threshold"]
    assert s["threshold"] == th["value"] and th["reason"]
    assert s["recall_val"] >= 0.98
    rt = s["recall_test"]
    assert wilson(rt["k"], rt["n"]).model_dump() == rt
    assert 0 < s["alert_rate_test"] <= 1


# ------------------------------------------------------------------ BAS-04 alerts and samples


def scored(built: dict[str, Any]) -> pd.DataFrame:
    h = build_features(built["splits"])
    booster = train(h, 7)
    h["score"] = score(booster, h)
    return h


def test_alerts_are_exactly_the_rows_at_or_above_the_threshold(built):
    h = scored(built)
    t = built["report"]["threshold"]["value"]
    mix = built["report"]["alert_mix"]
    for split, key in (("val", "val_all"), ("test", "test_all")):
        part = h[h["split"] == split]
        alerts = part[part["score"] >= t]
        assert mix[key]["alerts"] == len(alerts)
        assert mix[key]["fraud"] == int(np.asarray(alerts["is_fraud"]).sum())
    out = built["root"] / "out"
    ids = {r["alert_id"] for r in lines(out / "cases_dev.jsonl")}
    val_ids = {alert_id(x) for x in h[(h["split"] == "val") & (h["score"] >= t)]["trans_id"]}
    assert ids <= val_ids
    eval_ids = {r["alert_id"] for r in lines(out / "cases_eval.jsonl")}
    test_ids = {alert_id(x) for x in h[(h["split"] == "test") & (h["score"] >= t)]["trans_id"]}
    assert eval_ids <= test_ids and not ids & eval_ids
    assert len(ids) == 10 and len(eval_ids) == 20


def test_samples_follow_the_seed(built):
    root = built["root"]
    again = root / "again"
    other = root / "other"
    run_baseline(root / "data", again, 7, n_dev=10, n_eval=20)
    run_baseline(root / "data", other, 8, n_dev=10, n_eval=20)
    for f in ("cases_dev.jsonl", "cases_eval.jsonl", "truth_eval.jsonl", "baseline.json"):
        assert (root / "out" / f).read_bytes() == (again / f).read_bytes(), f
    assert (root / "out" / "cases_eval.jsonl").read_bytes() != (
        other / "cases_eval.jsonl"
    ).read_bytes()


def test_too_few_alerts_to_sample_is_refused(built):
    with pytest.raises(BaselineError, match="are needed"):
        run_baseline(built["root"] / "data", built["root"] / "big", 7, n_dev=10, n_eval=10**6)


def test_cases_carry_the_score_and_time_and_nothing_else(built):
    h = scored(built)
    records: list[dict[str, Any]] = h.to_dict("records")  # pyright: ignore[reportAssignmentType]
    by_id = {alert_id(r["trans_id"]): r for r in records}
    for name in ("dev", "eval"):
        for raw in lines(built["root"] / "out" / f"cases_{name}.jsonl"):
            case = AlertCase.model_validate_json(json.dumps(raw))
            row = by_id[case.alert_id]
            ts = pd.Timestamp(row["ts"])
            assert case.risk_score == pytest.approx(row["score"])
            assert (case.ts_hour, case.ts_dow) == (ts.hour, ts.day_of_week)
            assert case.amount == row["amt"] and case.category == row["category"]
            assert set(raw) == {
                "alert_id",
                "ts_hour",
                "ts_dow",
                "category",
                "amount",
                "features",
                "risk_score",
            }


# ------------------------------------------------------------------ BAS-05 SHAP


def test_drivers_are_the_top_five_by_absolute_value_with_sign():
    c = np.array([0.1, -0.9, 0.3, -0.05, 0.6, 0.2, -0.3, 1.5])  # last column: expected value
    d = drivers(c)
    # |0.3| ties between hour and card_history_n are broken by name
    assert [x.feature for x in d] == [
        "category",
        "category_freq",
        "card_history_n",
        "hour",
        "merchant_first_seen",
    ]
    assert [x.contribution for x in d] == [-0.9, 0.6, -0.3, 0.3, 0.2]


def test_truth_drivers_match_lightgbm_and_shap(built):
    import shap

    h = scored(built)
    booster = train(build_features(built["splits"]), 7)
    truths = [
        AlertTruth.model_validate_json(json.dumps(r))
        for r in lines(built["root"] / "out" / "truth_eval.jsonl")
    ]
    by_id = {alert_id(t): i for i, t in enumerate(h["trans_id"])}
    rows = h.iloc[[by_id[t.alert_id] for t in truths]]
    contribs = shap_values(booster, rows)
    explainer = shap.TreeExplainer(booster)
    via_shap = np.asarray(explainer.shap_values(model_frame(rows)))
    if via_shap.ndim == 3:
        via_shap = via_shap[-1]
    np.testing.assert_allclose(via_shap, contribs[:, : len(MODEL_FEATURES)], atol=1e-6)
    for t, c, (_, row) in zip(truths, contribs, rows.iterrows(), strict=True):
        assert t.shap_top5 == drivers(c)
        assert t.label == bool(row["is_fraud"])
        assert [abs(d.contribution) for d in t.shap_top5] == sorted(
            (abs(d.contribution) for d in t.shap_top5), reverse=True
        )


@pytest.mark.safety
def test_truth_and_view_files_are_separate_from_cases(built):
    out = built["root"] / "out"
    for name in ("dev", "eval"):
        text = (out / f"cases_{name}.jsonl").read_text(encoding="utf-8")
        for word in ('"label"', "shap", "contribution", '"merchant"', "trans_id", "card_id", '"c-'):
            assert word not in text, word
        views = [
            Alert.model_validate_json(json.dumps(r)) for r in lines(out / f"alerts_{name}.jsonl")
        ]
        cases = [
            AlertCase.model_validate_json(json.dumps(r)) for r in lines(out / f"cases_{name}.jsonl")
        ]
        assert [v.case for v in views] == cases
    assert set(bl.__dict__) >= {"run_baseline"}
    assert {p.name for p in out.iterdir()} >= {
        f"{k}_{s}.jsonl" for k in ("cases", "truth", "alerts") for s in ("dev", "eval")
    }
    assert all(name in SPLITS for name in built["report"]["split_sha256"])


def test_cli_baseline_runs_and_reports_with_the_simulated_label(
    built, tmp_path, capsys, monkeypatch
):
    from fraudreview import cli

    monkeypatch.setattr(bl, "N_DEV", 10)
    monkeypatch.setattr(bl, "N_EVAL", 20)

    def small(data_dir, out_dir, seed):
        return run_baseline(data_dir, out_dir, seed, n_dev=10, n_eval=20)

    monkeypatch.setattr(bl, "run_baseline", small)
    out = tmp_path / "b"
    args = ["baseline", "--data-dir", str(built["root"] / "data"), "--out", str(out)]
    assert cli.main(args) == 0
    text = capsys.readouterr().out
    assert "threshold" in text and all(
        "simulated" in line for line in text.splitlines() if "%" in line
    )
    assert (out / "baseline.json").is_file()
    bad = tmp_path / "bad.yaml"
    bad.write_text("seed: [", encoding="utf-8")
    assert cli.main([*args, "--config", str(bad)]) == 2
    assert cli.main(["baseline", "--data-dir", str(tmp_path / "none"), "--out", str(out)]) == 1
