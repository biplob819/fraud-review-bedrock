"""Our explanation checks as EvalKit scorers (E2.2, EXP-07, D-36).

Registered with EvalKit's `@scorer` decorator and announced under the `evalkit.scorers` entry-point
group in `pyproject.toml`, so `evalkit.scorers.discover()` finds them in any process where this
package is installed.

EvalKit's scorer protocol is `scorer(case, completion, **ctx) -> ScoreResult` with EvalKit's own
`Case` and `Completion` types, which describe a question-answering item. A fraud decision is not
that, so the protocol is met this way: `case` and `completion` are thin wrappers built by `wrap`
(the alert id and the action), and the real records travel in `ctx`:

- `decision`: the `TriageDecision` (required),
- `alert`: its `AlertCase` (required by `value_outcome` and `rule_compliant`),
- `truth`: its `AlertTruth` (required by `driver_agreement`),
- `policy`: a `Policy` (optional; the repository's `policy.md` by default).

Each returns a `ScoreResult` whose value is 1.0 for a good decision and 0.0 for a bad one. A reply
with no output (an error) scores 0.0 on every check, with `details["no_output"]`. Nothing here
calls a model.
"""

from __future__ import annotations

import functools
from typing import Any

from evalkit.schemas import Answer, Case, Completion
from evalkit.scorers import ScoreResult, scorer

from fraudreview import evalkit_bridge, explain, valuecheck
from fraudreview.defaults import packaged
from fraudreview.policy import Policy, load_policy
from fraudreview.schemas import AlertCase, TriageDecision
from fraudreview.truth import AlertTruth

NAMES = (
    "clause_valid",
    "field_valid",
    "protected_clean",
    "driver_agreement",
    "value_outcome",
    "rule_compliant",
)


def wrap(decision: TriageDecision, alert: AlertCase) -> tuple[Case, Completion]:
    """EvalKit `Case` and `Completion` for one decision (placeholders for the protocol)."""
    out = decision.output
    case = Case(
        id=alert.alert_id,
        question="Should this card alert be cleared?",
        context=("fraud alert",),
        source="fraudreview",
    )
    completion = Completion(
        model=decision.model,
        model_snapshot="",
        params={},
        raw_text="" if out is None else out.action,
        answer=None if out is None else Answer(label=out.action, answer="", citations=()),
        tokens_in=decision.tokens.input,
        tokens_out=decision.tokens.output,
        latency_ms=decision.latency_ms,
        cost_usd=decision.cost_usd,
        error=decision.error if out is None else None,
    )
    return case, completion


@functools.cache
def _policy() -> Policy:
    return load_policy(packaged("policy.md"))


def _need(ctx: dict[str, Any], key: str) -> Any:
    if ctx.get(key) is None:
        raise TypeError(f"this scorer needs `{key}=` in its context")
    return ctx[key]


def _no_output() -> ScoreResult:
    return ScoreResult(value=0.0, details={"no_output": True})


@scorer("clause_valid")
def clause_valid(case: Case, completion: Completion, **ctx: Any) -> ScoreResult:
    """1.0 when every cited clause id exists in the policy (B8)."""
    decision: TriageDecision = _need(ctx, "decision")
    policy = ctx.get("policy") or _policy()
    if decision.output is None:
        return _no_output()
    unknown = policy.unknown_clauses(decision.output.policy_clauses)
    return ScoreResult(value=0.0 if unknown else 1.0, details={"unknown": unknown})


@scorer("field_valid")
def field_valid(case: Case, completion: Completion, **ctx: Any) -> ScoreResult:
    """1.0 when every cited field is on the allow-list (B2)."""
    decision: TriageDecision = _need(ctx, "decision")
    if decision.output is None:
        return _no_output()
    bad = [f for f in decision.output.fields_used if f not in explain.ALLOWED_FIELDS]
    return ScoreResult(value=0.0 if bad else 1.0, details={"bad": bad})


@scorer("protected_clean")
def protected_clean(case: Case, completion: Completion, **ctx: Any) -> ScoreResult:
    """1.0 when no reason names a protected attribute (B7)."""
    decision: TriageDecision = _need(ctx, "decision")
    if decision.output is None:
        return _no_output()
    terms = [t for r in decision.output.reasons for t in explain.protected_terms(r)]
    return ScoreResult(value=0.0 if terms else 1.0, details={"terms": terms})


@scorer("driver_agreement")
def driver_agreement(case: Case, completion: Completion, **ctx: Any) -> ScoreResult:
    """The share of the comparable cited fields that are positive SHAP drivers (EXP-04).

    When no cited field has a driver counterpart there is nothing to compare: the value is 0.0 and
    `details["comparable"]` is False, so a caller averaging the score must skip those rows."""
    decision: TriageDecision = _need(ctx, "decision")
    truth: AlertTruth = _need(ctx, "truth")
    if decision.output is None:
        return _no_output()
    share = explain.driver_agreement(decision, truth)
    return ScoreResult(
        value=0.0 if share is None else share, details={"comparable": share is not None}
    )


@scorer("value_outcome")
def value_outcome(case: Case, completion: Completion, **ctx: Any) -> ScoreResult:
    """The strict value check of every reason (D-42): the value is the share of reasons verified,
    and 0.0 as soon as one reason is contradicted. Details list the outcome of each reason
    (verified, contradicted or unverifiable) and the counts."""
    decision: TriageDecision = _need(ctx, "decision")
    alert: AlertCase = _need(ctx, "alert")
    if decision.output is None:
        return _no_output()
    outcomes = valuecheck.check_decision(decision, alert)
    counts = {k: outcomes.count(k) for k in ("verified", "contradicted", "unverifiable")}
    value = 0.0 if counts["contradicted"] else counts["verified"] / len(outcomes)
    return ScoreResult(value=value, details={"per_reason": list(outcomes), **counts})


@scorer("rule_compliant")
def rule_compliant(case: Case, completion: Completion, **ctx: Any) -> ScoreResult:
    """EvalKit rule compliance (E2.5): cited rules exist and a clear's clauses hold on the alert."""
    decision: TriageDecision = _need(ctx, "decision")
    alert: AlertCase = _need(ctx, "alert")
    policy = ctx.get("policy") or _policy()
    return evalkit_bridge.compliance(decision, alert, evalkit_bridge.ruleset(policy))


__all__ = [
    "NAMES",
    "clause_valid",
    "driver_agreement",
    "field_valid",
    "protected_clean",
    "rule_compliant",
    "value_outcome",
    "wrap",
]
