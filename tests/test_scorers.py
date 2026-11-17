"""EXP-07: our checks are EvalKit scorers, found by `discover()`, and score synthetic decisions.
No network, no model."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

import pytest
from evalkit.scorers import ScoreResult, discover

from fraudreview import scorers
from fraudreview.schemas import TriageDecision
from tests.test_explain import dec, truth
from tests.test_stages import RISKY, ROUTINE

GOOD_REASON = "The amount is 0.6 times the card's usual"


def score(name: str, d: TriageDecision, alert: Any = ROUTINE, **ctx: Any) -> ScoreResult:
    case, completion = scorers.wrap(d, alert)
    return discover()[name](case, completion, decision=d, alert=alert, **ctx)


def good() -> TriageDecision:
    return dec(reasons=(GOOD_REASON,))


def with_fields(d: TriageDecision, fields: tuple[str, ...]) -> TriageDecision:
    assert d.output is not None
    return d.model_copy(update={"output": d.output.model_copy(update={"fields_used": fields})})


def test_discover_finds_every_scorer() -> None:
    found = discover()
    assert set(scorers.NAMES) <= set(found)
    for name in scorers.NAMES:
        assert found[name].name == name


def test_entry_points_announce_every_scorer() -> None:
    eps = {e.name: e.value for e in entry_points(group="evalkit.scorers")}
    for name in scorers.NAMES:
        assert eps[name] == f"fraudreview.scorers:{name}"


def test_good_decision_scores_one_everywhere() -> None:
    d = good()
    t = truth(ROUTINE.alert_id, category_freq=1.0)
    for name in scorers.NAMES:
        result = score(name, d, truth=t)
        assert isinstance(result, ScoreResult)
        assert result.value == 1.0, name


def test_clause_valid_zero_for_an_invented_clause() -> None:
    r = score("clause_valid", dec(clauses=("P9.9",)))
    assert r.value == 0.0 and list(r.details["unknown"]) == ["P9.9"]


def test_field_valid_zero_for_a_field_off_the_allow_list() -> None:
    r = score("field_valid", with_fields(good(), ("gender",)))
    assert r.value == 0.0 and list(r.details["bad"]) == ["gender"]


def test_protected_clean_zero_when_a_reason_names_an_attribute() -> None:
    r = score("protected_clean", dec(reasons=("The cardholder is elderly",)))
    assert r.value == 0.0 and list(r.details["terms"]) == ["elderly"]


def test_driver_agreement_scores_the_positive_share() -> None:
    d = dec(fields=("category_freq", "amount"))
    assert score("driver_agreement", d, truth=truth(ROUTINE.alert_id, amount=1.0)).value == 0.5
    against = truth(ROUTINE.alert_id, category_freq=-1.0, amount=-1.0)
    assert score("driver_agreement", d, truth=against).value == 0.0
    none = score("driver_agreement", dec(fields=("risk_score",)), truth=against)
    assert none.value == 0.0 and none.details["comparable"] is False


def test_value_outcome_per_reason() -> None:
    d = dec(reasons=(GOOD_REASON, "The amount is 9.0 times the card's usual", "Looks normal"))
    r = score("value_outcome", d)
    assert list(r.details["per_reason"]) == ["verified", "contradicted", "unverifiable"]
    assert r.value == 0.0  # one contradicted reason zeroes the decision
    vague = score("value_outcome", dec(reasons=("Looks normal", GOOD_REASON)))
    assert vague.value == 0.5 and vague.details["unverifiable"] == 1


def test_rule_compliant_zero_for_a_clear_the_policy_forbids() -> None:
    assert score("rule_compliant", dec(clauses=("P9.9",))).value == 0.0
    risky = dec(alert_id=RISKY.alert_id, clauses=("P7.1",))  # 9.1x usual, night, first merchant
    assert score("rule_compliant", risky, alert=RISKY).value == 0.0


@pytest.mark.parametrize("name", [n for n in scorers.NAMES if n != "rule_compliant"])
def test_an_error_reply_scores_zero(name: str) -> None:
    r = score(name, dec(error="schema"), truth=truth(ROUTINE.alert_id))
    assert r.value == 0.0 and r.details["no_output"] is True


def test_a_missing_context_record_is_refused() -> None:
    d = good()
    case, completion = scorers.wrap(d, ROUTINE)
    with pytest.raises(TypeError, match="truth"):
        discover()["driver_agreement"](case, completion, decision=d)
