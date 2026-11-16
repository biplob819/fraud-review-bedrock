"""E2.1: leakage with its exact bound, end-to-end recall, auto-clear rates, stage shares, queue
mix, bootstrap minutes and cost, latency, and thresholds, on synthetic runs."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Literal

import pytest
from scipy.stats import beta

from fraudreview import metrics as m
from fraudreview.data import alert_id
from fraudreview.schemas import Disposition, ThresholdResult
from fraudreview.stats import bootstrap_mean, clopper_pearson_upper, percentile, wilson
from tests.test_router import decision


def aid(i: int) -> str:
    return alert_id(f"synthetic-{i}")


def synthetic_run(n: int, seed: int, leak: bool = True):
    """n alerts with random labels and random dispositions the router could reach, with
    replies that support them (a refused stage-1 clear may still go to stage 2)."""
    rng = random.Random(seed)
    dispositions, decisions, labels = [], [], {}
    for i in range(n):
        a = aid(i + 1)
        fraud = rng.random() < 0.2
        labels[a] = fraud
        cleared_at_1 = rng.random() < 0.3 and (leak or not fraud)
        a1 = "clear" if cleared_at_1 else rng.choice(["clear", "pass"])
        d1 = decision(stage=1, action=a1, alert_id=a, decision_id=f"d1-{i}")
        d1 = d1.model_copy(
            update={
                "cost_usd": round(rng.uniform(0, 0.01), 6),
                "latency_ms": rng.randint(200, 3000),
                "cache_hit": rng.random() < 0.1,
            }
        )
        decisions.append(d1)
        if cleared_at_1:
            dispositions.append(
                Disposition(
                    alert_id=a,
                    final="auto_cleared",
                    decided_at_stage=1,
                    decision_ids=(d1.decision_id,),
                    queue=None,
                )
            )
            continue
        final: Literal["auto_cleared", "hold", "escalate"] = rng.choice(
            ["auto_cleared", "hold", "escalate"]
        )
        if final == "auto_cleared" and fraud and not leak:
            final = "hold"
        a2 = {"auto_cleared": "clear", "hold": "hold"}.get(final) or rng.choice(
            ["escalate", "clear"]  # an escalation can follow a refused clear
        )
        d2 = decision(stage=2, action=a2, alert_id=a, decision_id=f"d2-{i}")
        d2 = d2.model_copy(
            update={
                "cost_usd": round(rng.uniform(0, 0.05), 6),
                "latency_ms": rng.randint(800, 9000),
                "cache_hit": rng.random() < 0.1,
            }
        )
        decisions.append(d2)
        queue = {"auto_cleared": None, "hold": "priority", "escalate": "standard"}[final]
        dispositions.append(
            Disposition(
                alert_id=a,
                final=final,
                decided_at_stage=2,
                decision_ids=(d1.decision_id, d2.decision_id),
                queue=queue,
            )
        )
    return dispositions, decisions, labels


# ------------------------------------------------------------------ MET-01


def test_leakage_and_recall_match_a_direct_count():
    for seed in range(20):
        disps, decs, labels = synthetic_run(120, seed)
        outs = m.outcomes(disps, decs, labels)
        frauds = [d for d in disps if labels[d.alert_id]]
        leaked = sum(d.final == "auto_cleared" for d in frauds)
        lk, rc = m.leakage(outs), m.recall_end_to_end(outs)
        assert (lk.k, lk.n) == (leaked, len(frauds))
        assert (rc.k, rc.n) == (len(frauds) - leaked, len(frauds))
        if leaked:
            assert lk == wilson(leaked, len(frauds))
        cmp = m.baseline_comparison(outs)
        assert cmp["baseline_recall"] == 1.0
        diff = cmp["difference"]
        assert diff is not None and diff == pytest.approx(-lk.point) and diff <= 0


def test_zero_leakage_reports_the_exact_upper_bound():
    disps, decs, labels = synthetic_run(300, 3, leak=False)
    lk = m.leakage(m.outcomes(disps, decs, labels))
    assert lk.k == 0 and lk.lo == 0.0
    assert lk.hi == pytest.approx(1 - 0.05 ** (1 / lk.n))
    assert clopper_pearson_upper(0, 79) == pytest.approx(0.0372, abs=5e-5)


def test_clopper_pearson_matches_the_beta_quantile():
    for n in (1, 7, 50, 500):
        for k in range(0, n + 1, max(1, n // 7)):
            want = 1.0 if k == n else float(beta.ppf(0.95, k + 1, n - k))
            assert clopper_pearson_upper(k, n) == pytest.approx(want, rel=1e-9)
    with pytest.raises(ValueError):
        clopper_pearson_upper(3, 2)


# ------------------------------------------------------------------ MET-02


def test_auto_clear_stage_and_queue_counts():
    for seed in range(20):
        disps, decs, labels = synthetic_run(150, seed)
        outs = m.outcomes(disps, decs, labels)
        cleared = [d for d in disps if d.final == "auto_cleared"]
        acr, acp = m.auto_clear_rate(outs), m.auto_clear_precision(outs)
        assert (acr.k, acr.n) == (len(cleared), 150)
        assert (acp.k, acp.n) == (sum(not labels[d.alert_id] for d in cleared), len(cleared))
        s1, s2 = m.stage1_clear_rate(outs), m.stage2_reach_rate(outs)
        assert s1.k + s2.k == 150 and s1.n == s2.n == 150
        assert m.queue_mix(outs) == {
            "hold": sum(d.final == "hold" for d in disps),
            "escalate": sum(d.final == "escalate" for d in disps),
        }
    empty = m.auto_clear_precision([m.Outcome("a", False, "hold", 2, 0.0)])
    assert (empty.n, empty.point, empty.lo, empty.hi) == (0, 0.0, 0.0, 1.0)


def test_outcomes_refuse_records_that_do_not_fit():
    disps, decs, labels = synthetic_run(10, 1)
    with pytest.raises(m.MetricsError, match="no disposition"):
        m.outcomes(disps[1:], decs, labels)
    with pytest.raises(m.MetricsError, match="2 dispositions"):
        m.outcomes([*disps, disps[0]], decs, labels)
    with pytest.raises(m.MetricsError, match="unknown alert"):
        m.outcomes(disps, decs, {k: v for k, v in list(labels.items())[1:]})
    gone = set(disps[0].decision_ids)
    with pytest.raises(m.MetricsError, match="missing"):
        m.outcomes(disps, [d for d in decs if d.decision_id not in gone], labels)
    swapped = [
        d.model_copy(update={"alert_id": aid(99)}) if d.decision_id in gone else d for d in decs
    ]
    with pytest.raises(m.MetricsError, match="is for"):
        m.outcomes(disps, swapped, labels)
    two = next(d for d in disps if d.decided_at_stage == 2)
    flipped = two.model_copy(update={"decision_ids": tuple(reversed(two.decision_ids))})
    with pytest.raises(m.MetricsError, match="stage"):
        m.outcomes([flipped if d is two else d for d in disps], decs, labels)
    with pytest.raises(m.MetricsError, match="twice"):
        m.outcomes(disps, [*decs, decs[0]], labels)


# ------------------------------------------------------------------ MET-03


def test_minutes_and_cost_points_and_bootstrap_intervals():
    disps, decs, labels = synthetic_run(200, 9)
    outs = m.outcomes(disps, decs, labels)
    acr = m.auto_clear_rate(outs).point
    mins = m.minutes_saved_per_1000(outs, 6.0, seed=1)
    assert mins.point == pytest.approx(1000 * 6.0 * acr)
    assert 0 <= mins.lo <= mins.point <= mins.hi
    cost = m.cost_per_1000_usd(outs, seed=1)
    by_alert = {}
    for d in decs:
        by_alert[d.alert_id] = by_alert.get(d.alert_id, 0.0) + d.cost_usd
    assert cost.point == pytest.approx(1000 * sum(by_alert.values()) / 200)
    assert 0 <= cost.lo <= cost.point <= cost.hi
    assert m.cost_per_1000_usd(outs, seed=1) == cost
    assert m.cost_per_1000_usd(outs, seed=2) != cost
    with pytest.raises(m.MetricsError):
        m.minutes_saved_per_1000(outs, -1, seed=1)
    with pytest.raises(m.MetricsError):
        m.minutes_saved_per_1000(outs, float("nan"), seed=1)


def test_bootstrap_uses_ten_thousand_seeded_resamples():
    import numpy as np

    values = [float(x) for x in range(50)]
    point, lo, hi = bootstrap_mean(values, seed=4)
    rng = np.random.default_rng(4)
    data = np.asarray(values)
    means = np.concatenate(
        [data[rng.integers(0, 50, size=(1000, 50))].mean(axis=1) for _ in range(10)]
    )
    assert len(means) == 10_000
    want_lo, want_hi = np.percentile(means, [2.5, 97.5])
    assert (point, lo, hi) == pytest.approx((24.5, want_lo, want_hi))


# ------------------------------------------------------------------ MET-04


def test_latency_is_nearest_rank_over_live_calls_only():
    _, decs, _ = synthetic_run(80, 5)
    lat = m.latency(decs)
    for stage in (1, 2):
        live = sorted(d.latency_ms for d in decs if d.stage == stage and not d.cache_hit)
        want50 = live[math.ceil(0.5 * len(live)) - 1]
        want95 = live[math.ceil(0.95 * len(live)) - 1]
        assert (lat[f"stage{stage}"].p50, lat[f"stage{stage}"].p95) == (want50, want95)
    cached = [d.model_copy(update={"cache_hit": True}) for d in decs]
    assert m.latency(cached) == {}
    assert percentile([5], 95) == 5.0


# ------------------------------------------------------------------ MET-05


def default_thresholds():
    return (ThresholdResult(metric="leakage", op="==", value=0),)


def test_a_planted_leak_fails_the_leakage_threshold_and_exits_1():
    disps, decs, labels = synthetic_run(60, 2, leak=False)
    clean = m.compute(disps, decs, labels, minutes_per_alert=6, seed=1)
    ok = m.evaluate_thresholds(clean["threshold_values"], default_thresholds())
    assert ok[0].passed is True and m.exit_code(ok) == 0
    fraud = next(d for d in disps if labels[d.alert_id] and d.decided_at_stage == 2)
    leaked = fraud.model_copy(update={"final": "auto_cleared", "queue": None})
    planted = [leaked if d is fraud else d for d in disps]
    s2 = fraud.decision_ids[1]  # the stage-2 reply now (wrongly) clears this fraud
    clear2 = decision(stage=2, action="clear", alert_id=fraud.alert_id, decision_id=s2)
    decs = [clear2 if d.decision_id == s2 else d for d in decs]
    bad = m.compute(planted, decs, labels, minutes_per_alert=6, seed=1)
    assert bad["leakage"].k == 1
    res = m.evaluate_thresholds(bad["threshold_values"], default_thresholds())
    assert res[0].passed is False and m.exit_code(res) == 1


def test_thresholds_use_their_operator_and_skip_missing_metrics():
    values = {"auto_clear_rate": 0.3, "cost_per_1000_usd": 4.0, "latency_p95_stage2_ms": None}
    ts = (
        ThresholdResult(metric="auto_clear_rate", op=">=", value=0.25),
        ThresholdResult(metric="cost_per_1000_usd", op="<=", value=3.0),
        ThresholdResult(metric="latency_p95_stage2_ms", op="<=", value=9000),
        ThresholdResult(metric="schema_valid", op=">=", value=0.99),
    )
    res = m.evaluate_thresholds(values, ts)
    assert [r.passed for r in res] == [True, False, None, None]
    assert m.exit_code(res) == 1
    assert m.exit_code(res[:1] + res[2:]) == 0


def test_compute_fills_the_run_summary_fields():
    from fraudreview.schemas import RunSummary  # noqa: F401  (fields checked by name)

    disps, decs, labels = synthetic_run(90, 6)
    f = m.compute(disps, decs, labels, minutes_per_alert=6, seed=3)
    assert f["n_alerts"] == 90 and f["n_frauds"] == sum(labels.values())
    assert f["leakage"].k + f["recall_end_to_end"].k == f["n_frauds"]
    assert f["auto_clear_precision"].n == f["auto_clear_rate"].k
    assert f["cost_total_usd"] == pytest.approx(sum(d.cost_usd for d in decs))
    assert set(f["latency_ms"]) <= {"stage1", "stage2"}


# ------------------------------------------------------------------ verifier pass 1 regressions


def test_unnamed_decisions_are_refused_so_latency_and_cost_match():
    disps, decs, labels = synthetic_run(20, 8)
    stray = decision(stage=2, action="hold", alert_id=aid(999), decision_id="stray")
    stray = stray.model_copy(update={"latency_ms": 99_999})
    with pytest.raises(m.MetricsError, match="no disposition names"):
        m.compute(disps, [*decs, stray], labels, minutes_per_alert=6, seed=1)
    one = next(d for d in disps if d.decided_at_stage == 1)
    extra = decision(stage=2, action="hold", alert_id=one.alert_id, decision_id="extra")
    with pytest.raises(m.MetricsError, match="no disposition names"):
        m.outcomes(disps, [*decs, extra], labels)


def test_dispositions_the_router_could_not_reach_are_refused():
    a = aid(500)
    s1_pass = decision(stage=1, action="pass", alert_id=a, decision_id="p1")
    s2_clear = decision(stage=2, action="clear", alert_id=a, decision_id="c2")
    s1_clear = decision(stage=1, action="clear", alert_id=a, decision_id="c1")
    cases = [
        (
            Disposition(
                alert_id=a,
                final="auto_cleared",
                decided_at_stage=1,
                decision_ids=("p1",),
                queue=None,
            ),
            [s1_pass],
            "stage-1 clear",
        ),
        (
            Disposition(
                alert_id=a,
                final="escalate",
                decided_at_stage=1,
                decision_ids=("c1",),
                queue="standard",
            ),
            [s1_clear],
            "stage-1 clear",
        ),
        (
            Disposition(
                alert_id=a,
                final="hold",
                decided_at_stage=2,
                decision_ids=("p1", "c2"),
                queue="priority",
            ),
            [s1_pass, s2_clear],
            "hold",
        ),
        (
            Disposition(
                alert_id=a,
                final="auto_cleared",
                decided_at_stage=2,
                decision_ids=("p1", "c1x"),
                queue=None,
            ),
            [s1_pass, decision(stage=2, action="hold", alert_id=a, decision_id="c1x")],
            "clear",
        ),
    ]
    for disp, decs, message in cases:
        with pytest.raises(m.MetricsError, match=message):
            m.outcomes([disp], decs, {a: False})
    ok = Disposition(
        alert_id=a,
        final="escalate",
        decided_at_stage=2,
        decision_ids=("c1", "c2"),
        queue="standard",
    )  # a refused clear escalates
    assert m.outcomes([ok], [s1_clear, s2_clear], {a: True})[0].final == "escalate"


def test_a_missing_leakage_value_is_refused():
    with pytest.raises(m.MetricsError, match="leakage"):
        m.evaluate_thresholds({}, default_thresholds())


def test_compute_output_builds_a_valid_run_summary():
    from datetime import UTC, datetime

    from fraudreview.schemas import BaselineSummary, ExplanationSummary, RunSummary
    from fraudreview.stats import wilson as w

    fixture = Path(__file__).parent / "fixtures" / "v1" / "summary.json"
    explanation = ExplanationSummary.model_validate_json(
        json.dumps(json.loads(fixture.read_text(encoding="utf-8"))["explanation"])
    )

    for leak in (True, False):
        disps, decs, labels = synthetic_run(70, 12, leak=leak)
        f = m.compute(disps, decs, labels, minutes_per_alert=6, seed=2)
        results = m.evaluate_thresholds(f["threshold_values"], default_thresholds())
        RunSummary(
            run_id="run-1",
            split="dev",
            n_alerts=f["n_alerts"],
            n_frauds=f["n_frauds"],
            policy_version="1",
            prompt_hashes={"stage1": "h1", "stage2": "h2"},
            models={"stage1": "m1", "stage2": "m2"},
            provider="bedrock",
            baseline=BaselineSummary(
                threshold=0.1,
                recall_val=0.98,
                precision_val=0.2,
                recall_test=w(9, 10),
                precision_test=w(1, 10),
                alert_rate_test=0.03,
            ),
            leakage=f["leakage"],
            recall_end_to_end=f["recall_end_to_end"],
            auto_clear_rate=f["auto_clear_rate"],
            auto_clear_precision=f["auto_clear_precision"],
            stage1_clear_rate=f["stage1_clear_rate"],
            stage2_reach_rate=f["stage2_reach_rate"],
            queue_mix=f["queue_mix"],
            minutes_saved_per_1000=f["minutes_saved_per_1000"],
            cost_per_1000_usd=f["cost_per_1000_usd"],
            latency_ms=f["latency_ms"],
            explanation=explanation,
            errors={},
            thresholds=results,
            cost_total_usd=f["cost_total_usd"],
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        )


# ------------------------------------------------------------------ verifier pass 2 regressions


def test_a_run_with_no_frauds_reports_no_recall_drop():
    disps, decs, labels = synthetic_run(30, 4)
    clean = {k: False for k in labels}
    f = m.compute(disps, decs, clean, minutes_per_alert=6, seed=1)
    assert f["baseline_comparison"] == {
        "baseline_recall": None,
        "system_recall": None,
        "difference": 0.0,
    }
    assert f["leakage"].n == 0 and f["threshold_values"]["recall_end_to_end"] is None
    res = m.evaluate_thresholds(
        f["threshold_values"],
        (*default_thresholds(), ThresholdResult(metric="recall_end_to_end", op=">=", value=0.9)),
    )
    assert [r.passed for r in res] == [True, None] and m.exit_code(res) == 0


def test_escalating_a_stage_2_hold_and_infinite_minutes_are_refused():
    a = aid(700)
    s1 = decision(stage=1, action="pass", alert_id=a, decision_id="e1")
    s2 = decision(stage=2, action="hold", alert_id=a, decision_id="e2")
    esc = Disposition(
        alert_id=a,
        final="escalate",
        decided_at_stage=2,
        decision_ids=("e1", "e2"),
        queue="standard",
    )
    with pytest.raises(m.MetricsError, match="held, not escalated"):
        m.outcomes([esc], [s1, s2], {a: True})
    disps, decs, labels = synthetic_run(10, 1)
    with pytest.raises(m.MetricsError):
        m.minutes_saved_per_1000(m.outcomes(disps, decs, labels), float("inf"), seed=1)


def test_minutes_must_be_a_real_number_and_metric_values_finite():
    disps, decs, labels = synthetic_run(10, 1)
    outs = m.outcomes(disps, decs, labels)
    for bad in (True, 1e308, "6"):
        with pytest.raises(m.MetricsError):
            m.minutes_saved_per_1000(outs, bad, seed=1)  # type: ignore[arg-type]
    with pytest.raises(m.MetricsError, match="finite"):
        m.evaluate_thresholds({"leakage": float("nan")}, default_thresholds())
    gen = (d for d in decs)
    f = m.compute(disps, gen, labels, minutes_per_alert=6, seed=1)  # type: ignore[arg-type]
    assert f["latency_ms"]  # a one-pass iterable still yields latency
