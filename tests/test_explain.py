"""E2.2 explanation checks: validity, protected scan, driver agreement, mismatch picks. Synthetic
decisions only; no network, no model."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from fraudreview import explain
from fraudreview.policy import load_policy
from fraudreview.schemas import AlertCase, Driver, ExplanationSummary, TriageDecision
from fraudreview.stats import wilson
from fraudreview.truth import AlertTruth
from tests.test_stages import RISKY, ROUTINE, case

ROOT = Path(__file__).resolve().parent.parent
POLICY = load_policy()
BASE = json.loads(
    (ROOT / "tests" / "fixtures" / "v1" / "decision_stage2_clear.json").read_text(encoding="utf-8")
)


def dec(
    alert_id: str = ROUTINE.alert_id,
    stage: int = 2,
    action: str = "clear",
    clauses: tuple[str, ...] = ("P7.1",),
    fields: tuple[str, ...] = ("category_freq",),
    reasons: tuple[str, ...] = ("Routine purchase in a usual category",),
    error: str | None = None,
) -> TriageDecision:
    data: dict[str, Any] = dict(BASE)
    data.update(alert_id=alert_id, stage=stage, decision_id=f"{alert_id}-s{stage}")
    if error is None:
        data["output"] = {
            "reasons": list(reasons),
            "policy_clauses": list(clauses),
            "fields_used": list(fields),
            "confidence": 0.9,
            "action": action,
        }
        data["error"] = None
    else:
        data.update(output=None, error=error)
    return TriageDecision.model_validate_json(json.dumps(data))


def truth(alert_id: str, **contribs: float) -> AlertTruth:
    return AlertTruth(
        alert_id=alert_id,
        label=False,
        shap_top5=tuple(Driver(feature=f, contribution=c) for f, c in contribs.items()),
    )


CASES = {c.alert_id: c for c in (ROUTINE, RISKY)}


# ------------------------------------------------------------------ EXP-01 validity


def test_validity_rates_over_outputs_and_errors():
    decisions = [
        dec(),
        dec(alert_id=RISKY.alert_id, action="escalate", clauses=("P3.1", "P1.1")),  # P1.1 unknown
        dec(alert_id=RISKY.alert_id, stage=1, error="schema"),
    ]
    v = explain.validity(decisions, CASES, POLICY)
    assert v.schema_valid == wilson(2, 3)
    assert v.clauses_valid == wilson(1, 2)
    assert v.fields_valid == wilson(2, 2)
    assert v.suspicious_no_clear == 0


def test_fields_check_uses_the_allow_list():
    d = dec(fields=("category_freq", "amount"))
    assert explain.fields_ok(d)
    assert not explain.fields_ok(d, allowed={"category_freq"})
    v = explain.validity([d], CASES, POLICY, allowed={"category_freq"})
    assert v.fields_valid == wilson(0, 1)
    assert set(explain.FIELD_TO_DRIVER) == explain.ALLOWED_FIELDS


def test_clear_citing_a_false_no_clear_clause_is_suspicious_not_invalid():
    # ROUTINE: hour 14, so P5.9 (night) is false; it is irrelevant to a clear
    d = dec(clauses=("P7.1", "P5.9"))
    assert explain.suspicious_no_clear(d, ROUTINE, POLICY) == ["P5.9"]
    v = explain.validity([d], CASES, POLICY)
    assert v.clauses_valid == wilson(1, 1) and v.suspicious_no_clear == 1
    # a no-clear clause that holds is not this flag; neither is a non-clear action
    night = case(alert_id="a-ccccccccccccccc3")
    assert explain.suspicious_no_clear(dec(night.alert_id, clauses=("P5.9",)), night, POLICY) == []
    assert (
        explain.suspicious_no_clear(dec(action="escalate", clauses=("P5.9",)), ROUTINE, POLICY)
        == []
    )


def test_validity_needs_every_case():
    with pytest.raises(explain.ExplainError):
        explain.validity([dec(alert_id="a-dddddddddddddddd")], CASES, POLICY)


def test_empty_run_gives_empty_rates():
    v = explain.validity([], CASES, POLICY)
    assert v.schema_valid.n == 0 and v.clauses_valid.hi == 1.0


# ------------------------------------------------------------------ EXP-03 protected scan


@pytest.mark.safety
@pytest.mark.parametrize(
    "text, found",
    [
        ("Amount is 0.8 times the card's usual amount", []),
        ("Average spend; usage is normal; stage 2 review", []),
        ("Amount near usual and a near-usual category", []),
        ("Spend nearly matches the statement", []),
        ("The customer lives near the merchant", ["near the merchant"]),
        ("Cardholder age is 72", ["age"]),
        ("Cardholder was born in 1950", ["born"]),
        ("DOB on file; date of birth matches", ["dob", "date of birth"]),
        ("Gender is female", ["gender", "female"]),
        ("Purchase in the same city and state", ["city", "state"]),
        ("ZIP code and street address differ", ["zip", "street", "address"]),
        ("Shopping in the local area", ["local area"]),
        ("Hour 22 (10 p.m. local)", []),
        ("Card used in Texas, then New  York", ["texas", "new  york"]),
        ("Usual pattern; Washington", ["washington"]),
        ("Ohioan", []),
    ],
)
def test_protected_scan_edge_cases(text, found):
    assert explain.protected_terms(text) == found


@pytest.mark.safety
def test_protected_mentions_count_per_decision():
    d = dec(reasons=("Same city as the cardholder", "Age 70 and born in Texas"))
    assert explain.protected_mentions(d) == 4
    assert explain.protected_mentions(dec()) == 0
    assert explain.protected_mentions(dec(error="refusal")) == 0


# ------------------------------------------------------------------ EXP-04 driver agreement


def test_driver_agreement_counts_only_positive_drivers():
    t = truth(ROUTINE.alert_id, amount=2.0, hour=1.0, category=-0.5, card_history_n=0.0)
    d = dec(fields=("amount", "hour", "category", "card_history_n"))
    assert explain.driver_agreement(d, t) == 0.5


def test_risk_score_has_no_driver_and_is_left_out():
    t = truth(ROUTINE.alert_id, amount=2.0)
    assert explain.driver_agreement(dec(fields=("amount", "risk_score")), t) == 1.0
    assert explain.driver_agreement(dec(fields=("risk_score",)), t) is None
    assert explain.driver_agreement(dec(error="schema"), t) is None


def test_field_names_match_the_baseline_driver_names():
    baseline = pytest.importorskip("fraudreview.baseline")
    drivers = {v for v in explain.FIELD_TO_DRIVER.values() if v is not None}
    assert drivers == set(baseline.MODEL_FEATURES)


def test_agreements_use_stage2_outputs_only_and_mean_is_seeded():
    truths = {
        ROUTINE.alert_id: truth(ROUTINE.alert_id, category_freq=1.0),
        RISKY.alert_id: truth(RISKY.alert_id, amount=1.0),
    }
    decisions = [
        dec(stage=1, action="pass"),
        dec(),  # stage 2, category_freq positive: 1.0
        dec(alert_id=RISKY.alert_id, fields=("amount", "hour")),  # 0.5
    ]
    agree = explain.agreements(decisions, truths)
    assert agree == {ROUTINE.alert_id: 1.0, RISKY.alert_id: 0.5}
    iv = explain.mean_agreement(agree, seed=1)
    assert iv.point == 0.75 and 0 <= iv.lo <= iv.point <= iv.hi <= 1
    assert explain.mean_agreement(dict(reversed(list(agree.items()))), seed=1) == iv
    assert explain.mean_agreement({}).point == 0.0


def test_agreements_refuse_missing_truth_and_duplicates():
    with pytest.raises(explain.ExplainError):
        explain.agreements([dec()], {})
    t = {ROUTINE.alert_id: truth(ROUTINE.alert_id, amount=1.0)}
    with pytest.raises(explain.ExplainError):
        explain.agreements([dec(), dec()], t)


# ------------------------------------------------------------------ EXP-06 mismatch picks


def test_picks_lowest_agreement_then_protected_then_id():
    agree = {
        "a-0000000000000004": 0.0,
        "a-0000000000000003": 0.0,
        "a-0000000000000002": 0.0,
        "a-0000000000000001": 0.5,
        "a-0000000000000005": 1.0,
    }
    mentions = {"a-0000000000000004": 1}
    picks = explain.mismatch_picks(agree, mentions)
    assert picks == ("a-0000000000000004", "a-0000000000000002", "a-0000000000000003")
    assert explain.mismatch_picks(agree, mentions, k=5)[-2:] == (
        "a-0000000000000001",
        "a-0000000000000005",
    )


def test_picks_are_deterministic_under_shuffling():
    rng = random.Random(7)
    agree = {f"a-{i:016x}": rng.choice([0.0, 0.25, 0.5, 1.0]) for i in range(40)}
    mentions = {k: rng.choice([0, 0, 1]) for k in agree}
    first = explain.mismatch_picks(agree, mentions, k=5)
    for _ in range(5):
        items = list(agree.items())
        rng.shuffle(items)
        assert explain.mismatch_picks(dict(items), mentions, k=5) == first
    assert len(first) == 5


def test_picks_include_a_protected_mention_without_a_score_last():
    picks = explain.mismatch_picks({"a-0000000000000002": 1.0}, {"a-0000000000000001": 2}, k=3)
    assert picks == ("a-0000000000000002", "a-0000000000000001")


# ------------------------------------------------------------------ summary


def test_summarise_fills_its_fields_and_leaves_evalkit_ones():
    cases: list[AlertCase] = [ROUTINE, RISKY]
    truths = [
        truth(ROUTINE.alert_id, category_freq=1.0),
        truth(RISKY.alert_id, amount=1.0, hour=-1.0),
    ]
    decisions = [
        dec(stage=1, action="pass"),
        dec(clauses=("P7.1", "P5.9")),
        dec(alert_id=RISKY.alert_id, stage=1, action="pass"),
        dec(
            alert_id=RISKY.alert_id,
            action="escalate",
            clauses=("P3.1",),
            fields=("hour",),
            reasons=("Night purchase in another state",),
        ),
    ]
    s = explain.summarise(decisions, cases, truths, POLICY)
    assert s["faithfulness"] == {} and s["judge_mode"] == "none"
    assert s["schema_valid"] == wilson(4, 4)
    assert s["protected_mentions"] == 1 and s["suspicious_no_clear"] == 1
    assert s["driver_agreement"].point == 0.5 and s["driver_agreement_n"] == 2
    assert s["mismatch_examples"] == (RISKY.alert_id, ROUTINE.alert_id)
    assert explain.summarise(decisions, cases, truths, POLICY) == s
    full = explain.to_summary(s, values_true=wilson(0, 0))
    assert isinstance(full, ExplanationSummary) and full.judge_mode == "none"


@pytest.mark.safety
@pytest.mark.parametrize(
    "text, flagged",
    [
        ("females", True),
        ("the cardholder is elderly", True),
        ("a young customer", True),
        ("religion", True),
        ("nationality", True),
        ("near the cardholder's home", True),
        ("the customer's state", True),
        ("state of the art", False),
        ("age of the card", False),
        ("the card's age", False),
        ("account age", False),
        ("near midnight", False),
        ("near a high-risk category", False),
        ("amount near usual", False),
        ("local hour 22", False),
        ("usage", False),
    ],
)
def test_protected_scan_catches_attributes_and_leaves_card_and_amount_words(text, flagged):
    assert bool(explain.protected_terms(text)) is flagged
