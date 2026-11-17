"""EvalKit scorers on our records (E2.5).

- `ruleset` turns `policy.md` into an EvalKit `RuleSet` (clear -> allow, no_clear -> never,
  signal -> signal), typed by the alert fields.
- `record` is the agent-visible alert (`prompts.alert_payload`) flattened to one level.
- `compliance` runs EvalKit `rule_compliance` on one decision; on a clear it agrees with router
  rules 2-4 (`Policy.clearing_allowed`), which `tests/test_evalkit_bridge.py` checks.
- `evidence` renders the record as one plain sentence per glossary field, for `faithfulness`.
- `value_shares` checks the numbers and comparisons in each reason against the record with our own
  strict `valuecheck` (EvalKit's `value_check` is not in the release this project pins).

Nothing here calls a model.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from evalkit.scorers import Rule, RuleSet, ScoreResult, render_evidence, rule_compliance

from fraudreview import valuecheck
from fraudreview.policy import CATEGORIES, FIELD_TYPES, Policy
from fraudreview.prompts import GLOSSARY, alert_payload
from fraudreview.schemas import AlertCase, TriageDecision

KIND: dict[str, Literal["allow", "never", "signal"]] = {
    "clear": "allow",
    "no_clear": "never",
    "signal": "signal",
}
CITED_KEY = "policy_clauses"
ALLOW = 'action == "clear"'

# every key of the flat record, typed for EvalKit (category is its closed value list)
_EXTRA_FIELDS = {"ts_hour": "number", "ts_dow": "number"}  # agent-visible, not predicate fields


def field_types() -> dict[str, Any]:
    types: dict[str, Any] = {}
    for name, kind in {**FIELD_TYPES, **_EXTRA_FIELDS}.items():
        types[name] = tuple(sorted(CATEGORIES)) if kind == "category" else kind
    return types


def ruleset(policy: Policy) -> RuleSet:
    """The policy's clauses as an EvalKit rule set over the flat alert record."""
    rules = tuple(
        Rule(id=c.id, kind=KIND[c.kind], when=c.when.strip(), title=c.title)
        for c in policy.clauses.values()
    )
    return RuleSet(rules=rules, fields=field_types())


def record(case: AlertCase) -> dict[str, Any]:
    """`prompts.alert_payload` with the features lifted to the top level."""
    payload = alert_payload(case)
    features = payload.pop("features")
    return {**payload, **features}


def _decision_view(decision: TriageDecision) -> dict[str, Any]:
    out = decision.output
    if out is None:  # an error: no action, no citations; EvalKit reads it as an allow (fail safe)
        return {"action": None, CITED_KEY: []}
    return {"action": out.action, CITED_KEY: list(out.policy_clauses)}


def compliance(decision: TriageDecision, case: AlertCase, rules: RuleSet) -> ScoreResult:
    """EvalKit `rule_compliance` for one stage reply on its alert."""
    if decision.alert_id != case.alert_id:
        raise ValueError(f"decision {decision.decision_id} is for another alert")
    return rule_compliance(
        _decision_view(decision), record(case), rules, cited_key=CITED_KEY, allow=ALLOW
    )


# one sentence per glossary field, in glossary order
EVIDENCE_TEMPLATE: dict[str, str] = {
    "amount": "The purchase amount is {amount:.2f} US dollars.",
    "category": "The merchant category is {category}.",
    "risk_score": "The screening model's fraud score is {risk_score:.2f}.",
    "amt_vs_usual": "The amount is {amt_vs_usual:.1f} times the card's usual spend.",
    "hour": "The purchase was made at local hour {hour}.",
    "category_freq": "{category_freq:.0%} of the card's earlier purchases were in this category.",
    "merchant_first_seen": "The card {merchant_first_seen?has never been used at this merchant "
    "before|has been used at this merchant before}.",
    "card_history_n": "The card has {card_history_n} earlier purchases.",
}
assert list(EVIDENCE_TEMPLATE) == list(GLOSSARY), "the evidence must cover the glossary in order"


def evidence(case: AlertCase) -> list[str]:
    """The alert as plain evidence sentences, one per glossary field (for `faithfulness`)."""
    return render_evidence(record(case), list(EVIDENCE_TEMPLATE.values()))


def value_shares(decisions: Iterable[TriageDecision], cases: Iterable[AlertCase]) -> dict[str, Any]:
    """`valuecheck.shares` over every reason of every decision with an output: counts of
    verified, contradicted and unverifiable, with Wilson intervals of the first two."""
    by_id = {c.alert_id: c for c in cases}
    outcomes: list[valuecheck.Outcome] = []
    for d in decisions:
        outcomes += valuecheck.check_decision(d, by_id[d.alert_id])
    return valuecheck.shares(outcomes)
