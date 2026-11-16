"""Run metrics with intervals and threshold checks (Tech Spec §5-6, D-10, D-19, E2.1).

Pure functions over a run's dispositions, decisions and labels: no model calls, seeded
randomness only. Leakage is the headline: frauds auto-cleared out of frauds in the scored
alerts, reported with its exact upper bound when none leaked.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fraudreview.router import reconcile
from fraudreview.schemas import (
    Disposition,
    Interval,
    LatencySummary,
    Rate,
    ThresholdResult,
    TriageDecision,
)
from fraudreview.stats import bootstrap_mean, percentile, rate_with_exact_zero, wilson


class MetricsError(Exception):
    """The run's records do not fit together (missing, duplicate or mismatched)."""


@dataclass(frozen=True)
class Outcome:
    """One scored alert: its label, what happened to it, and what it cost."""

    alert_id: str
    fraud: bool
    final: str  # auto_cleared | hold | escalate
    decided_at_stage: int
    cost_usd: float


def outcomes(
    dispositions: Sequence[Disposition],
    decisions: Iterable[TriageDecision],
    labels: Mapping[str, bool],
) -> list[Outcome]:
    """Join each alert's one disposition, its decisions and its label; refuse anything that does
    not fit (an alert counted twice, a missing decision, a label without a disposition)."""
    problems = reconcile(labels.keys(), dispositions)
    if problems:
        raise MetricsError("; ".join(problems))
    by_id: dict[str, TriageDecision] = {}
    for d in decisions:
        if d.decision_id in by_id:
            raise MetricsError(f"decision {d.decision_id} appears twice")
        by_id[d.decision_id] = d
    out = []
    named: set[str] = set()
    for disp in dispositions:
        cost = 0.0
        named.update(disp.decision_ids)
        for i, did in enumerate(disp.decision_ids):
            dec = by_id.get(did)
            if dec is None:
                raise MetricsError(f"{disp.alert_id}: decision {did} is missing")
            if dec.alert_id != disp.alert_id:
                raise MetricsError(f"{disp.alert_id}: decision {did} is for {dec.alert_id}")
            if dec.stage != i + 1:
                raise MetricsError(f"{disp.alert_id}: decision {did} is stage {dec.stage}")
            cost += dec.cost_usd
        if len(disp.decision_ids) != disp.decided_at_stage:
            raise MetricsError(
                f"{disp.alert_id}: decided at stage {disp.decided_at_stage} "
                f"with {len(disp.decision_ids)} decision(s)"
            )
        _check_routing(disp, [by_id[did] for did in disp.decision_ids])
        out.append(
            Outcome(disp.alert_id, labels[disp.alert_id], disp.final, disp.decided_at_stage, cost)
        )
    unnamed = sorted(set(by_id) - named)
    if unnamed:  # every decision counted (cost, latency) must belong to a scored alert
        raise MetricsError(f"decision(s) no disposition names: {', '.join(unnamed[:5])}")
    return out


def _action(dec: TriageDecision) -> str | None:
    return None if dec.output is None else dec.output.action


def _check_routing(disp: Disposition, decs: list[TriageDecision]) -> None:
    """The disposition must be one the router could reach from these replies (A2.2 rules)."""
    last = _action(decs[-1])
    if disp.decided_at_stage == 1 and not (disp.final == "auto_cleared" and last == "clear"):
        raise MetricsError(f"{disp.alert_id}: only an accepted stage-1 clear ends at stage 1")
    if disp.decided_at_stage == 2:
        if disp.final == "auto_cleared" and last != "clear":
            raise MetricsError(f"{disp.alert_id}: auto-cleared without a stage-2 clear")
        if disp.final == "hold" and last != "hold":
            raise MetricsError(f"{disp.alert_id}: held without a stage-2 hold")
        if disp.final == "escalate" and last == "hold":
            raise MetricsError(f"{disp.alert_id}: a stage-2 hold is held, not escalated")


def _count(outs: Iterable[Outcome], test: Any) -> int:
    return sum(1 for o in outs if test(o))


def leakage(outs: Sequence[Outcome]) -> Rate:
    """Frauds auto-cleared out of frauds (D-10), exact upper bound when none leaked (D-19)."""
    frauds = [o for o in outs if o.fraud]
    return rate_with_exact_zero(_count(frauds, lambda o: o.final == "auto_cleared"), len(frauds))


def recall_end_to_end(outs: Sequence[Outcome]) -> Rate:
    """Frauds that reach a person (hold or escalate) out of frauds."""
    frauds = [o for o in outs if o.fraud]
    return wilson(_count(frauds, lambda o: o.final != "auto_cleared"), len(frauds))


def baseline_comparison(outs: Sequence[Outcome]) -> dict[str, float | None]:
    """The baseline raised every scored alert, so its recall on them is 1; the system's recall
    can only be lower, by exactly the leakage (paired difference on the same alerts). With no
    frauds there is no recall to compare: both recalls are None and the difference is 0."""
    if not any(o.fraud for o in outs):
        return {"baseline_recall": None, "system_recall": None, "difference": 0.0}
    system = recall_end_to_end(outs).point
    return {"baseline_recall": 1.0, "system_recall": system, "difference": -leakage(outs).point}


def auto_clear_rate(outs: Sequence[Outcome]) -> Rate:
    return wilson(_count(outs, lambda o: o.final == "auto_cleared"), len(outs))


def auto_clear_precision(outs: Sequence[Outcome]) -> Rate:
    """Legitimate alerts among the auto-clears."""
    cleared = [o for o in outs if o.final == "auto_cleared"]
    return wilson(_count(cleared, lambda o: not o.fraud), len(cleared))


def stage1_clear_rate(outs: Sequence[Outcome]) -> Rate:
    return wilson(
        _count(outs, lambda o: o.final == "auto_cleared" and o.decided_at_stage == 1), len(outs)
    )


def stage2_reach_rate(outs: Sequence[Outcome]) -> Rate:
    return wilson(_count(outs, lambda o: o.decided_at_stage == 2), len(outs))


def queue_mix(outs: Sequence[Outcome]) -> dict[str, int]:
    return {
        "hold": _count(outs, lambda o: o.final == "hold"),
        "escalate": _count(outs, lambda o: o.final == "escalate"),
    }


def _per_1000(values: list[float], seed: int) -> Interval:
    point, lo, hi = bootstrap_mean(values, seed)
    return Interval(point=1000 * point, lo=1000 * lo, hi=1000 * hi)


def minutes_saved_per_1000(
    outs: Sequence[Outcome], minutes_per_alert: float, seed: int
) -> Interval:
    """Modelled analyst minutes saved per 1,000 alerts: m for each auto-clear (ROI input m)."""
    if (
        type(minutes_per_alert) not in (int, float)
        or not math.isfinite(minutes_per_alert)
        or not 0 <= minutes_per_alert <= 1e6  # a day's worth of minutes is far beyond any m
    ):
        raise MetricsError("minutes_per_alert must be a non-negative number")
    if not outs:
        raise MetricsError("no alerts to score")
    return _per_1000([minutes_per_alert if o.final == "auto_cleared" else 0.0 for o in outs], seed)


def cost_per_1000_usd(outs: Sequence[Outcome], seed: int) -> Interval:
    """Model cost per 1,000 alerts: the sum of each alert's decision costs."""
    if not outs:
        raise MetricsError("no alerts to score")
    return _per_1000([o.cost_usd for o in outs], seed)


def latency(decisions: Iterable[TriageDecision]) -> dict[str, LatencySummary]:
    """p50 and p95 (nearest rank) per stage over live calls; cached replays do not count."""
    by_stage: dict[str, list[int]] = {}
    for d in decisions:
        if not d.cache_hit:
            by_stage.setdefault(f"stage{d.stage}", []).append(d.latency_ms)
    return {
        stage: LatencySummary(p50=int(percentile(ms, 50)), p95=int(percentile(ms, 95)))
        for stage, ms in sorted(by_stage.items())
    }


_OPS = {
    "==": lambda a, b: a == b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
}


def evaluate_thresholds(
    values: Mapping[str, float | None], thresholds: Iterable[ThresholdResult]
) -> tuple[ThresholdResult, ...]:
    """Compare each configured threshold with its metric; a metric with no value yet is left
    unevaluated (passed=None)."""
    results = []
    for t in thresholds:
        value = values.get(t.metric)
        if value is not None and not math.isfinite(value):
            raise MetricsError(f"{t.metric} is not a finite number")
        if t.metric == "leakage" and value is None:  # the safety bar is never skipped (D-10)
            raise MetricsError("a leakage threshold needs the leakage value")
        passed = None if value is None else bool(_OPS[t.op](value, t.value))
        results.append(t.model_copy(update={"passed": passed}))
    return tuple(results)


def exit_code(results: Iterable[ThresholdResult]) -> int:
    """1 when any evaluated threshold failed (D-10: leakage > 0 fails), otherwise 0."""
    return 1 if any(r.passed is False for r in results) else 0


def compute(
    dispositions: Sequence[Disposition],
    decisions: Sequence[TriageDecision],
    labels: Mapping[str, bool],
    *,
    minutes_per_alert: float,
    seed: int,
) -> dict[str, Any]:
    """The `RunSummary` fields E2.1 owns, plus the values thresholds are checked against."""
    decisions = list(decisions)  # read twice below (outcomes, latency)
    outs = outcomes(dispositions, decisions, labels)
    lat = latency(decisions)
    fields: dict[str, Any] = {
        "n_alerts": len(outs),
        "n_frauds": _count(outs, lambda o: o.fraud),
        "leakage": leakage(outs),
        "recall_end_to_end": recall_end_to_end(outs),
        "auto_clear_rate": auto_clear_rate(outs),
        "auto_clear_precision": auto_clear_precision(outs),
        "stage1_clear_rate": stage1_clear_rate(outs),
        "stage2_reach_rate": stage2_reach_rate(outs),
        "queue_mix": queue_mix(outs),
        "minutes_saved_per_1000": minutes_saved_per_1000(outs, minutes_per_alert, seed),
        "cost_per_1000_usd": cost_per_1000_usd(outs, seed),
        "latency_ms": lat,
        "cost_total_usd": sum(o.cost_usd for o in outs),
    }
    fields["threshold_values"] = {
        "leakage": fields["leakage"].point,
        # with no frauds there is no recall to judge; a recall threshold stays unevaluated
        "recall_end_to_end": fields["recall_end_to_end"].point if fields["n_frauds"] else None,
        "auto_clear_rate": fields["auto_clear_rate"].point,
        "auto_clear_precision": fields["auto_clear_precision"].point,
        "cost_per_1000_usd": fields["cost_per_1000_usd"].point,
        "latency_p95_stage2_ms": float(lat["stage2"].p95) if "stage2" in lat else None,
    }
    fields["baseline_comparison"] = baseline_comparison(outs)
    return fields
