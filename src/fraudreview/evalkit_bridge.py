"""EvalKit v0.3 scorers on our records (E2.5).

- `ruleset` turns `policy.md` into an EvalKit `RuleSet` (clear -> allow, no_clear -> never,
  signal -> signal), typed by the alert fields.
- `record` is the agent-visible alert (`prompts.alert_payload`) flattened to one level.
- `compliance` runs EvalKit `rule_compliance` on one decision; on a clear it agrees with router
  rules 2-4 (`Policy.clearing_allowed`), which `tests/test_evalkit_bridge.py` checks.
- `evidence` renders the record as one plain sentence per glossary field, for `faithfulness`.
- `PHRASES` and `values_true` check the numbers and common claims in each reason against the
  record without a model.

Nothing here calls a model.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from evalkit.scorers import (
    Rule,
    RuleSet,
    ScoreResult,
    ValuePhrase,
    render_evidence,
    rule_compliance,
    value_check,
)

from fraudreview.policy import CATEGORIES, FIELD_TYPES, Policy
from fraudreview.prompts import GLOSSARY, alert_payload
from fraudreview.schemas import AlertCase, Rate, TriageDecision
from fraudreview.stats import wilson

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


# A condition phrase is not read when a negation sits right before it or a "does not apply"
# follows it in the same clause: EvalKit checks negated comparisons but not negated conditions.
_NOT_BEFORE = r"(?<!\bnot )(?<!\bno )(?<!n't )(?<!\bnot at )(?<!\bnot a )(?<!\bnot an )"
_NOT_AFTER = (
    r"(?![^.;]{0,40}\b(?:does not|doesn't|do not|don't|is not|isn't|not apply|not met|"
    r"not triggered)\b)"
)


def _guarded(core: str) -> str:
    return rf"{_NOT_BEFORE}\b(?:{core}){_NOT_AFTER}"


PHRASES: tuple[ValuePhrase, ...] = (
    # "9 times usual", "9.1x the card's usual amount", "0.76 times its typical spend"
    ValuePhrase(
        pattern=r"\d[\d.,]*\s*(?:x|×|times)\s+(?:\S+\s+){0,3}?(?:usual|normal|typical|median)",
        field="amt_vs_usual",
    ),
    ValuePhrase(  # P5.9's window
        pattern=_guarded(
            r"at night|night[- ]?time|overnight|late[- ]night|in the middle of the night"
        ),
        holds="hour >= 22 or hour < 6",
    ),
    ValuePhrase(
        pattern=_guarded(
            r"first (?:purchase|time|use|transaction|visit)[^.;]{0,30}merchant|new merchant|"
            r"never (?:been )?(?:used|seen|shopped)[^.;]{0,25}merchant"
        ),
        holds="merchant_first_seen",
    ),
    ValuePhrase(  # P6.1's threshold
        pattern=_guarded(
            r"(?:rare|rarely used|rarely-used|seldom[- ]used|uncommon|unusual) (?:merchant )?"
            r"category"
        ),
        holds="category_freq < 0.05",
    ),
    ValuePhrase(  # P6.2's threshold
        pattern=_guarded(r"(?:thin|short|limited|sparse|little) (?:card |purchase )?history"),
        holds="card_history_n < 5",
    ),
)


def values_detail(
    decisions: Iterable[TriageDecision],
    cases: Iterable[AlertCase],
    *,
    phrases_only: bool = False,
) -> tuple[Rate, int, list[str]]:
    """(values_true rate, reasons seen, texts of failed checks). With `phrases_only`, only the
    `PHRASES` checks count; EvalKit's checks of other numbers are left out (a diagnostic)."""
    by_id = {c.alert_id: c for c in cases}
    k = n = total = 0
    failed: list[str] = []
    for d in decisions:
        if d.output is None:
            continue
        rec = record(by_id[d.alert_id])
        for reason in d.output.reasons:
            total += 1
            checks = value_check(reason, rec, phrases=PHRASES).details["checks"]
            if phrases_only:
                checks = [c for c in checks if c["kind"] != "number"]
            if not checks:
                continue
            n += 1
            k += all(c["ok"] for c in checks)
            failed += [c["text"] for c in checks if not c["ok"]]
    return wilson(k, n), total, failed


def values_true(decisions: Iterable[TriageDecision], cases: Iterable[AlertCase]) -> Rate:
    """The share of reasons whose value checks all pass, over the reasons holding at least one
    checkable number or phrase (`Rate.n`). Error replies have no reasons."""
    return values_detail(decisions, cases)[0]
