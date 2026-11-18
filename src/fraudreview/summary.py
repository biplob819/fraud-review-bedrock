"""`fraud evaluate`: one validated `RunSummary` from a finished run (E2.3, DSH-01, D-14).

Everything here is offline: it reads a run folder (or the committed golden fixture), the alert
cases and the labels, and calls no model. The numbers come from the modules that own them:
`metrics.compute` (leakage with its exact bound, recall, auto-clear, stage shares, queue mix,
minutes and cost, latency), `explain.summarise` (validity, protected mentions, driver agreement,
mismatch examples), `valuecheck.shares` (the three outcomes of the value check) and the
faithfulness cache summary (`scripts/judge_cache_summary.py`), carried through with its partial
flag and notes. The thresholds come from config; the exit code is `metrics.exit_code`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fraudreview import explain, metrics, monitor, stages, valuecheck
from fraudreview.config import RunConfig
from fraudreview.policy import Policy
from fraudreview.schemas import (
    AlertCase,
    BaselineSummary,
    Disposition,
    Interval,
    Rate,
    ThresholdResult,
    TriageDecision,
)
from fraudreview.stats import wilson
from fraudreview.summary_model import (
    AlarmRecord,
    AuditHead,
    DriftRecord,
    EvaluatedExplanation,
    EvaluatedSummary,
    FaithfulnessDetail,
    JudgeFaithfulness,
    MismatchExample,
    ValueShares,
)
from fraudreview.truth import AlertTruth

MINUTES_PER_ALERT = 4.0  # the modelled analyst minutes per alert (ROI input m)
BASELINE_SUMMARY = Path("docs") / "measurements" / "baseline-summary.json"


class SummaryError(Exception):
    """The run, cases, labels or side files do not fit together."""


@dataclass(frozen=True)
class RunInputs:
    run_id: str
    dispositions: tuple[Disposition, ...]
    decisions: tuple[TriageDecision, ...]
    cases: tuple[AlertCase, ...]
    truths: tuple[AlertTruth, ...]
    facts: Mapping[str, Any]  # run.json, or its golden stand-in


@dataclass(frozen=True)
class Built:
    summary: EvaluatedSummary
    fields: Mapping[str, Any]  # `metrics.compute` output, for the history line
    facts: Mapping[str, Any]
    alarms: tuple[monitor.Alarm, ...]


def _lines(path: Path) -> list[str]:
    try:
        return [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError as exc:
        raise SummaryError(f"{path}: cannot read ({exc.strerror})") from exc


def read_cases(path: Path) -> tuple[AlertCase, ...]:
    return tuple(AlertCase.model_validate_json(x) for x in _lines(path))


def read_truths(path: Path) -> tuple[AlertTruth, ...]:
    return tuple(AlertTruth.model_validate_json(x) for x in _lines(path))


def load_run(run: Path, cases: Path, truth: Path) -> RunInputs:
    """A finished run folder (`fraud triage`) with its cases and labels."""
    facts_path = run / "run.json"
    try:
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SummaryError(f"{facts_path}: cannot read ({exc.strerror})") from exc
    if facts.get("stopped") or facts.get("n_done") != facts.get("n_total"):
        raise SummaryError(f"{run.name} did not finish every alert; evaluate a finished run")
    return RunInputs(
        run_id=run.name,
        dispositions=tuple(
            Disposition.model_validate_json(x) for x in _lines(run / "dispositions.jsonl")
        ),
        decisions=tuple(
            TriageDecision.model_validate_json(x) for x in _lines(run / "decisions.jsonl")
        ),
        cases=read_cases(cases),
        truths=read_truths(truth),
        facts=facts,
    )


def load_golden(golden: Path) -> RunInputs:
    """The committed golden run (tests/fixtures/golden): its recorded replies are read straight
    from the cache store, its dispositions from `expected.json`. No model, no network."""
    expected = json.loads((golden / "expected.json").read_text(encoding="utf-8"))
    decisions = []
    for f in sorted((golden / "store").rglob("*.json")):
        value = json.loads(f.read_text(encoding="utf-8"))["value"]
        decisions.append(TriageDecision.model_validate_json(json.dumps(value)))
    dispositions = tuple(
        Disposition.model_validate_json(json.dumps(d)) for d in expected["dispositions"]
    )
    order = {d.alert_id: i for i, d in enumerate(dispositions)}
    decisions.sort(key=lambda d: (order[d.alert_id], d.stage))
    freeze = expected["freeze"]
    cases = read_cases(golden / "cases.jsonl")
    facts = {"freeze": freeze, "errors": stages.error_counts(decisions)}
    return RunInputs(
        run_id=f"golden-{len(cases)}-{freeze['digest'][:8]}",
        dispositions=dispositions,
        decisions=tuple(decisions),
        cases=cases,
        truths=read_truths(golden / "truth.jsonl"),
        facts=facts,
    )


def read_baseline(path: Path) -> BaselineSummary:
    """`results/baseline/baseline.json` (its `summary` block) or the committed copy of it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SummaryError(f"{path}: cannot read ({exc.strerror})") from exc
    return BaselineSummary.model_validate_json(json.dumps(data.get("summary", data)))


# ------------------------------------------------------------------ faithfulness


def _rate(d: Mapping[str, Any] | None) -> Rate | None:
    if d is None:
        return None
    return Rate(k=d["k"], n=d["n"], point=d["k"] / d["n"], lo=d["lo"], hi=d["hi"])


def faithfulness_parts(
    path: Path | None,
) -> tuple[dict[str, Interval], str, FaithfulnessDetail | None]:
    """The `ExplanationSummary` faithfulness fields from the judge cache summary. Each judge's
    figure is its share of claims judged supported; `partial` and the notes ride along."""
    if path is None:
        return {}, "none", None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SummaryError(f"{path}: cannot read ({exc.strerror})") from exc
    scores: dict[str, Interval] = {}
    judges: dict[str, JudgeFaithfulness] = {}
    for name, j in sorted(raw.get("judges", {}).items()):
        supported = _rate(j.get("claims_supported"))
        if supported is None:
            continue
        scores[name] = Interval(point=supported.point, lo=supported.lo, hi=supported.hi)
        judges[name] = JudgeFaithfulness(
            claims_judged=j["claims_judged"],
            claim_coverage=j["claim_coverage"],
            claims_supported=supported,
            explanations_fully_judged=j["explanations_fully_judged"],
            explanations_fully_supported=_rate(j.get("explanations_fully_supported")),
        )
    if not scores:
        return {}, "none", None
    mode = "single" if len(scores) == 1 else "dual"
    if mode != raw.get("judge_mode"):
        raise SummaryError(
            f"{path}: judge_mode {raw.get('judge_mode')!r} does not match its judges"
        )
    detail = FaithfulnessDetail(
        partial=bool(raw.get("partial", False)),
        notes=tuple(raw.get("notes", ())),
        explanations=raw["explanations"],
        claims_extracted=raw["claims_extracted"],
        judges=judges,
    )
    return scores, mode, detail


# ------------------------------------------------------------------ the summary


def _split(run_id: str) -> str:
    for prefix in ("eval", "dev", "golden"):
        if run_id.startswith(prefix):
            return "eval" if prefix == "eval" else prefix
    raise SummaryError(f"cannot tell the split from run id {run_id!r} (eval, dev or golden)")


def _value_shares(
    decisions: Sequence[TriageDecision], cases: Mapping[str, AlertCase]
) -> ValueShares:
    outcomes = []
    for d in decisions:
        if d.alert_id not in cases:
            raise SummaryError(f"no case for decision {d.decision_id}")
        outcomes.extend(valuecheck.check_decision(d, cases[d.alert_id]))
    s = valuecheck.shares(outcomes)
    n = s["n"]
    return ValueShares(
        n=n,
        verified=s["verified"],
        contradicted=s["contradicted"],
        unverifiable=s["unverifiable"],
        verified_share=s["verified_share"],
        contradicted_share=s["contradicted_share"],
        unverifiable_share=wilson(s["unverifiable"], n),
    )


def _mismatch_detail(
    ids: Sequence[str],
    decisions: Sequence[TriageDecision],
    truths: Mapping[str, AlertTruth],
) -> tuple[MismatchExample, ...]:
    stage2 = {d.alert_id: d for d in decisions if d.stage == 2 and d.output is not None}
    out = []
    for alert_id in ids:
        d = stage2.get(alert_id)
        truth = truths[alert_id]
        agreement = explain.driver_agreement(d, truth) if d is not None else None
        out.append(
            MismatchExample(
                alert_id=alert_id,  # type: ignore[arg-type]
                agreement=agreement,
                cited=tuple(d.output.fields_used) if d is not None and d.output else (),
                positive_drivers=tuple(sorted(explain.positive_drivers(truth))),
                protected_mentions=sum(
                    explain.protected_mentions(x) for x in decisions if x.alert_id == alert_id
                ),
            )
        )
    return tuple(out)


def _times(
    decisions: Sequence[TriageDecision], facts: Mapping[str, Any]
) -> tuple[datetime, datetime]:
    started = min(d.at for d in decisions)
    finished = max(d.at for d in decisions)
    if facts.get("finished_at"):
        finished = max(started, datetime.fromisoformat(facts["finished_at"]).astimezone(UTC))
    return started, finished


def build(
    inputs: RunInputs,
    config: RunConfig,
    policy: Policy,
    baseline: BaselineSummary,
    *,
    faithfulness: Path | None = None,
    reference: Sequence[AlertCase] = (),
    minutes_per_alert: float = MINUTES_PER_ALERT,
) -> Built:
    """Assemble and validate the run's `RunSummary`; nothing is written."""
    labels = {t.alert_id: t.label for t in inputs.truths}
    truth_map = {t.alert_id: t for t in inputs.truths}
    case_map = {c.alert_id: c for c in inputs.cases}
    try:
        fields = metrics.compute(
            inputs.dispositions,
            inputs.decisions,
            {d.alert_id: labels[d.alert_id] for d in inputs.dispositions},
            minutes_per_alert=minutes_per_alert,
            seed=config.seed,
        )
        exp = explain.summarise(inputs.decisions, inputs.cases, inputs.truths, policy)
    except (metrics.MetricsError, explain.ExplainError, KeyError) as exc:
        raise SummaryError(f"the run does not fit its cases and labels: {exc}") from exc
    shares = _value_shares(inputs.decisions, case_map)
    scores, mode, detail = faithfulness_parts(faithfulness)
    explanation = EvaluatedExplanation(
        schema_valid=exp["schema_valid"],
        clauses_valid=exp["clauses_valid"],
        fields_valid=exp["fields_valid"],
        # reasons whose numbers could be checked: the share not contradicted would hide the
        # unverifiable ones, so this is verified out of verified plus contradicted
        values_true=wilson(shares.verified, shares.verified + shares.contradicted),
        protected_mentions=exp["protected_mentions"],
        driver_agreement=exp["driver_agreement"],
        faithfulness=scores,
        judge_mode=mode,  # type: ignore[arg-type]
        mismatch_examples=exp["mismatch_examples"],
        driver_agreement_n=exp["driver_agreement_n"],
        suspicious_no_clear=exp["suspicious_no_clear"],
        value_shares=shares,
        faithfulness_detail=detail,
        mismatch_detail=_mismatch_detail(exp["mismatch_examples"], inputs.decisions, truth_map),
    )
    thresholds: tuple[ThresholdResult, ...] = metrics.evaluate_thresholds(
        fields["threshold_values"], config.thresholds
    )
    freeze = inputs.facts["freeze"]
    line = monitor.run_history_line(inputs.run_id, fields, inputs.facts)
    alarms = tuple(monitor.check_bands(line, config.alarms))
    drift: tuple[DriftRecord, ...] | None = None
    if reference:
        drift = tuple(
            DriftRecord(feature=name, psi=value, level=level)
            for name, (value, level) in monitor.drift(reference, inputs.cases).items()
        )
    head = inputs.facts.get("audit_head")
    started, finished = _times(inputs.decisions, inputs.facts)
    summary = EvaluatedSummary(
        run_id=inputs.run_id,
        split=_split(inputs.run_id),  # type: ignore[arg-type]
        n_alerts=fields["n_alerts"],
        n_frauds=fields["n_frauds"],
        policy_version=freeze["policy_version"],
        prompt_hashes=freeze["prompt_hashes"],
        models=freeze["models"],
        provider=freeze["provider"],
        baseline=baseline,
        leakage=fields["leakage"],
        recall_end_to_end=fields["recall_end_to_end"],
        auto_clear_rate=fields["auto_clear_rate"],
        auto_clear_precision=fields["auto_clear_precision"],
        stage1_clear_rate=fields["stage1_clear_rate"],
        stage2_reach_rate=fields["stage2_reach_rate"],
        queue_mix=fields["queue_mix"],
        minutes_saved_per_1000=fields["minutes_saved_per_1000"],
        cost_per_1000_usd=fields["cost_per_1000_usd"],
        latency_ms=fields["latency_ms"],
        explanation=explanation,
        errors=inputs.facts.get("errors") or stages.error_counts(list(inputs.decisions)),  # type: ignore[arg-type]
        thresholds=thresholds,
        cost_total_usd=fields["cost_total_usd"],
        started_at=started,
        finished_at=finished,
        freeze_digest=freeze["digest"],
        audit_head=AuditHead(seq=head["seq"], hash=head["hash"]) if head else None,
        minutes_per_alert=minutes_per_alert,
        alarms=tuple(AlarmRecord(metric=a.metric, level=a.level, value=a.value) for a in alarms),
        drift=drift,
    )
    return Built(summary, fields, inputs.facts, alarms)


def to_json(summary: EvaluatedSummary) -> str:
    return summary.model_dump_json(indent=2) + "\n"


def read_summary(path: Path) -> EvaluatedSummary:
    try:
        return EvaluatedSummary.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SummaryError(f"{path}: cannot read ({exc.strerror})") from exc


def append_history_once(path: Path, built: Built) -> bool:
    """Append the run's history line unless that run (same id and digest) is already logged.
    True when a line was added."""
    line = monitor.run_history_line(built.summary.run_id, built.fields, built.facts)
    line["source"] = "fraud evaluate"
    return monitor.append_history_once(path, line)
