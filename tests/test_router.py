import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fraudreview import schemas as s
from fraudreview.policy import load_policy
from fraudreview.router import (
    GuardError,
    RouterSettings,
    check_clear,
    dispose,
    guard_request,
    queues,
    reconcile,
)

ROOT = Path(__file__).resolve().parent.parent
POLICY = load_policy(ROOT / "policy.md")
SETTINGS = RouterSettings(clear_ceiling=0.9, clear_min_confidence=0.7)
AT = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
A1 = "a-ffffffffffffff01"
A2 = "a-ffffffffffffff02"

FEATURES = dict(
    amt_vs_usual=1.0, hour=14, category_freq=0.4, merchant_first_seen=False, card_history_n=50
)


def alert(alert_id=A1, risk_score=0.4, amount=30.0, **feats):
    f = {**FEATURES, **feats}
    return s.AlertCase(
        alert_id=alert_id,
        ts_hour=f["hour"],
        ts_dow=2,
        category="grocery_pos",
        amount=amount,
        features=s.Features(**f),
        risk_score=risk_score,
    )


def decision(
    stage=1,
    action: Any = "clear",
    clauses=("P7.1",),
    confidence=0.9,
    alert_id=A1,
    policy_version=None,
    error=None,
    decision_id=None,
):
    out_cls = s.Stage1Output if stage == 1 else s.Stage2Output
    output = (
        None
        if error
        else out_cls(
            action=action,
            reasons=("Routine purchase in a usual category",),
            policy_clauses=clauses,
            fields_used=("amt_vs_usual",),
            confidence=confidence,
        )
    )
    return s.TriageDecision(
        decision_id=decision_id or f"d-{stage}-{alert_id[-2:]}",
        alert_id=alert_id,
        stage=stage,
        provider="bedrock",
        model="m",
        effort="medium",
        prompt_hash="ph",
        policy_version=POLICY.version if policy_version is None else policy_version,
        input_hash="ih",
        output=output,
        error=error,
        attempts=1,
        tokens=s.Tokens(input=10, output=10, cache_read=0),
        cost_usd=0.001,
        latency_ms=100,
        request_id="r",
        cache_hit=False,
        at=AT,
    )


# ---------- RTE-01 router rules ----------


def test_a_clear_that_meets_every_rule_is_accepted():
    check = check_clear(decision(), alert(), POLICY, SETTINGS)
    assert check.accepted and check.refusals == ()


@pytest.mark.safety
@pytest.mark.parametrize(
    "dec, case, settings, reasons",
    [
        (decision(error="schema"), alert(), SETTINGS, ("error:schema",)),
        (decision(alert_id=A2), alert(), SETTINGS, ("alert_mismatch",)),
        (decision(policy_version="old"), alert(), SETTINGS, ("policy_version_mismatch",)),
        (decision(clauses=("P7.1", "P42.1")), alert(), SETTINGS, ("unknown_clause:P42.1",)),
        (decision(clauses=("P3.1",)), alert(), SETTINGS, ("no_clear_clause_cited",)),
        (decision(), alert(merchant_first_seen=True), SETTINGS, ("predicate_false:P7.1",)),
        (decision(clauses=("P7.2",)), alert(amt_vs_usual=12.0), SETTINGS, ("no_clear:P3.9",)),
        (
            decision(),
            alert(risk_score=0.6),
            RouterSettings(clear_ceiling=0.5, clear_min_confidence=0.7),
            ("risk_ceiling",),
        ),
        (
            decision(stage=2, confidence=0.6),
            alert(),
            SETTINGS,
            ("low_confidence",),
        ),
    ],
)
def test_each_rule_refuses_and_is_named(dec, case, settings, reasons):
    check = check_clear(dec, case, settings=settings, policy=POLICY)
    assert not check.accepted and check.refusals == reasons


def test_refusals_come_in_rule_order():
    check = check_clear(
        decision(stage=2, clauses=("P7.1",), confidence=0.1, policy_version="old"),
        alert(merchant_first_seen=True, risk_score=0.95),
        POLICY,
        SETTINGS,
    )
    assert check.refusals == (
        "policy_version_mismatch",
        "predicate_false:P7.1",
        "no_clear:P8.9",
        "risk_ceiling",
        "low_confidence",
    )


def test_confidence_rule_applies_to_stage_2_only():
    assert check_clear(decision(stage=1, confidence=0.1), alert(), POLICY, SETTINGS).accepted


@pytest.mark.parametrize(
    "dec",
    [
        decision(action="pass"),
        decision(stage=2, action="hold"),
        decision(stage=2, action="escalate"),
    ],
)
def test_a_reply_that_is_not_a_clear_is_never_accepted(dec):
    check = check_clear(dec, alert(), POLICY, SETTINGS)
    assert not check.accepted and check.refusals == ()


@pytest.mark.safety
def test_planted_clear_with_a_false_predicate_is_refused():
    """Tech Spec §5.7: a model "clear" whose predicate is false must be refused."""
    check = check_clear(decision(clauses=("P7.2",)), alert(amount=500.0), POLICY, SETTINGS)
    assert not check.accepted and "predicate_false:P7.2" in check.refusals


# ---------- RTE-02 one disposition per alert ----------


def test_stage_1_clear_is_auto_cleared_without_stage_2():
    d = dispose(alert(), decision(), None, POLICY, SETTINGS)
    assert (d.final, d.decided_at_stage, d.queue, d.decision_ids) == (
        "auto_cleared",
        1,
        None,
        ("d-1-01",),
    )
    with pytest.raises(ValueError, match="should not exist"):
        dispose(alert(), decision(), decision(stage=2), POLICY, SETTINGS)


@pytest.mark.parametrize(
    "s1, s2, final, queue, refusals",
    [
        (decision(action="pass"), decision(stage=2), "auto_cleared", None, ()),
        (decision(action="pass"), decision(stage=2, action="hold"), "hold", "priority", ()),
        (decision(action="pass"), decision(stage=2, action="escalate"), "escalate", "standard", ()),
        (
            decision(action="pass"),
            decision(stage=2, confidence=0.5),
            "escalate",
            "standard",
            ("s2:low_confidence",),
        ),
        (
            decision(action="pass"),
            decision(stage=2, error="refusal"),
            "escalate",
            "standard",
            ("s2:error:refusal",),
        ),
        (
            decision(clauses=("P3.1",)),
            decision(stage=2, action="hold"),
            "hold",
            "priority",
            ("s1:no_clear_clause_cited",),
        ),
        (
            decision(error="transport"),
            decision(stage=2),
            "auto_cleared",
            None,
            ("s1:error:transport",),
        ),
    ],
)
def test_stage_2_outcomes(s1, s2, final, queue, refusals):
    d = dispose(alert(), s1, s2, POLICY, SETTINGS)
    assert (d.final, d.queue, d.router_refusals, d.decided_at_stage) == (final, queue, refusals, 2)
    assert d.decision_ids == (s1.decision_id, s2.decision_id)


@pytest.mark.safety
def test_a_refused_stage_2_clear_escalates_and_never_clears():
    d = dispose(
        alert(amt_vs_usual=12.0),
        decision(clauses=("P7.2",)),
        decision(stage=2, clauses=("P7.2",)),
        POLICY,
        SETTINGS,
    )
    assert d.final == "escalate" and d.router_refusals == ("s1:no_clear:P3.9", "s2:no_clear:P3.9")


@pytest.mark.parametrize(
    "s1, s2, needle",
    [
        (decision(action="pass"), None, "stage 2 is needed"),
        (decision(action="pass"), decision(stage=2, alert_id=A2), "another alert"),
        (decision(action="pass"), decision(stage=1, decision_id="x"), "not 2"),
        (decision(stage=2), decision(stage=2), "not 1"),
    ],
)
def test_dispose_refuses_missing_or_mismatched_replies(s1, s2, needle):
    with pytest.raises(ValueError, match=needle):
        dispose(alert(), s1, s2, POLICY, SETTINGS)


def test_reconcile_names_every_problem():
    d1 = dispose(alert(A1), decision(), None, POLICY, SETTINGS)
    d2 = dispose(alert(A2), decision(alert_id=A2), None, POLICY, SETTINGS)
    assert reconcile([A1, A2], [d1, d2]) == []
    assert reconcile([A1, A2], [d1]) == [f"{A2}: no disposition"]
    assert reconcile([A1], [d1, d1]) == [f"{A1}: 2 dispositions"]
    assert reconcile([A1], [d1, d2]) == [f"{A2}: disposition for an unknown alert"]
    assert reconcile([A1, A1], [d1]) == [f"{A1}: listed more than once"]


# ---------- RTE-03 queues ----------


def test_holds_and_escalations_reach_a_person_and_clears_do_not():
    a3 = "a-ffffffffffffff03"
    ds = [
        dispose(
            alert(A1),
            decision(action="pass"),
            decision(stage=2, action="escalate"),
            POLICY,
            SETTINGS,
        ),
        dispose(alert(A2), decision(alert_id=A2), None, POLICY, SETTINGS),
        dispose(
            alert(a3),
            decision(alert_id=a3, action="pass"),
            decision(stage=2, alert_id=a3, action="hold"),
            POLICY,
            SETTINGS,
        ),
    ]
    q = queues(ds)
    assert q == {"priority": (a3,), "standard": (A1,)}
    needs_person = {d.alert_id for d in ds if d.final != "auto_cleared"}
    assert needs_person == set(q["priority"]) | set(q["standard"])
    assert not set(q["priority"]) & set(q["standard"])


# ---------- RTE-05 no-action guard ----------


@pytest.mark.safety
@pytest.mark.parametrize(
    "body",
    [
        {"model": "m", "tools": []},
        {"model": "m", "tool_choice": {"type": "auto"}},
        {"model": "m", "extra": [{"nested": {"tools": [{"name": "block_card"}]}}]},
    ],
)
def test_guard_refuses_tools_at_any_depth(body):
    with pytest.raises(GuardError):
        guard_request(body)


def test_guard_passes_a_plain_request_unchanged():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}], "max_tokens": 10}
    assert guard_request(body) is body


ACTION_NAME = re.compile(
    r"(block|freeze|decline|suspend|lock|cancel)_?(the_)?(card|account|payment)"
    r"|(contact|email|text|call|sms|notify|message|phone)_?(the_)?(customer|cardholder|client)",
    re.I,
)


@pytest.mark.safety
def test_no_function_blocks_a_card_or_contacts_a_customer():
    for path in (ROOT / "src" / "fraudreview").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                name = node.name
            elif isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            assert not (name and ACTION_NAME.search(name)), f"{path.name}: {name}"


# ---------- verifier pass 1 regressions ----------


@pytest.mark.safety
@pytest.mark.parametrize(
    "body",
    [
        {"Tools": []},
        {"toolConfig": {"tools": []}},
        {"tool-choice": "auto"},
        {"mcp_servers": [{"url": "x"}]},
        {"x": {1, 2}},
        {"x": object()},
        {1: "x"},
    ],
)
def test_guard_refuses_other_spellings_and_non_json_data(body):
    with pytest.raises(GuardError):
        guard_request(body)


def test_guard_refuses_a_request_that_contains_itself():
    body: dict = {"model": "m"}
    body["self"] = body
    with pytest.raises(GuardError, match="contain itself"):
        guard_request(body)


@pytest.mark.safety
@pytest.mark.parametrize("ceiling, confidence", [(1.5, 0.7), (0.9, 0.2), (0.0, 0.7)])
def test_router_settings_cannot_switch_a_rule_off(ceiling, confidence):
    with pytest.raises(ValueError):
        RouterSettings(clear_ceiling=ceiling, clear_min_confidence=confidence)


@pytest.mark.safety
def test_a_stage_2_clear_for_another_alert_or_policy_is_refused():
    check = check_clear(
        decision(stage=2, alert_id=A2, policy_version="old"), alert(), POLICY, SETTINGS
    )
    assert check.refusals == ("alert_mismatch", "policy_version_mismatch")


def test_queues_keep_input_order():
    ids = [f"a-ffffffffffffff{i:02x}" for i in range(4, 8)]
    ds = [
        dispose(
            alert(a),
            decision(alert_id=a, action="pass"),
            decision(stage=2, alert_id=a, action="hold" if i % 2 else "escalate"),
            POLICY,
            SETTINGS,
        )
        for i, a in enumerate(ids)
    ]
    assert queues(ds) == {"priority": (ids[1], ids[3]), "standard": (ids[0], ids[2])}


def test_guard_allows_shared_parts_that_do_not_contain_themselves():
    blocks = [{"type": "text", "text": "policy"}]
    body = {"system": blocks, "messages": [{"role": "user", "content": blocks}], "a": (), "b": ()}
    assert guard_request(body) is body


@pytest.mark.safety
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_guard_refuses_values_json_cannot_carry(value):
    with pytest.raises(GuardError, match="NaN"):
        guard_request({"temperature": value})


def test_guard_refuses_container_subclasses():
    class Sneaky(dict):
        pass

    with pytest.raises(GuardError, match="plain JSON"):
        guard_request({"x": Sneaky(a=1)})
