"""Baseline model, D-9 threshold, alerts, samples and SHAP drivers (Tech Spec §2.2, A2.1).

The model sees exactly `MODEL_FEATURES`, all of them allow-listed field names, so its SHAP
drivers can be compared with the fields an agent cites (E2.2). Cases (what a model may read),
truth (label and drivers) and the analyst view are written to separate files (D-7, H5).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import lightgbm as lgb
import numpy as np
import pandas as pd

from fraudreview.data import SPLITS, alert_id, read_manifest, read_split
from fraudreview.features import build_features
from fraudreview.schemas import AlertCase, BaselineSummary, Category, Driver, Features
from fraudreview.stats import wilson
from fraudreview.truth import Alert, AlertTruth

MODEL_FEATURES = (
    "amount",
    "category",
    "hour",
    "amt_vs_usual",
    "category_freq",
    "merchant_first_seen",
    "card_history_n",
)
CATEGORIES = tuple(sorted(get_args(Category)))
MIN_RECALL = 0.98  # D-9
MAX_PRECISION = 0.5  # D-9: the queue must be mostly false positives
N_DEV, N_EVAL = 100, 500  # D-8
TOP_K = 5
PARAMS: dict[str, Any] = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 1.0,
    "bagging_fraction": 1.0,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "verbose": -1,
}
MAX_ROUNDS, EARLY_STOP = 2000, 50


class BaselineError(Exception):
    """The baseline cannot be built as specified (for example too few alerts to sample)."""


def _arr(frame: pd.DataFrame, column: str) -> np.ndarray:
    return np.asarray(frame[column])


def _where(frame: pd.DataFrame, mask: np.ndarray) -> pd.DataFrame:
    return frame.loc[mask]


def _utc(ts: Any) -> datetime:
    """The simulated time has no zone; it is stored as UTC (see DATA_CARD.md)."""
    return datetime.fromisoformat(pd.Timestamp(ts).isoformat()).replace(tzinfo=UTC)


def model_frame(history: pd.DataFrame) -> pd.DataFrame:
    """The model's inputs, in a fixed column order and with fixed category codes."""
    x = pd.DataFrame(
        {
            "amount": history["amt"].astype("float64"),
            "category": pd.Categorical(history["category"], categories=CATEGORIES),
            "hour": history["hour"].astype("int64"),
            "amt_vs_usual": history["amt_vs_usual"].astype("float64"),
            "category_freq": history["category_freq"].astype("float64"),
            "merchant_first_seen": history["merchant_first_seen"].astype("int64"),
            "card_history_n": history["card_history_n"].astype("int64"),
        }
    )
    if tuple(x.columns) != MODEL_FEATURES:
        raise AssertionError("model columns out of order")
    return x


def train(history: pd.DataFrame, seed: int) -> lgb.Booster:
    """LightGBM on train, early stopping on validation; deterministic for a given seed (BAS-02)."""
    params = {**PARAMS, "seed": seed}
    split = _arr(history, "split")
    tr, va = _where(history, split == "train"), _where(history, split == "val")
    dtrain = lgb.Dataset(model_frame(tr), label=_arr(tr, "is_fraud"), free_raw_data=False)
    dval = lgb.Dataset(model_frame(va), label=_arr(va, "is_fraud"), reference=dtrain)
    return lgb.train(
        params,
        dtrain,
        num_boost_round=MAX_ROUNDS,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)],
    )


def model_text(booster: lgb.Booster) -> str:
    return booster.model_to_string(num_iteration=booster.best_iteration or -1)


def score(booster: lgb.Booster, history: pd.DataFrame) -> np.ndarray:
    return np.asarray(
        booster.predict(model_frame(history), num_iteration=booster.best_iteration or -1),
        dtype="float64",
    )


@dataclass(frozen=True)
class Threshold:
    value: float
    recall: float
    precision: float
    lowered: bool  # True when D-9's "mostly false positives" rule moved it down
    reason: str
    first: float  # the 98%-recall threshold before any lowering


def choose_threshold(
    scores: Sequence[float] | np.ndarray, labels: Sequence[int] | np.ndarray
) -> Threshold:
    """D-9 on validation: the highest threshold whose recall is at least 98%; if more than half
    of the alerts at that threshold are fraud, lower it until at most half are."""
    s = np.asarray(scores, dtype="float64")
    y = np.asarray(labels, dtype="int64")
    if s.shape != y.shape or s.size == 0:
        raise BaselineError("scores and labels must be non-empty and the same length")
    positives = int(y.sum())
    if positives == 0:
        raise BaselineError("validation has no fraud; recall is undefined")
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    # alerts are score >= t, so a threshold sits at a distinct score value: group ties
    distinct, first = np.unique(-s_sorted, return_index=True)
    ends = np.append(first[1:], s_sorted.size)  # alert count when t = that distinct score
    caught = np.cumsum(y_sorted)[ends - 1]
    recalls, precisions = caught / positives, caught / ends
    i = int(np.argmax(recalls >= MIN_RECALL))  # first (highest) score reaching 98% recall
    first = float(-distinct[i])
    reason = f"highest threshold with validation recall >= {MIN_RECALL:.0%}"
    lowered = False
    if precisions[i] > MAX_PRECISION:
        later = np.nonzero(precisions[i:] <= MAX_PRECISION)[0]
        if later.size == 0:
            raise BaselineError("no threshold makes the queue mostly false positives")
        i += int(later[0])
        lowered = True
        reason += (
            f"; precision was above {MAX_PRECISION:.0%}, so it was lowered to the highest "
            f"threshold where at most half of alerts are fraud (D-9)"
        )
    return Threshold(
        value=float(-distinct[i]),
        recall=float(recalls[i]),
        precision=float(precisions[i]),
        lowered=lowered,
        reason=reason,
        first=first,
    )


def _unit(risk: float) -> float:
    if not (math.isfinite(risk) and 0.0 <= risk <= 1.0):
        raise BaselineError(f"model score {risk!r} is not a probability")
    return float(risk)


def case_for(row: Mapping[str, Any], risk: float) -> AlertCase:
    usual = row["amt_vs_usual"]
    ts = row["ts"]
    return AlertCase(
        alert_id=alert_id(row["trans_id"]),
        ts_hour=int(ts.hour),
        ts_dow=int(ts.day_of_week),
        category=row["category"],
        amount=float(row["amt"]),
        features=Features(
            amt_vs_usual=None if pd.isna(usual) else float(usual),
            hour=int(row["hour"]),
            category_freq=float(row["category_freq"]),
            merchant_first_seen=bool(row["merchant_first_seen"]),
            card_history_n=int(row["card_history_n"]),
        ),
        risk_score=_unit(risk),
    )


def drivers(contribs: np.ndarray) -> tuple[Driver, ...]:
    """Top five model inputs by absolute SHAP value, with sign, largest first (ties by name)."""
    ranked = sorted(
        zip(MODEL_FEATURES, contribs[: len(MODEL_FEATURES)], strict=True),
        key=lambda fc: (-abs(fc[1]), fc[0]),
    )
    return tuple(Driver(feature=f, contribution=float(c)) for f, c in ranked[:TOP_K])


def shap_values(booster: lgb.Booster, rows: pd.DataFrame) -> np.ndarray:
    """Exact TreeSHAP contributions (log-odds) from LightGBM itself; the last column is the
    expected value."""
    return np.asarray(
        booster.predict(
            model_frame(rows), num_iteration=booster.best_iteration or -1, pred_contrib=True
        ),
        dtype="float64",
    )


def sample(alerts: pd.DataFrame, n: int, rng: np.random.Generator, what: str) -> pd.DataFrame:
    if len(alerts) < n:
        raise BaselineError(f"only {len(alerts)} {what} alerts; {n} are needed")
    pick = rng.choice(len(alerts), size=n, replace=False)
    return alerts.iloc[np.sort(pick)]


def _mix(frame: pd.DataFrame) -> dict[str, Any]:
    n = len(frame)
    fraud = int(_arr(frame, "is_fraud").sum())
    return {"alerts": n, "fraud": fraud, "fraud_share": round(fraud / n, 6) if n else 0.0}


def _write_lines(path: Path, lines: Sequence[str]) -> None:
    tmp = path.with_name(f".{path.name}.part")
    try:
        tmp.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def run_baseline(
    data_dir: Path,
    out_dir: Path,
    seed: int,
    *,
    n_dev: int = N_DEV,
    n_eval: int = N_EVAL,
) -> dict[str, Any]:
    """Build features, train, threshold, alert, sample, explain and write everything (A2.1)."""
    splits = {name: read_split(data_dir, name) for name in SPLITS}  # hash-checked (H6)
    manifest = read_manifest(data_dir)
    history = build_features(splits)
    booster = train(history, seed)
    text = model_text(booster)
    history["score"] = score(booster, history)

    split = _arr(history, "split")
    val, test = _where(history, split == "val"), _where(history, split == "test")
    th = choose_threshold(_arr(val, "score"), _arr(val, "is_fraud"))
    val_alerts = _where(val, _arr(val, "score") >= th.value)
    test_alerts = _where(test, _arr(test, "score") >= th.value)
    test_fraud = int(_arr(test, "is_fraud").sum())
    caught = int(_arr(test_alerts, "is_fraud").sum())
    summary = BaselineSummary(
        threshold=th.value,
        recall_val=th.recall,
        precision_val=th.precision,
        recall_test=wilson(caught, test_fraud),
        precision_test=wilson(caught, len(test_alerts)),
        alert_rate_test=len(test_alerts) / len(test),
    )

    rng = np.random.default_rng(seed)
    dev = sample(val_alerts, n_dev, rng, "validation")
    evaluation = sample(test_alerts, n_eval, rng, "test")
    # a second, disjoint eval sample for a run after any change to the frozen settings (Tech
    # Spec §5 step 6, EvalKit CAL-05); drawn last, so the first two samples are unchanged
    rest = test_alerts.drop(index=evaluation.index)
    evaluation2 = sample(rest, n_eval, rng, "remaining test")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("dev", dev), ("eval", evaluation), ("eval2", evaluation2)):
        contribs = shap_values(booster, rows)
        cases, truths, views = [], [], []
        records: list[dict[str, Any]] = rows.to_dict("records")  # pyright: ignore[reportAssignmentType]
        for row, c in zip(records, contribs, strict=True):
            case = case_for(row, float(row["score"]))
            truth = AlertTruth(
                alert_id=case.alert_id, label=bool(row["is_fraud"]), shap_top5=drivers(c)
            )
            view = Alert(
                case=case,
                truth=truth,
                merchant=row["merchant"],
                trans_ts=_utc(row["ts"]),
            )
            cases.append(case.model_dump_json())
            truths.append(truth.model_dump_json())
            views.append(view.model_dump_json())
        _write_lines(out_dir / f"cases_{name}.jsonl", cases)
        _write_lines(out_dir / f"truth_{name}.jsonl", truths)
        _write_lines(out_dir / f"alerts_{name}.jsonl", views)
    (out_dir / "model.txt").write_text(text, encoding="utf-8")
    report = {
        "schema_version": "1.0",
        "data_label": "simulated",
        "seed": seed,
        "model_features": list(MODEL_FEATURES),
        "model_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "best_iteration": int(booster.best_iteration or 0),
        "split_sha256": {n: manifest["splits"][n]["sha256"] for n in SPLITS},
        "threshold": {
            "value": th.value,
            "lowered": th.lowered,
            "before_lowering": th.first,
            "reason": th.reason,
        },
        "summary": json.loads(summary.model_dump_json()),
        "alert_mix": {
            "val_all": _mix(val_alerts),
            "test_all": _mix(test_alerts),
            "dev_sample": _mix(dev),
            "eval_sample": _mix(evaluation),
            "eval2_sample": _mix(evaluation2),
        },
    }
    if not all(math.isfinite(v) for v in (th.value, th.recall, th.precision)):
        raise BaselineError("non-finite threshold result")
    _write_lines(out_dir / "baseline.json", [json.dumps(report, indent=2, sort_keys=True)])
    return report
