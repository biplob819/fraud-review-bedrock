"""E2.4 monitoring and improvement (MON-01..04, IMP-01..03): run history, alarm bands, PSI drift,
trend series, the experiment ledger, the keep-or-revert rule and the freeze guard. Synthetic and
committed data only; no network, no model."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from fraudreview import metrics
from fraudreview import monitor as mon
from fraudreview.config import AlarmBand, ConfigError, load_config
from fraudreview.data import alert_id
from fraudreview.run import FREEZE
from tests.test_metrics import synthetic_run
from tests.test_stages import case

ROOT = Path(__file__).resolve().parent.parent
MEAS = ROOT / "docs" / "measurements"
GOOD = (ROOT / "config.yaml").read_text(encoding="utf-8")


def history(**over: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "run_id": "eval-500-abcdef12",
        "digest": "abcdef1234567890",
        "n": 500,
        "frauds": 50,
        "leakage": {"k": 0, "n": 50, "point": 0.0, "hi": 0.058},
        "auto_clear_rate": {"k": 100, "n": 500, "point": 0.2, "lo": 0.17, "hi": 0.24},
        "auto_clear_precision": {"k": 100, "n": 100, "point": 1.0, "lo": 0.96, "hi": 1.0},
        "cost_per_1000_usd": 5.0,
        "schema_error_rate": 0.0,
        "refusal_rate": 0.0,
        "latency_p95_stage2_ms": 7000,
    }
    line.update(over)
    return line


def dev(run_id: str, leaks: int, cleared: int, n: int = 100, frauds: int = 21) -> dict:
    return {
        "run_id": run_id,
        "digest": run_id.rsplit("-", 1)[-1],
        "n": n,
        "frauds": frauds,
        "leakage": {"k": leaks, "n": frauds, "point": leaks / frauds, "hi": 0.2},
        "auto_clear_rate": {"k": cleared, "n": n, "point": cleared / n, "hi": 0.5},
        "decision_cost_usd": 0.5,
    }


# ------------------------------------------------------------------ MON-01


def test_run_history_line_from_compute_fields_and_run_facts():
    disps, decs, labels = synthetic_run(80, 4)
    fields = metrics.compute(disps, decs, labels, minutes_per_alert=4, seed=1)
    facts = {"freeze": {"digest": "0123456789abcdef"}, "errors": {"schema": 2, "refusal": 1}}
    line = mon.run_history_line("eval-80-01234567", fields, facts)
    assert line["digest"] == "0123456789abcdef" and line["n"] == 80
    assert line["frauds"] == fields["n_frauds"]
    assert line["leakage"]["k"] == fields["leakage"].k
    assert set(line["leakage"]) == {"k", "n", "point", "hi"}
    assert line["auto_clear_rate"]["k"] == fields["auto_clear_rate"].k
    assert line["auto_clear_precision"]["n"] == fields["auto_clear_rate"].k
    assert line["cost_per_1000_usd"] == pytest.approx(fields["cost_per_1000_usd"].point, abs=1e-4)
    assert line["schema_error_rate"] == pytest.approx(2 / 80)
    assert line["refusal_rate"] == pytest.approx(1 / 80)
    assert line["latency_p95_stage2_ms"] == fields["latency_ms"]["stage2"].p95
    json.dumps(line, allow_nan=False)  # a history line is plain JSON


def test_run_history_line_without_errors_or_stage2():
    disps, decs, labels = synthetic_run(10, 4)
    fields = metrics.compute(disps, decs, labels, minutes_per_alert=4, seed=1)
    fields["latency_ms"] = {}
    line = mon.run_history_line("r", fields, {"freeze": {"digest": "d" * 16}})
    assert line["schema_error_rate"] == 0.0 and line["latency_p95_stage2_ms"] is None


def test_append_history_appends_one_line_per_call(tmp_path):
    path = tmp_path / "deep" / "runs.jsonl"
    mon.append_history(path, history(run_id="a"))
    mon.append_history(path, history(run_id="b"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["run_id"] for x in lines] == ["a", "b"]
    assert mon.read_jsonl(path)[1]["run_id"] == "b"
    assert mon.read_jsonl(tmp_path / "missing.jsonl") == []
    with pytest.raises(ValueError):
        mon.append_history(path, history(cost_per_1000_usd=float("nan")))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.contract
def test_backfilled_run_history_matches_eval_runs():
    evals = mon.read_jsonl(MEAS / "eval-runs.jsonl")
    runs = mon.read_jsonl(MEAS / "runs.jsonl")
    assert runs == [mon.history_from_eval_row(r) for r in evals]
    for line, row in zip(runs, evals, strict=True):
        assert line["source"] == "eval-runs.jsonl" and line["digest"] == row["digest"]
        assert line["leakage"]["k"] == row["leakage"]["k"]
        assert line["cost_per_1000_usd"] == pytest.approx(
            1000 * row["billed_usd"] / row["n"], abs=1e-4
        )
    assert [r["leakage"]["k"] for r in runs] == [2, 0]  # run 1's leak stays in the history


# ------------------------------------------------------------------ MON-02


def test_committed_alarm_bands_load():
    bands = load_config(ROOT / "config.yaml").alarms
    assert {b.metric for b in bands} >= {
        "schema_error_rate",
        "refusal_rate",
        "cost_per_1000_usd",
        "latency_p95_stage2_ms",
    }


def test_alarms_block_is_optional(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(GOOD.split("alarms:")[0], encoding="utf-8")
    assert load_config(p).alarms == ()


@pytest.mark.parametrize(
    "band, field",
    [
        ("  - metric: cost_per_1000_usd\n    warn_above: 9\n    fail_above: 5\n", "alarms.0"),
        ("  - metric: refusal_rate\n    warn_above: 0.1\n    fail_below: 0.0\n", "alarms.0"),
        ("  - metric: auto_clear_rate\n    warn_below: 0.1\n    fail_below: 0.2\n", "alarms.0"),
        ("  - metric: auto_clear_rate\n", "alarms.0"),
        ("  - metric: recall\n    warn_above: 1\n", "alarms.0.metric"),
        ('  - metric: refusal_rate\n    warn_above: "0.1"\n', "alarms.0.warn_above"),
        ("  - metric: refusal_rate\n    warn_above: -1\n", "alarms.0.warn_above"),
        ("  - metric: refusal_rate\n    warn_above: 0.1\n    surprise: 1\n", "alarms.0.surprise"),
        (
            "  - metric: refusal_rate\n    warn_above: 0.1\n"
            "  - metric: refusal_rate\n    fail_above: 0.2\n",
            "each alarm metric may appear once",
        ),
    ],
)
def test_bad_alarm_band_is_a_config_error(tmp_path, band, field):
    p = tmp_path / "c.yaml"
    p.write_text(GOOD.split("alarms:")[0] + "alarms:\n" + band, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert str(p) in str(exc.value) and field in str(exc.value)


def test_band_levels_above_and_below():
    cost = AlarmBand(metric="cost_per_1000_usd", warn_above=8, fail_above=10)
    rate = AlarmBand(metric="auto_clear_rate", warn_below=0.1, fail_below=0.05)
    assert mon.check_bands(history(cost_per_1000_usd=8.0), [cost]) == []  # at the edge: inside
    (a,) = mon.check_bands(history(cost_per_1000_usd=9.0), [cost])
    assert (a.metric, a.level, a.value, a.band) == ("cost_per_1000_usd", "warn", 9.0, cost)
    assert mon.check_bands(history(cost_per_1000_usd=11.0), [cost])[0].level == "fail"
    low = history(auto_clear_rate={"k": 40, "n": 500, "point": 0.08, "lo": 0, "hi": 1})
    assert mon.check_bands(low, [rate])[0].level == "warn"
    lower = history(auto_clear_rate={"k": 10, "n": 500, "point": 0.02, "lo": 0, "hi": 1})
    assert mon.check_bands(lower, [rate])[0].level == "fail"
    assert mon.check_bands(history(latency_p95_stage2_ms=None), [cost]) == []
    gone = AlarmBand(metric="latency_p95_stage2_ms", warn_above=1)
    assert mon.check_bands(history(latency_p95_stage2_ms=None), [gone]) == []
    with pytest.raises(ValidationError):
        AlarmBand(metric="refusal_rate")


@pytest.mark.safety
@pytest.mark.parametrize(
    "bands",
    [
        [],
        [AlarmBand(metric="leakage", warn_above=0.5)],
        [AlarmBand(metric="leakage", warn_above=0.9, fail_above=1.0)],
        [AlarmBand(metric="leakage", warn_below=0.0)],
    ],
    ids=["no-bands", "warn-only", "loose-fail", "below"],
)
def test_leakage_above_zero_always_fails_whatever_the_bands(bands):
    leaked = history(leakage={"k": 1, "n": 50, "point": 0.02, "hi": 0.1})
    alarms = mon.check_bands(leaked, bands)
    assert alarms[0].metric == "leakage" and alarms[0].level == "fail"
    assert [a.metric for a in alarms].count("leakage") == 1
    assert all(a.metric != "leakage" for a in mon.check_bands(history(), bands))


def test_committed_eval_runs_raise_the_expected_alarms():
    bands = load_config(ROOT / "config.yaml").alarms
    run1, run2 = mon.read_jsonl(MEAS / "runs.jsonl")
    assert ("leakage", "fail") in [(a.metric, a.level) for a in mon.check_bands(run1, bands)]
    assert all(a.level != "fail" for a in mon.check_bands(run2, bands))


# ------------------------------------------------------------------ MON-03


def test_psi_identical_distributions_is_near_zero():
    rng = random.Random(1)
    ref = [rng.gauss(0, 1) for _ in range(2000)]
    assert mon.psi(ref, ref) == pytest.approx(0.0, abs=1e-12)
    same = [rng.gauss(0, 1) for _ in range(2000)]
    assert mon.psi(ref, same) < 0.05


def test_psi_shifted_distribution_is_above_the_alarm_line():
    rng = random.Random(2)
    ref = [rng.gauss(0, 1) for _ in range(2000)]
    shifted = [rng.gauss(1.5, 1) for _ in range(2000)]
    value = mon.psi(ref, shifted)
    assert value > 0.25 and mon.drift_level(value) == "alarm"


def test_psi_with_empty_bins_stays_finite():
    ref = [float(i) for i in range(100)]
    value = mon.psi(ref, [5.0] * 50)  # every current value in one bin; nine bins empty
    assert 0.25 < value < 100
    assert mon.psi([1.0] * 50, [1.0] * 50) == pytest.approx(0.0)
    assert mon.psi([1.0] * 50, [3.0] * 50) > 0.25  # a constant reference still sees a shift
    assert mon.psi([0.0, 1.0, None], [0.0, 1.0, float("nan")]) == pytest.approx(0.0)


def test_psi_refuses_empty_samples_and_too_few_bins():
    with pytest.raises(ValueError):
        mon.psi([], [1.0])
    with pytest.raises(ValueError):
        mon.psi([1.0], [None])
    with pytest.raises(ValueError):
        mon.psi([1.0], [1.0], bins=1)


@pytest.mark.parametrize(
    "value, level",
    [(0.0, "ok"), (0.0999, "ok"), (0.1, "warn"), (0.25, "warn"), (0.2501, "alarm")],
)
def test_drift_levels(value, level):
    assert mon.drift_level(value) == level


def _cases(n: int, seed: int, shift: float = 0.0) -> list:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        hour = rng.randrange(24)
        out.append(
            case(
                alert_id=alert_id(f"drift-{seed}-{i}"),
                ts_hour=hour,
                ts_dow=rng.randrange(7),
                amount=round(rng.lognormvariate(3 + shift, 1), 2),
                risk_score=min(1.0, rng.random() * 0.2),
                features={
                    "amt_vs_usual": None if i % 10 == 0 else rng.lognormvariate(shift, 0.5),
                    "hour": hour,
                    "category_freq": rng.random(),
                    "card_history_n": rng.randrange(1, 2000),
                },
            )
        )
    return out


def test_drift_over_agent_visible_features():
    ref = _cases(400, 1)
    same = mon.drift(ref, _cases(400, 2))
    assert set(same) == set(mon.DRIFT_FEATURES)
    assert all(level == "ok" for _, level in same.values())
    moved = mon.drift(ref, _cases(400, 3, shift=1.5))
    assert moved["amount"][1] == "alarm" and moved["amt_vs_usual"][1] == "alarm"
    assert moved["ts_hour"][1] == "ok"


# ------------------------------------------------------------------ MON-04


def test_trend_series_in_run_order():
    lines = [
        history(run_id="r1", cost_per_1000_usd=5.0),
        history(run_id="r2", latency_p95_stage2_ms=None),
    ]
    series = {s["metric"]: s for s in mon.trend(lines)}
    assert set(series) == set(mon.TREND_METRICS)
    assert series["cost_per_1000_usd"]["run_ids"] == ["r1", "r2"]
    assert series["leakage_hi"]["values"] == [0.058, 0.058]
    assert series["auto_clear_rate"]["values"] == [0.2, 0.2]
    assert series["latency_p95_stage2_ms"]["values"] == [7000.0, None]
    assert mon.trend([])[0]["values"] == []


# ------------------------------------------------------------------ IMP-01 / IMP-02


def test_accept_change_rules():
    base = dev("dev-100-aaaaaaaa", 0, 30)
    assert mon.accept_change(dev("dev-100-x", 2, 34), base)[0] == "keep"  # fixes a leak
    verdict, reason = mon.accept_change(base, dev("dev-100-b", 1, 40))
    assert verdict == "revert" and "leakage" in reason
    assert mon.accept_change(base, dev("dev-100-b", 0, 28))[0] == "keep"  # within 0.02
    verdict, reason = mon.accept_change(base, dev("dev-100-b", 0, 27))
    assert verdict == "revert" and "tolerance" in reason
    verdict, reason = mon.accept_change(base, dev("dev-100-b", 0, 35))
    assert verdict == "keep" and "pending" in reason
    assert mon.accept_change(base, dev("dev-100-b", 0, 35), paired={"survives_holm": True}) == (
        "keep",
        "leakage stays 0 and the paired gain survives the Holm correction",
    )
    assert mon.accept_change(base, dev("b", 0, 35), paired={"survives_holm": False})[0] == "revert"
    assert mon.accept_change(base, dev("b", 0, 20), tolerance=0.1)[0] == "keep"
    with pytest.raises(ValueError, match="same sample"):
        mon.accept_change(base, dev("dev-20-b", 0, 6, n=20, frauds=4))
    with pytest.raises(ValueError):
        mon.accept_change(base, base, tolerance=2)


@pytest.mark.parametrize("verdict", ["keep", "revert"])
def test_ledger_takes_keep_or_revert(tmp_path, verdict):
    path = tmp_path / "experiments.jsonl"
    line = mon.append_experiment(
        path,
        hypothesis="h",
        change="c",
        digest_before="aaaaaaaa",
        digest_after="bbbbbbbb",
        dev_before=dev("dev-100-aaaaaaaa", 0, 30),
        dev_after=dev("dev-100-bbbbbbbb", 0, 31),
        paired=None,
        verdict=verdict,
    )
    assert mon.read_jsonl(path) == [line] and line["verdict"] == verdict
    assert line["rule"]["verdict"] == "keep" and line["auto_clear_change"] == 0.01


@pytest.mark.parametrize(
    "over",
    [
        {"verdict": "maybe"},
        {"verdict": "Keep"},
        {"hypothesis": " "},
        {"digest_after": "aaaaaaaa"},
        {"digest_before": ""},
    ],
)
def test_ledger_refuses_bad_entries(tmp_path, over):
    args: dict[str, Any] = {
        "hypothesis": "h",
        "change": "c",
        "digest_before": "aaaaaaaa",
        "digest_after": "bbbbbbbb",
        "dev_before": dev("dev-100-aaaaaaaa", 0, 30),
        "dev_after": dev("dev-100-bbbbbbbb", 0, 31),
        "paired": None,
        "verdict": "keep",
    }
    args.update(over)
    path = tmp_path / "experiments.jsonl"
    with pytest.raises(ValueError):
        mon.append_experiment(path, **args)
    assert not path.exists()


@pytest.mark.contract
def test_backfilled_experiments_match_dev_runs():
    dev_runs = {r["run_id"]: r for r in mon.read_jsonl(MEAS / "dev-runs.jsonl")}
    ledger = mon.read_jsonl(MEAS / "experiments.jsonl")
    assert [e["verdict"] for e in ledger] == ["keep", "keep"]
    assert all(e["verdict"] in mon.VERDICTS for e in ledger)
    ceiling, night = ledger
    assert (ceiling["digest_before"], ceiling["digest_after"]) == ("3fe2eff7", "48bda41f")
    assert (night["digest_before"], night["digest_after"]) == ("48bda41f", "7fec65d1")
    assert "0.045" in ceiling["change"] and "P5.9" in night["change"]
    for e in ledger:
        before = dev_runs[f"dev-100-{e['digest_before']}"]
        after = dev_runs[f"dev-100-{e['digest_after']}"]
        assert e["dev_before"]["run_id"] == before["run_id"]
        assert e["dev_after"]["run_id"] == after["run_id"]
        assert e["leaks_before_after"] == [before["leakage"]["k"], after["leakage"]["k"]]
        assert e["auto_clear_change"] == pytest.approx(
            after["auto_clear_rate"]["point"] - before["auto_clear_rate"]["point"]
        )
        rebuilt = mon.experiment_line(
            hypothesis=e["hypothesis"],
            change=e["change"],
            digest_before=e["digest_before"],
            digest_after=e["digest_after"],
            dev_before=before,
            dev_after=after,
            paired=e["paired"],
            verdict=e["verdict"],
            note=e["note"],
        )
        assert rebuilt == e
    assert ceiling["leaks_before_after"] == [2, 0] and ceiling["rule"]["verdict"] == "keep"
    # the night rule's price is stated: dev auto-clear 31 -> 14 of 100; kept by OWNER D-40
    assert night["auto_clear_change"] == pytest.approx(-0.17)
    assert night["rule"]["verdict"] == "revert" and "D-40" in night["note"]


# ------------------------------------------------------------------ IMP-03


def _eval_log(tmp_path: Path, *rows: tuple[str, str]) -> Path:
    path = tmp_path / "eval-runs.jsonl"
    path.write_text(
        "".join(json.dumps({"run_id": r, "digest": d}) + "\n" for r, d in rows), encoding="utf-8"
    )
    return path


def test_freeze_guard(tmp_path):
    log = _eval_log(tmp_path, ("eval-500-11111111", "1111111122222222"))
    assert mon.freeze_guard(log, "1111111122222222") is None
    assert mon.freeze_guard(log, "11111111") is None  # short digest of the same settings
    reason = mon.freeze_guard(log, "3333333344444444")
    assert reason is not None and "eval2" in reason and "eval-500-11111111" in reason
    log = _eval_log(
        tmp_path, ("eval-500-11111111", "1111111122222222"), ("eval2-500-55555555", "5555555566")
    )
    assert "eval3" in (mon.freeze_guard(log, "1111111122222222") or "")
    assert "run the frozen settings on eval" in (
        mon.freeze_guard(tmp_path / "none.jsonl", "abc") or ""
    )
    assert mon.next_eval_sample([]) == "eval"
    assert mon.next_eval_sample([{"run_id": "eval9-500-x"}]) == "eval10"


@pytest.mark.contract
def test_freeze_guard_on_the_committed_log():
    current = json.loads((ROOT / FREEZE).read_text(encoding="utf-8"))["digest"]
    evals = MEAS / "eval-runs.jsonl"
    newest = mon.read_jsonl(evals)[-1]["digest"]
    reason = mon.freeze_guard(evals, current)
    assert (reason is None) == (current == newest)
    assert mon.freeze_guard(evals, newest) is None
    if reason is not None:
        assert "eval3" in reason


@pytest.mark.contract
def test_freeze_guard_accepts_eval2_under_the_widened_freeze():
    import json as _json

    from fraudreview import run
    from fraudreview.config import load_config
    from fraudreview.policy import load_policy

    root = Path(__file__).resolve().parent.parent
    current = run.freeze_record(load_config(root / "config.yaml"), load_policy())
    eval2 = _json.loads(
        (root / "docs" / "measurements" / "eval2-freeze.json").read_text(encoding="utf-8")
    )
    runs = root / "docs" / "measurements" / "eval-runs.jsonl"
    assert (
        mon.freeze_guard(runs, current["digest"], current_record=current, run_record=eval2)
        is None
    )
    moved = {**current, "seed": current["seed"] + 1}
    reason = mon.freeze_guard(runs, "x" * 16, current_record=moved, run_record=eval2)
    assert reason is not None and "fresh 500" in reason
