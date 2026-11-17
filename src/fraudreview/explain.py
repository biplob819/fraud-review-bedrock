"""Explanation checks (Tech Spec §5 step 5, E2.2: EXP-01, EXP-03, EXP-04, EXP-06).

Pure functions over a run's decisions, its cases, ground truth and the policy: no model calls,
seeded randomness only. The checks EvalKit v0.3.0 provides (rule compliance, structured-evidence
faithfulness, the value check: EXP-02, EXP-05, EXP-07) are not built here; `summarise` leaves
their fields for that step (`faithfulness` {}, `judge_mode` "none", `values_true` unset).

- Validity (EXP-01, B1, B2, B8): schema-valid decisions out of all decisions; clause- and
  field-valid decisions out of the decisions with an output. A clear that cites a no-clear
  clause whose predicate is false is counted apart: suspicious, not a failure.
- Protected-attribute scan (EXP-03, B7): word-bounded, case-insensitive terms in each reason.
- Driver agreement (EXP-04, D-11): per stage-2 output, the share of its comparable
  `fields_used` that are positive SHAP drivers of the alert; the mean over decisions with a
  seeded bootstrap interval. `risk_score` is the model's own output, not one of its inputs, so
  it has no driver and is left out of the share.
- Mismatch picks (EXP-06): lowest agreement first, then a protected mention, then alert id.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, get_args

from fraudreview.policy import Policy
from fraudreview.schemas import (
    AlertCase,
    ExplanationSummary,
    FeatureName,
    Interval,
    Rate,
    Stage2Output,
    TriageDecision,
)
from fraudreview.stats import bootstrap_mean, wilson
from fraudreview.truth import AlertTruth

ALLOWED_FIELDS: frozenset[str] = frozenset(get_args(FeatureName))
# a cited field -> the driver feature name in truth.py (baseline.MODEL_FEATURES); None: no driver
FIELD_TO_DRIVER: Mapping[str, str | None] = {
    "amount": "amount",
    "category": "category",
    "hour": "hour",
    "amt_vs_usual": "amt_vs_usual",
    "category_freq": "category_freq",
    "merchant_first_seen": "merchant_first_seen",
    "card_history_n": "card_history_n",
    "risk_score": None,
}
N_PICKS = 3
SEED = 20260517


class ExplainError(Exception):
    """The decisions, cases and truth do not fit together."""


# ------------------------------------------------------------------ EXP-01 validity


@dataclass(frozen=True)
class Validity:
    schema_valid: Rate  # decisions with an output, out of all decisions (B1)
    clauses_valid: Rate  # outputs whose every cited clause exists in the policy (B8)
    fields_valid: Rate  # outputs whose every field is on the allow-list (B2)
    suspicious_no_clear: int  # clears citing a no-clear clause whose predicate is false


def clauses_ok(decision: TriageDecision, policy: Policy) -> bool:
    out = decision.output
    return out is not None and not policy.unknown_clauses(out.policy_clauses)


def fields_ok(decision: TriageDecision, allowed: Iterable[str] = ALLOWED_FIELDS) -> bool:
    out = decision.output
    allow = frozenset(allowed)
    return out is not None and all(f in allow for f in out.fields_used)


def suspicious_no_clear(decision: TriageDecision, case: AlertCase, policy: Policy) -> list[str]:
    """No-clear clauses a clear cites although their predicate is false on this alert: not
    relevant to the clear, and odd to cite. A no-clear clause that holds is the router's job."""
    out = decision.output
    if out is None or out.action != "clear":
        return []
    found = []
    for cid in out.policy_clauses:
        clause = policy.clauses.get(cid)
        if clause is not None and clause.kind == "no_clear" and not clause.holds(case):
            found.append(cid)
    return found


def validity(
    decisions: Sequence[TriageDecision],
    cases: Mapping[str, AlertCase],
    policy: Policy,
    allowed: Iterable[str] = ALLOWED_FIELDS,
) -> Validity:
    allow = frozenset(allowed)
    outs = [d for d in decisions if d.output is not None]
    suspicious = 0
    for d in outs:
        if d.alert_id not in cases:
            raise ExplainError(f"no case for alert {d.alert_id}")
        suspicious += bool(suspicious_no_clear(d, cases[d.alert_id], policy))
    return Validity(
        schema_valid=wilson(len(outs), len(decisions)),
        clauses_valid=wilson(sum(clauses_ok(d, policy) for d in outs), len(outs)),
        fields_valid=wilson(sum(fields_ok(d, allow) for d in outs), len(outs)),
        suspicious_no_clear=suspicious,
    )


# ------------------------------------------------------------------ EXP-03 protected scan

US_STATES = (
    "alabama",
    "arizona",
    "california",
    "colorado",
    "florida",
    "georgia",
    "illinois",
    "michigan",
    "nevada",
    "new jersey",
    "new york",
    "ohio",
    "oregon",
    "pennsylvania",
    "texas",
    "virginia",
    "washington",
)
_TERMS = {
    "gender": r"genders?|male|female",
    "age": r"ages?|aged|elderly",
    "birth": r"birth|births|birthday|birthdate|born|dob|date of birth",
    "location": (
        r"city|cities|state|states|zip|zipcode|zip code|postcode|address|addresses|street|"
        r"local area|neighbou?rhood|"
        # "near usual" is about the amount, not a place
        r"near(?![\s-]+(?:the\s+)?(?:usual|norm|typical|threshold|limit|[0-9]))"
    ),
    "us_state": "|".join(s.replace(" ", r"\s+") for s in US_STATES),
}
PROTECTED_RE = re.compile(
    r"\b(?:" + "|".join(f"(?P<{k}>{v})" for k, v in _TERMS.items()) + r")\b",
    re.IGNORECASE,
)


def protected_terms(text: str) -> list[str]:
    """Protected-attribute words in one text, in order (lower case). Word-bounded on both sides, so
    "card's usual", "average", "usage" and "nearly" do not match."""
    return [m.group(0).lower() for m in PROTECTED_RE.finditer(text)]


def protected_mentions(decision: TriageDecision) -> int:
    out = decision.output
    return 0 if out is None else sum(len(protected_terms(r)) for r in out.reasons)


# ------------------------------------------------------------------ EXP-04 driver agreement


def positive_drivers(truth: AlertTruth) -> frozenset[str]:
    return frozenset(d.feature for d in truth.shap_top5 if d.contribution > 0)


def driver_agreement(decision: TriageDecision, truth: AlertTruth) -> float | None:
    """Share of the cited fields with a driver counterpart that are positive drivers; None when
    the decision has no output or cites no such field."""
    out = decision.output
    if out is None:
        return None
    if truth.alert_id != decision.alert_id:
        raise ExplainError(f"truth is for {truth.alert_id}, decision for {decision.alert_id}")
    mapped = [FIELD_TO_DRIVER.get(f) for f in out.fields_used]
    comparable = [m for m in mapped if m is not None]
    if not comparable:
        return None
    positive = positive_drivers(truth)
    return sum(m in positive for m in comparable) / len(comparable)


def agreements(
    decisions: Iterable[TriageDecision], truths: Mapping[str, AlertTruth]
) -> dict[str, float]:
    """Driver agreement per alert, over stage-2 decisions with an output."""
    found: dict[str, float] = {}
    for d in decisions:
        if d.stage != 2 or not isinstance(d.output, Stage2Output):
            continue
        truth = truths.get(d.alert_id)
        if truth is None:
            raise ExplainError(f"no truth for alert {d.alert_id}")
        if d.alert_id in found:
            raise ExplainError(f"two stage-2 decisions for alert {d.alert_id}")
        value = driver_agreement(d, truth)
        if value is not None:
            found[d.alert_id] = value
    return found


def mean_agreement(values: Mapping[str, float], seed: int = SEED) -> Interval:
    """Mean agreement with a seeded bootstrap 95% interval; 0 within [0, 1] when empty."""
    if not values:
        return Interval(point=0.0, lo=0.0, hi=1.0)
    ordered = [values[k] for k in sorted(values)]  # input order does not change the interval
    point, lo, hi = bootstrap_mean(ordered, seed)
    return Interval(point=point, lo=max(0.0, lo), hi=min(1.0, hi))


# ------------------------------------------------------------------ EXP-06 mismatch picks


def mismatch_picks(
    agreement: Mapping[str, float], mentions: Mapping[str, int], k: int = N_PICKS
) -> tuple[str, ...]:
    """The k alerts with the lowest driver agreement; ties go to an alert with a protected
    mention, then to the smaller alert id. Alerts with a mention but no agreement score come
    after those with one."""
    if k < 0:
        raise ExplainError("k must be >= 0")
    ids = set(agreement) | {a for a, n in mentions.items() if n > 0}

    def key(alert_id: str) -> tuple[float, int, str]:
        return (agreement.get(alert_id, 2.0), 0 if mentions.get(alert_id, 0) else 1, alert_id)

    return tuple(sorted(ids, key=key)[:k])


# ------------------------------------------------------------------ summary


def summarise(
    decisions: Sequence[TriageDecision],
    cases: Iterable[AlertCase],
    truths: Iterable[AlertTruth],
    policy: Policy,
    *,
    seed: int = SEED,
    k: int = N_PICKS,
) -> dict[str, Any]:
    """The `ExplanationSummary` fields this module fills, plus `suspicious_no_clear` and
    `driver_agreement_n` for the report. `values_true` comes from EvalKit's value check later:
    pass it with these fields (minus the two extras) to `ExplanationSummary`."""
    case_map: dict[str, AlertCase] = {c.alert_id: c for c in cases}
    truth_map: dict[str, AlertTruth] = {t.alert_id: t for t in truths}
    decisions = list(decisions)
    v = validity(decisions, case_map, policy)
    mentions: dict[str, int] = {}
    for d in decisions:
        n = protected_mentions(d)
        mentions[d.alert_id] = mentions.get(d.alert_id, 0) + n
    agree = agreements(decisions, truth_map)
    return {
        "schema_valid": v.schema_valid,
        "clauses_valid": v.clauses_valid,
        "fields_valid": v.fields_valid,
        "protected_mentions": sum(mentions.values()),
        "driver_agreement": mean_agreement(agree, seed),
        "faithfulness": {},
        "judge_mode": "none",
        "mismatch_examples": mismatch_picks(agree, mentions, k),
        "suspicious_no_clear": v.suspicious_no_clear,
        "driver_agreement_n": len(agree),
    }


EXTRA_KEYS = ("suspicious_no_clear", "driver_agreement_n")


def to_summary(fields: Mapping[str, Any], values_true: Rate) -> ExplanationSummary:
    """Build the full `ExplanationSummary` once `values_true` is known."""
    data = {k: v for k, v in fields.items() if k not in EXTRA_KEYS}
    return ExplanationSummary(**data, values_true=values_true)
