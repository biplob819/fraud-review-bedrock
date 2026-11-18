"""Monitoring and improvement, local versions (E2.4, Tech Spec §5 step 3, OWNER D-36).

- MON-01 run history: one line per evaluation (`run_history_line`), appended to
  `docs/measurements/runs.jsonl` with a single O_APPEND write (`append_history`).
- MON-02 alarm bands from config.yaml's `alarms:` block (`check_bands`). Leakage above 0 is always
  a failure, whatever the bands say (OWNER D-10).
- MON-03 a minimal PSI drift check on the agent-visible numeric features (`psi`, `drift`).
- MON-04 trend series for the dashboard (`trend`).
- IMP-01 experiment ledger `docs/measurements/experiments.jsonl` (`append_experiment`).
- IMP-02 the keep-or-revert rule for a dev-set change (`accept_change`). EvalKit's paired
  bootstrap with Holm (v0.3.0) plugs in through the `paired` argument; it is not reimplemented.
- IMP-03 freeze guard (`freeze_guard`): changed settings after an eval run need a fresh sample.

EvalKit's monitoring (MON-05, v0.4.0) replaces the run history, bands, PSI and trend here once it
ships; until then these stay minimal. Everything is pure apart from the two file appends.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NamedTuple

import numpy as np

from fraudreview.config import AlarmBand
from fraudreview.schemas import AlertCase

RUNS = Path("docs") / "measurements" / "runs.jsonl"
EXPERIMENTS = Path("docs") / "measurements" / "experiments.jsonl"

Level = Literal["warn", "fail"]
DriftLevel = Literal["ok", "warn", "alarm"]
Verdict = Literal["keep", "revert"]
VERDICTS: tuple[Verdict, ...] = ("keep", "revert")

PSI_EPSILON = 1e-4  # floor for an empty bin's share, so the log term stays finite
PSI_WARN = 0.1
PSI_ALARM = 0.25
AUTO_CLEAR_TOLERANCE = 0.02  # IMP-02: a kept change may lower dev auto-clear by 2 points at most

# agent-visible numeric features (AlertCase, D-7); features.hour repeats ts_hour
DRIFT_FEATURES = (
    "amount",
    "risk_score",
    "ts_hour",
    "ts_dow",
    "amt_vs_usual",
    "category_freq",
    "card_history_n",
)
TREND_METRICS = (
    "leakage",
    "leakage_hi",
    "auto_clear_rate",
    "auto_clear_precision",
    "cost_per_1000_usd",
    "schema_error_rate",
    "refusal_rate",
    "latency_p95_stage2_ms",
)


# ------------------------------------------------------------------ MON-01 run history


def _rate(r: Any, *, lo: bool = True) -> dict[str, Any]:
    """A `Rate` (or its dict form) as a compact dict with 4-decimal bounds."""
    d: Mapping[str, Any] = r if isinstance(r, Mapping) else r.model_dump()
    out = {"k": d["k"], "n": d["n"], "point": round(d["point"], 4)}
    if lo:
        out["lo"] = round(d["lo"], 4)
    out["hi"] = round(d["hi"], 4)
    return out


def _share(count: int, n: int) -> float:
    return round(count / n, 6) if n else 0.0


def run_history_line(
    run_id: str, fields: Mapping[str, Any], facts: Mapping[str, Any]
) -> dict[str, Any]:
    """MON-01: one history line from `metrics.compute` fields and the run's `run.json` facts
    (`freeze.digest` and `errors`). Error rates are per scored alert."""
    n = fields["n_alerts"]
    errors = facts.get("errors") or {}
    lat = fields.get("latency_ms") or {}
    stage2 = lat.get("stage2")
    cost = fields.get("cost_per_1000_usd")
    return {
        "run_id": run_id,
        "digest": facts["freeze"]["digest"],
        "source": "fraud evaluate",
        "n": n,
        "frauds": fields["n_frauds"],
        "leakage": _rate(fields["leakage"], lo=False),
        "auto_clear_rate": _rate(fields["auto_clear_rate"]),
        "auto_clear_precision": _rate(fields["auto_clear_precision"]),
        "cost_per_1000_usd": None if cost is None else round(cost.point, 4),
        "cost_basis": "decision_cost",
        "schema_error_rate": _share(errors.get("schema", 0), n),
        "refusal_rate": _share(errors.get("refusal", 0), n),
        "latency_p95_stage2_ms": None if stage2 is None else int(stage2.p95),
    }


def history_from_eval_row(row: Mapping[str, Any], source: str = "eval-runs.jsonl") -> dict:
    """The history line for a committed `eval-runs.jsonl` row (the backfill). Cost per 1,000 is
    the billed spend over the run's alerts, since those rows carry no per-alert decision cost."""
    n = row["n"]
    errors = row.get("errors") or {}
    stage2 = (row.get("latency_ms") or {}).get("stage2")
    return {
        "run_id": row["run_id"],
        "digest": row["digest"],
        "source": source,
        "n": n,
        "frauds": row["frauds"],
        "leakage": _rate(row["leakage"], lo=False),
        "auto_clear_rate": _rate(row["auto_clear_rate"]),
        "auto_clear_precision": _rate(row["auto_clear_precision"]),
        "cost_per_1000_usd": round(1000 * row["billed_usd"] / n, 4) if n else None,
        "cost_basis": "billed_usd",
        "schema_error_rate": _share(errors.get("schema", 0), n),
        "refusal_rate": _share(errors.get("refusal", 0), n),
        "latency_p95_stage2_ms": None if stage2 is None else int(stage2["p95"]),
    }


def _append(path: Path | str, line: Mapping[str, Any]) -> None:
    data = (json.dumps(line, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        written = os.write(fd, data)  # one write: concurrent appenders cannot interleave
    finally:
        os.close(fd)
    if written != len(data):
        raise OSError(f"{path}: short append ({written} of {len(data)} bytes)")


def append_history(path: Path | str, line: Mapping[str, Any]) -> None:
    """MON-01: append one history line (a single O_APPEND write)."""
    _append(path, line)


def append_history_once(path: Path | str, line: Mapping[str, Any]) -> bool:
    """MON-01: append the line unless that run (same id and digest) is already in the history.
    True when a line was added, so scoring one run twice leaves one line."""
    if any(
        row.get("run_id") == line.get("run_id") and row.get("digest") == line.get("digest")
        for row in read_jsonl(path)
    ):
        return False
    append_history(path, line)
    return True


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------------------ MON-02 alarm bands


class Alarm(NamedTuple):
    metric: str
    level: Level
    value: float
    band: AlarmBand | None  # None for the built-in leakage rule


def metric_value(line: Mapping[str, Any], metric: str) -> float | None:
    """A band metric's value in a history line (the point of a rate); None when absent."""
    value = line.get(metric)
    if isinstance(value, Mapping):
        if value.get("n") == 0:  # a rate over nothing is no value, not 0 (no auto-clears at all)
            return None
        value = value.get("point")
    return None if value is None else float(value)


def _band_level(band: AlarmBand, value: float) -> Level | None:
    if (band.fail_above is not None and value > band.fail_above) or (
        band.fail_below is not None and value < band.fail_below
    ):
        return "fail"
    if (band.warn_above is not None and value > band.warn_above) or (
        band.warn_below is not None and value < band.warn_below
    ):
        return "warn"
    return None


def check_bands(line: Mapping[str, Any], bands: Iterable[AlarmBand]) -> list[Alarm]:
    """MON-02: the alarms a history line raises. Leakage above 0 is always "fail" (OWNER D-10),
    whatever the bands say; a band cannot soften it."""
    alarms: list[Alarm] = []
    leak_band: AlarmBand | None = None
    for band in bands:
        if band.metric == "leakage":
            leak_band = band
            continue
        value = metric_value(line, band.metric)
        if value is None:
            continue
        level = _band_level(band, value)
        if level is not None:
            alarms.append(Alarm(band.metric, level, value, band))
    leak = line["leakage"]
    leak_value = float(leak["point"])
    if leak["k"] > 0:
        alarms.insert(0, Alarm("leakage", "fail", leak_value, leak_band))
    elif leak_band is not None:
        level = _band_level(leak_band, leak_value)
        if level is not None:
            alarms.insert(0, Alarm("leakage", level, leak_value, leak_band))
    return alarms


# ------------------------------------------------------------------ MON-03 drift (minimal PSI)


def _clean(values: Iterable[float | None]) -> np.ndarray:
    arr = np.array([v for v in values if v is not None], dtype=float)
    return arr[np.isfinite(arr)]


def psi(
    reference: Iterable[float | None], current: Iterable[float | None], bins: int = 10
) -> float:
    """Population stability index of `current` against `reference`, on quantile bins taken from
    the reference (repeated edges merged). Nulls and non-finite values are dropped; an empty bin's
    share is floored at a small epsilon so the index stays finite."""
    if bins < 2:
        raise ValueError("psi needs at least 2 bins")
    ref, cur = _clean(reference), _clean(current)
    if ref.size == 0 or cur.size == 0:
        raise ValueError("psi needs values in both samples")
    if ref.min() == ref.max():  # a constant reference: one bin for it, one either side
        v = float(ref[0])
        edges = np.array([v, np.nextafter(v, math.inf)])
    else:
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1))[1:-1])
    k = edges.size + 1
    r = np.bincount(np.searchsorted(edges, ref, side="right"), minlength=k) / ref.size
    c = np.bincount(np.searchsorted(edges, cur, side="right"), minlength=k) / cur.size
    r, c = np.maximum(r, PSI_EPSILON), np.maximum(c, PSI_EPSILON)
    return float(np.sum((c - r) * np.log(c / r)))


def drift_level(value: float) -> DriftLevel:
    if value < PSI_WARN:
        return "ok"
    return "warn" if value <= PSI_ALARM else "alarm"


def _feature(case: AlertCase, name: str) -> float | None:
    if hasattr(case, name):
        return getattr(case, name)
    return getattr(case.features, name)


def drift(
    reference_cases: Sequence[AlertCase], new_cases: Sequence[AlertCase]
) -> dict[str, tuple[float, DriftLevel]]:
    """MON-03: PSI and its level per agent-visible numeric feature, new batch against reference.
    "ok" below 0.1, "warn" from 0.1 to 0.25, "alarm" above 0.25."""
    out: dict[str, tuple[float, DriftLevel]] = {}
    for name in DRIFT_FEATURES:
        value = psi(
            [_feature(c, name) for c in reference_cases], [_feature(c, name) for c in new_cases]
        )
        out[name] = (round(value, 4), drift_level(value))
    return out


# ------------------------------------------------------------------ MON-04 trend


def trend(history_lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """MON-04: one series per metric, values in run order (None where a run lacks the metric)."""
    run_ids = [line["run_id"] for line in history_lines]
    series = []
    for metric in TREND_METRICS:
        if metric == "leakage_hi":
            values = [float(line["leakage"]["hi"]) for line in history_lines]
        else:
            values = [metric_value(line, metric) for line in history_lines]
        series.append({"metric": metric, "run_ids": run_ids, "values": values})
    return series


# ------------------------------------------------------------------ IMP-01 experiment ledger


def _dev_summary(dev: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": dev["run_id"],
        "n": dev["n"],
        "frauds": dev["frauds"],
        "leakage": {k: dev["leakage"][k] for k in ("k", "n", "hi")},
        "auto_clear_rate": {k: dev["auto_clear_rate"][k] for k in ("k", "n", "point")},
        "decision_cost_usd": dev.get("decision_cost_usd"),
    }


def experiment_line(
    *,
    hypothesis: str,
    change: str,
    digest_before: str,
    digest_after: str,
    dev_before: Mapping[str, Any],
    dev_after: Mapping[str, Any],
    paired: Mapping[str, Any] | None,
    verdict: str,
    note: str | None = None,
) -> dict[str, Any]:
    """IMP-01: the ledger line for one dev-set change, with the IMP-02 rule's own verdict beside
    the recorded one (an owner ruling may differ; the ledger shows both)."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    if not hypothesis.strip() or not change.strip():
        raise ValueError("hypothesis and change must be stated")
    if not digest_before or not digest_after or digest_before == digest_after:
        raise ValueError("a change needs two different settings digests")
    rule_verdict, rule_reason = accept_change(dev_before, dev_after, paired=paired)
    before_ac = dev_before["auto_clear_rate"]["point"]
    after_ac = dev_after["auto_clear_rate"]["point"]
    return {
        "hypothesis": hypothesis,
        "change": change,
        "digest_before": digest_before,
        "digest_after": digest_after,
        "dev_before": _dev_summary(dev_before),
        "dev_after": _dev_summary(dev_after),
        "auto_clear_change": round(after_ac - before_ac, 4),
        "leaks_before_after": [dev_before["leakage"]["k"], dev_after["leakage"]["k"]],
        "paired": None if paired is None else dict(paired),
        "rule": {"verdict": rule_verdict, "reason": rule_reason},
        "verdict": verdict,
        "note": note,
    }


def append_jsonl(path: Path | str, line: Mapping[str, Any]) -> None:
    """Append one already-built line (an experiment line) in a single write."""
    _append(path, line)


def append_experiment(
    path: Path | str,
    *,
    hypothesis: str,
    change: str,
    digest_before: str,
    digest_after: str,
    dev_before: Mapping[str, Any],
    dev_after: Mapping[str, Any],
    paired: Mapping[str, Any] | None,
    verdict: str,
    note: str | None = None,
) -> dict[str, Any]:
    """IMP-01: append one experiment to the ledger (kept or not) and return the line."""
    line = experiment_line(
        hypothesis=hypothesis,
        change=change,
        digest_before=digest_before,
        digest_after=digest_after,
        dev_before=dev_before,
        dev_after=dev_after,
        paired=paired,
        verdict=verdict,
        note=note,
    )
    _append(path, line)
    return line


# ------------------------------------------------------------------ IMP-02 keep or revert


def accept_change(
    dev_before: Mapping[str, Any],
    dev_after: Mapping[str, Any],
    *,
    tolerance: float = AUTO_CLEAR_TOLERANCE,
    paired: Mapping[str, Any] | None = None,
) -> tuple[Verdict, str]:
    """IMP-02: keep a dev-set change only if dev leakage stays 0 and auto-clear falls by no more
    than `tolerance`, or if it fixes a leak. Both runs must be on the same dev sample.

    `paired` is the hook for EvalKit's paired bootstrap with Holm correction (v0.3.0): pass its
    result as a mapping with a boolean `survives_holm`, and a change that is not a leak fix is
    kept only when its gain survives. Without it the verdict says the paired test is pending."""
    if not 0 <= tolerance <= 1:
        raise ValueError("tolerance must be between 0 and 1")
    if (dev_before["n"], dev_before["frauds"]) != (dev_after["n"], dev_after["frauds"]):
        raise ValueError("compare dev runs on the same sample (n and frauds differ)")
    leak_before, leak_after = dev_before["leakage"]["k"], dev_after["leakage"]["k"]
    if leak_after > 0:
        return "revert", f"dev leakage is {leak_after}, not 0"
    if leak_before > 0:
        return "keep", f"fixes a leak: dev leakage {leak_before} -> 0"
    drop = dev_before["auto_clear_rate"]["point"] - dev_after["auto_clear_rate"]["point"]
    if drop > tolerance + 1e-12:
        return "revert", f"auto-clear falls {drop:.2f}, more than the {tolerance:.2f} tolerance"
    if paired is None:
        return "keep", (
            f"leakage stays 0, auto-clear change {-drop:+.2f} within {tolerance:.2f}; "
            "paired test pending (EvalKit v0.3.0)"
        )
    if drop < -1e-12:  # auto-clear ROSE: a gain is claimed only if it survives Holm
        if paired.get("survives_holm") is not True:
            return "revert", "the auto-clear rise does not survive the Holm correction"
        return "keep", "leakage stays 0 and the auto-clear gain survives the Holm correction"
    return "keep", (
        f"leakage stays 0, auto-clear change {-drop:+.2f} within {tolerance:.2f}; "
        "no gain is claimed"
    )


# ------------------------------------------------------------------ IMP-03 freeze guard

_EVAL_PREFIX = re.compile(r"^eval(\d*)-")


def _same_digest(a: str, b: str) -> bool:
    short = min(len(a), len(b))
    return short >= 8 and a[:short] == b[:short]


def next_eval_sample(eval_runs: Sequence[Mapping[str, Any]]) -> str:
    """The next fresh sample's name: eval, eval2, eval3, ..."""
    used = [1]  # a log with no eval run yet still starts at "eval"
    seen = False
    for row in eval_runs:
        m = _EVAL_PREFIX.match(str(row.get("run_id", "")))
        if m:
            seen = True
            used.append(int(m.group(1) or 1))
    return "eval" if not seen else f"eval{max(used) + 1}"


def freeze_guard(
    eval_runs_path: Path | str,
    current_digest: str,
    *,
    current_record: dict[str, Any] | None = None,
    run_record: dict[str, Any] | None = None,
) -> str | None:
    """IMP-03: None when the newest eval run used the current frozen settings; otherwise the
    reason a fresh eval sample is needed before publishing, and which sample to use (Tech Spec §5
    step 6, EvalKit CAL-05).

    A freeze record can gain fields later (A2.5 added `logic_sources` after eval2). Given the
    newest run's own freeze record and the current one, the guard compares the settings both
    records hold, so a wider record alone never demands a fresh sample; any differing setting
    still does."""
    rows = read_jsonl(eval_runs_path)
    if not rows:
        return f"no eval run is logged in {eval_runs_path}; run the frozen settings on eval"
    newest = rows[-1]
    if _same_digest(str(newest["digest"]), current_digest):
        return None
    if current_record is not None and run_record is not None:
        if not _same_digest(str(run_record.get("digest", "")), str(newest["digest"])):
            return f"the freeze record given is not {newest['run_id']}'s"
        shared = [k for k in run_record if k != "digest" and k in current_record]
        if len(shared) == len(run_record) - 1 and all(
            run_record[k] == current_record[k] for k in shared
        ):
            return None
    return (
        f"settings changed since {newest['run_id']} (digest {newest['digest']}, now "
        f"{current_digest}); draw a fresh 500 from the remaining test-period alerts and run "
        f"{next_eval_sample(rows)} before publishing"
    )
