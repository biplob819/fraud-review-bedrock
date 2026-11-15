import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fraudreview import schemas as s
from fraudreview.truth import Alert, AlertTruth

ROOT = Path(__file__).resolve().parent.parent
AT = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
H0 = "0" * 63 + "1"
H1 = "a" * 64


def features(**kw):
    base = dict(
        amt_vs_usual=9.1, hour=3, category_freq=0.05, merchant_first_seen=True, card_history_n=40
    )
    return s.Features(**{**base, **kw})


def case(**kw):
    base = dict(
        alert_id="a-ffffffffffffff01",
        ts_hour=3,
        ts_dow=2,
        category="shopping_net",
        amount=812.4,
        features=features(),
        risk_score=0.83,
    )
    return s.AlertCase(**{**base, **kw})


def stage2(**kw):
    base = dict(
        action="hold",
        reasons=("Amount is 9x this card's usual spend", "First purchase at this merchant"),
        policy_clauses=("P3.1", "P4.2"),
        fields_used=("amt_vs_usual", "merchant_first_seen"),
        confidence=0.62,
    )
    return s.Stage2Output(**{**base, **kw})


def stage1(**kw):
    base = dict(
        action="clear",
        reasons=("Routine purchase in a usual category",),
        policy_clauses=("P7.1",),
        fields_used=("amt_vs_usual", "category_freq"),
        confidence=0.9,
    )
    return s.Stage1Output(**{**base, **kw})


def decision(**kw):
    base = dict(
        decision_id="d-1",
        alert_id="a-ffffffffffffff01",
        stage=2,
        provider="bedrock",
        model="anthropic.claude-sonnet-5-5",
        effort="high",
        prompt_hash="ph",
        policy_version="pv",
        input_hash="ih",
        output=stage2(),
        error=None,
        attempts=1,
        tokens=s.Tokens(input=2200, output=1150, cache_read=1500),
        cost_usd=0.013,
        latency_ms=4200,
        request_id="req-1",
        cache_hit=False,
        at=AT,
    )
    return s.TriageDecision(**{**base, **kw})


def event(payload, seq=0, prev="", h=H1, alert="a-ffffffffffffff01"):
    return s.AuditEvent(seq=seq, at=AT, alert_id=alert, payload=payload, prev_hash=prev, hash=h)


def rate(k, n, lo=None, hi=None):
    p = k / n if n else 0.0
    return s.Rate(k=k, n=n, point=p, lo=p if lo is None else lo, hi=p if hi is None else hi)


def interval(p=1.0):
    return s.Interval(point=p, lo=p, hi=p)


def explanation(**kw):
    base = dict(
        schema_valid=rate(1, 1),
        clauses_valid=rate(1, 1),
        fields_valid=rate(1, 1),
        values_true=rate(1, 1),
        protected_mentions=0,
        driver_agreement=interval(0.6),
        faithfulness={"claude-opus-5-5": interval(0.9), "gpt-sol": interval(0.88)},
        judge_mode="dual",
        mismatch_examples=("a-ffffffffffffff01", "a-ffffffffffffff02", "a-ffffffffffffff03"),
    )
    return s.ExplanationSummary(**{**base, **kw})


def summary(**kw):
    base = dict(
        run_id="r-1",
        split="golden",
        n_alerts=20,
        n_frauds=4,
        policy_version="pv",
        prompt_hashes={"stage1": "a", "stage2": "b"},
        models={"stage1": "h", "stage2": "s"},
        provider="bedrock",
        baseline=s.BaselineSummary(
            threshold=0.2,
            recall_val=0.98,
            precision_val=0.2,
            recall_test=rate(4, 4),
            precision_test=rate(4, 20),
            alert_rate_test=0.01,
        ),
        leakage=rate(0, 4, hi=0.6),
        recall_end_to_end=rate(4, 4),
        auto_clear_rate=rate(10, 20),
        auto_clear_precision=rate(10, 10),
        stage1_clear_rate=rate(6, 20),
        stage2_reach_rate=rate(14, 20),
        queue_mix={"hold": 4, "escalate": 6},
        minutes_saved_per_1000=interval(3000.0),
        cost_per_1000_usd=interval(13.1),
        latency_ms={"stage2": s.LatencySummary(p50=3000, p95=7000)},
        explanation=explanation(),
        errors={},
        thresholds=(s.ThresholdResult(metric="leakage", op="==", value=0, passed=True),),
        cost_total_usd=0.26,
        started_at=AT,
        finished_at=AT,
    )
    return s.RunSummary(**{**base, **kw})


def truth(**kw):
    base = dict(
        alert_id="a-ffffffffffffff01",
        label=True,
        shap_top5=(s.Driver(feature="amt_vs_usual", contribution=1.2),),
    )
    return AlertTruth(**{**base, **kw})


def invalid(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


# ---------- SCH-01 alerts ----------


def test_alert_case_field_set_is_the_allow_list():
    assert set(s.AlertCase.model_fields) == {
        "alert_id",
        "ts_hour",
        "ts_dow",
        "category",
        "amount",
        "features",
        "risk_score",
    }
    assert set(s.Features.model_fields) | {"amount", "category", "risk_score"} == set(
        s.FeatureName.__args__
    )


@pytest.mark.safety
def test_alert_case_cannot_carry_label_or_personal_data():
    for extra in ("label", "shap_top5", "merchant", "first", "cc_num", "dob", "gender"):
        with pytest.raises(ValidationError):
            case(**{extra: "x"})
    for bad in (
        dict(category="John Smith 4111111111111111"),
        dict(category="fraud_Kirlin and Sons"),
        dict(alert_id="4111111111111111"),
    ):
        with pytest.raises(ValidationError):
            case(**bad)


@pytest.mark.safety
def test_schemas_module_does_not_import_truth():
    import subprocess
    import sys

    code = (
        "import sys, fraudreview.schemas, fraudreview.config;"
        "print('fraudreview.truth' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_features_and_case_bounds_and_agreement():
    for bad in (
        dict(hour=24),
        dict(category_freq=1.5),
        dict(card_history_n=-1),
        dict(amt_vs_usual=-1),
    ):
        with pytest.raises(ValidationError):
            features(**bad)
    assert features(amt_vs_usual=None).amt_vs_usual is None
    with pytest.raises(ValidationError):
        case(ts_hour=17)  # disagrees with features.hour


def test_no_coercion_of_booleans_and_numbers():
    invalid(s.Features, {**features().model_dump(), "merchant_first_seen": "yes"})
    invalid(s.Features, {**features().model_dump(), "hour": True})
    invalid(s.Stage2Output, {**stage2().model_dump(), "confidence": "0.5"})
    invalid(s.Stage2Output, {**stage2().model_dump(), "confidence": True})
    with pytest.raises(ValidationError):
        s.Stage2Output.model_validate_json(stage2().model_dump_json().replace("0.62", '"0.62"'))


def test_alert_view_and_truth_rules():
    Alert(case=case(), truth=truth(), merchant="Kirlin and Sons", trans_ts=AT)
    with pytest.raises(ValidationError):
        Alert(case=case(alert_id="a-ffffffffffffff02"), truth=truth(), merchant="m", trans_ts=AT)
    with pytest.raises(ValidationError):
        truth(
            shap_top5=(
                s.Driver(feature="hour", contribution=1.0),
                s.Driver(feature="hour", contribution=0.5),
            )
        )


# ---------- SCH-02 decisions ----------


@pytest.mark.parametrize(
    "bad",
    [
        dict(action="block"),
        dict(reasons=()),
        dict(reasons=("a", "b", "c", "d", "e", "f")),
        dict(reasons=("  ",)),
        dict(reasons=("\u200b",)),
        dict(reasons=("x" * 501,)),
        dict(policy_clauses=()),
        dict(policy_clauses=("3.1",)),
        dict(policy_clauses=("P3.1\n",)),
        dict(policy_clauses=("P\u0663.\u0661",)),
        dict(policy_clauses=("P3.1", "P3.1")),
        dict(fields_used=()),
        dict(fields_used=("amt_vs_usual", "amt_vs_usual")),
        dict(fields_used=("gender",)),
        dict(confidence=1.2),
        dict(confidence=-0.1),
    ],
)
def test_stage2_output_rejects(bad):
    with pytest.raises(ValidationError):
        stage2(**bad)


def test_stage1_actions_are_clear_or_pass():
    stage1(action="pass")
    invalid(s.Stage1Output, {**stage1().model_dump(), "action": "hold"})


@pytest.mark.contract
def test_decision_provenance_and_rules():
    for f in (
        "provider",
        "policy_version",
        "prompt_hash",
        "input_hash",
        "effort",
        "request_id",
        "latency_ms",
        "attempts",
        "at",
    ):
        assert f in s.TriageDecision.model_fields
    assert decision().schema_version == "1.0"
    for bad in (dict(cost_usd=-1), dict(latency_ms=-1), dict(attempts=0)):
        with pytest.raises(ValidationError):
            decision(**bad)
    with pytest.raises(ValidationError):
        s.Tokens(input=-1, output=0)
    invalid(s.TriageDecision, {**decision().model_dump(), "provider": "openai"})
    invalid(s.TriageDecision, {**decision().model_dump(), "effort": "turbo"})
    invalid(s.TriageDecision, {**decision().model_dump(), "model": " "})
    invalid(s.TriageDecision, {**decision().model_dump(), "request_id": ""})
    invalid(s.TriageDecision, {**decision().model_dump(), "at": datetime(2026, 1, 5, 14, 30)})


@pytest.mark.safety
def test_decision_has_output_or_error_never_both_or_neither():
    with pytest.raises(ValidationError):
        decision(output=None, error=None)
    with pytest.raises(ValidationError):
        decision(error="schema")  # output still set
    decision(output=None, error="refusal")
    with pytest.raises(ValidationError):
        decision(stage=1)  # stage 1 cannot carry a Stage2Output


@pytest.mark.contract
@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: decision(output=stage2(action="clear")), id="stage2-clear"),
        pytest.param(lambda: decision(stage=1, output=stage1()), id="stage1-clear"),
        pytest.param(lambda: decision(stage=1, output=stage1(action="pass")), id="stage1-pass"),
        pytest.param(lambda: decision(output=stage2(action="escalate")), id="stage2-escalate"),
        pytest.param(lambda: decision(output=stage2()), id="stage2-hold"),
        pytest.param(lambda: decision(output=None, error="transport"), id="error"),
    ],
)
def test_every_decision_shape_reads_back(build):
    original = build()
    assert s.TriageDecision.model_validate_json(original.model_dump_json()) == original
    assert s.TriageDecision.model_validate(original.model_dump()) == original
    wrapped = event(s.DecisionEvent(decision=original))
    assert s.AuditEvent.model_validate_json(wrapped.model_dump_json()) == wrapped


# ---------- SCH-03 audit ----------


def test_disposition_rules():
    s.Disposition(
        alert_id="a-ffffffffffffff01",
        final="hold",
        decided_at_stage=2,
        decision_ids=("d",),
        queue="priority",
    )
    s.Disposition(
        alert_id="a-ffffffffffffff01",
        final="auto_cleared",
        decided_at_stage=1,
        decision_ids=("d",),
        queue=None,
    )
    s.Disposition(
        alert_id="a-ffffffffffffff01",
        final="escalate",
        decided_at_stage=1,
        decision_ids=("d",),
        queue="standard",
    )
    base = {"alert_id": "a-ffffffffffffff01", "decided_at_stage": 2, "decision_ids": ("d",)}
    for final, q in (("hold", None), ("escalate", "priority"), ("auto_cleared", "standard")):
        invalid(s.Disposition, {**base, "final": final, "queue": q})
    invalid(s.Disposition, {**base, "final": "hold", "queue": "priority", "decided_at_stage": 1})
    for ids in (("d", "d"), ("",), ()):
        invalid(
            s.Disposition, {**base, "final": "escalate", "queue": "standard", "decision_ids": ids}
        )


@pytest.mark.contract
def test_audit_event_payload_is_discriminated_by_kind():
    ev = event(s.DecisionEvent(decision=decision()))
    assert ev.kind == "decision"
    raw = ev.model_dump(mode="json")
    raw["payload"]["kind"] = "disposition"
    with pytest.raises(ValidationError, match="disposition"):
        s.AuditEvent.model_validate_json(json.dumps(raw))
    with pytest.raises(ValidationError):
        event(s.DecisionEvent(decision=decision()), alert="a-ffffffffffffff09")


@pytest.mark.safety
def test_audit_event_chain_shape():
    action = s.AnalystAction(
        alert_id="a-ffffffffffffff01", action="confirm_fraud", comment=None, analyst="demo"
    )
    event(action, seq=0, prev="", h=H1)
    event(action, seq=5, prev=H0, h=H1)
    for seq, prev, h in ((5, "", H1), (0, H0, H1), (3, H1, H1), (3, H0, "h")):
        with pytest.raises(ValidationError):
            event(action, seq=seq, prev=prev, h=h)


def test_analyst_action_rules():
    invalid(
        s.AnalystAction,
        {"alert_id": "a-ffffffffffffff01", "action": "override", "comment": " ", "analyst": "demo"},
    )
    invalid(
        s.AnalystAction,
        {"alert_id": "a-ffffffffffffff01", "action": "clear", "comment": None, "analyst": " "},
    )


# ---------- SCH-04 summary ----------


@pytest.mark.parametrize(
    "kw",
    [
        dict(k=5, n=4, point=1.0, lo=1.0, hi=1.0),
        dict(k=1, n=4, point=0.3, lo=0.1, hi=0.5),
        dict(k=1, n=4, point=0.25, lo=0.3, hi=0.5),
        dict(k=1, n=4, point=0.25, lo=0.1, hi=0.2),
    ],
)
def test_rate_rejects_inconsistent(kw):
    with pytest.raises(ValidationError):
        s.Rate(**kw)


@pytest.mark.contract
def test_interval_order_and_ranges():
    for bad in (dict(point=1.0, lo=2.0, hi=3.0), dict(point=4.0, lo=2.0, hi=3.0)):
        with pytest.raises(ValidationError):
            s.Interval(**bad)
    with pytest.raises(ValidationError):
        summary(cost_per_1000_usd=s.Interval(point=-5, lo=-6, hi=-4))
    with pytest.raises(ValidationError):
        explanation(driver_agreement=interval(5))
    with pytest.raises(ValidationError):
        explanation(faithfulness={"j": interval(-3), "k": interval(0.5)})
    with pytest.raises(ValidationError):
        s.LatencySummary(p50=9000, p95=10)


@pytest.mark.safety
def test_summary_is_always_simulated_and_carries_the_leakage_bar():
    assert summary().data_label == "simulated"
    invalid(s.RunSummary, {**summary().model_dump(), "data_label": "real"})
    with pytest.raises(ValidationError):
        summary(
            thresholds=(
                s.ThresholdResult(metric="recall_end_to_end", op=">=", value=0.9, passed=True),
            )
        )
    with pytest.raises(ValidationError):
        summary(thresholds=(s.ThresholdResult(metric="leakage", op="==", value=0, passed=None),))


@pytest.mark.parametrize(
    "bad",
    [
        dict(n_frauds=21),
        dict(leakage=rate(0, 10)),
        dict(leakage=rate(3, 4), recall_end_to_end=rate(4, 4)),
        dict(auto_clear_rate=rate(10, 999)),
        dict(auto_clear_precision=rate(5, 5)),
        dict(queue_mix={"hold": 1, "escalate": 1}),
        dict(queue_mix={"foo": 10}),
        dict(errors={"oops": 1}),
        dict(finished_at=datetime(2026, 1, 5, 15, 0)),
        dict(finished_at=datetime(2026, 1, 4, tzinfo=UTC)),
    ],
)
def test_summary_cross_field_rules(bad):
    with pytest.raises(ValidationError):
        summary(**bad)


def test_explanation_judges_match_mode():
    with pytest.raises(ValidationError):
        explanation(judge_mode="none")
    explanation(judge_mode="none", faithfulness={})
    with pytest.raises(ValidationError):
        explanation(mismatch_examples=("a-ffffffffffffff01", "a-ffffffffffffff01"))


# ---------- SCH-06 format v1 ----------


@pytest.mark.safety
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nan_and_infinity_rejected(bad):
    with pytest.raises(ValidationError):
        decision(cost_usd=bad)
    with pytest.raises(ValidationError):
        s.Interval(point=bad, lo=0, hi=1)


def test_models_and_their_mappings_are_frozen():
    d = decision()
    with pytest.raises(ValidationError):
        d.cost_usd = 1.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        d.output.reasons.append("x")  # type: ignore[union-attr]
    sm = summary()
    for mutate in (
        lambda: sm.queue_mix.__setitem__("hold", -5),
        lambda: sm.errors.update({"schema": 1}),
        lambda: sm.latency_ms.pop("stage2"),
        lambda: sm.explanation.faithfulness.clear(),
    ):
        with pytest.raises(TypeError):
            mutate()
    assert hash(sm) == hash(summary())


FIXTURES = ROOT / "tests" / "fixtures" / "v1"


def build_fixtures():
    return {
        "decision.json": (s.TriageDecision, decision()),
        "decision_stage1.json": (s.TriageDecision, decision(stage=1, output=stage1())),
        "decision_stage2_clear.json": (s.TriageDecision, decision(output=stage2(action="clear"))),
        "decision_error.json": (s.TriageDecision, decision(output=None, error="refusal")),
        "decision_stage1_pass.json": (
            s.TriageDecision,
            decision(stage=1, output=stage1(action="pass")),
        ),
        "decision_stage2_escalate.json": (
            s.TriageDecision,
            decision(output=stage2(action="escalate")),
        ),
        "audit_event.json": (s.AuditEvent, event(s.DecisionEvent(decision=decision()))),
        "audit_disposition.json": (
            s.AuditEvent,
            event(
                s.Disposition(
                    alert_id="a-ffffffffffffff01",
                    final="hold",
                    decided_at_stage=2,
                    router_refusals=("predicate_false:P7.1",),
                    decision_ids=("d-0", "d-1"),
                    queue="priority",
                ),
                seq=1,
                prev=H0,
            ),
        ),
        "audit_analyst.json": (
            s.AuditEvent,
            event(
                s.AnalystAction(
                    alert_id="a-ffffffffffffff01",
                    action="override",
                    comment="Known traveller",
                    analyst="demo",
                ),
                seq=2,
                prev=H0,
            ),
        ),
        "alert.json": (Alert, Alert(case=case(), truth=truth(), merchant="Kirlin", trans_ts=AT)),
        "summary.json": (s.RunSummary, summary()),
    }


@pytest.mark.contract
@pytest.mark.parametrize("name", sorted(build_fixtures()))
def test_fixtures_round_trip_byte_identically(name):
    model, _ = build_fixtures()[name]
    text = (FIXTURES / name).read_text(encoding="utf-8")
    obj = model.model_validate_json(text)
    assert obj.model_dump_json(indent=2) + "\n" == text
    if "schema_version" in type(obj).model_fields:
        assert obj.schema_version == "1.0" and '"schema_version": "1.0"' in text


@pytest.mark.contract
def test_fixtures_match_the_builders():
    names = {p.name for p in FIXTURES.glob("*.json")}
    assert names == set(build_fixtures())
    for name, (_, obj) in build_fixtures().items():
        assert (FIXTURES / name).read_text(encoding="utf-8") == obj.model_dump_json(indent=2) + "\n"


# ---------- pass-2 regressions ----------


@pytest.mark.safety
@pytest.mark.parametrize(
    "bad",
    [
        dict(alert_id="a-4111111111111111"),
        dict(alert_id="a-1"),
        dict(category="kirlin_and_sons"),
        dict(category="john_smith"),
    ],
)
def test_ids_and_categories_cannot_carry_numbers_or_names(bad):
    with pytest.raises(ValidationError):
        case(**bad)


@pytest.mark.parametrize("blank", ["\u200b", "\ufeff", "\x00", "\u2800", " "])
def test_text_fields_need_a_visible_character(blank):
    invalid(s.TriageDecision, {**decision().model_dump(), "model": blank})
    invalid(
        s.Disposition,
        {
            "alert_id": "a-ffffffffffffff01",
            "final": "escalate",
            "decided_at_stage": 2,
            "decision_ids": (blank,),
            "queue": "standard",
        },
    )


def test_stage_is_a_strict_int():
    invalid(s.TriageDecision, {**decision().model_dump(), "stage": True})
    invalid(s.TriageDecision, {**decision().model_dump(), "stage": 2.0})
    with pytest.raises(ValidationError):
        s.TriageDecision.model_validate_json(
            decision().model_dump_json().replace('"stage":2', '"stage":true')
        )


def test_timestamps_are_normalised_to_utc():
    from datetime import timedelta, timezone

    other = AT.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert decision(at=other).model_dump_json() == decision(at=AT).model_dump_json()


def test_mappings_survive_copy_and_pickle_but_not_reinit():
    import copy
    import pickle

    sm = summary()
    with pytest.raises(TypeError):
        sm.queue_mix.__init__({"hold": 999})  # type: ignore[misc]
    assert copy.deepcopy(sm) == sm
    assert sm.model_copy(deep=True) == sm
    assert pickle.loads(pickle.dumps(sm)) == sm
    clone = copy.deepcopy(sm)
    with pytest.raises(TypeError):
        clone.queue_mix.__setitem__("hold", 1)


@pytest.mark.safety
@pytest.mark.parametrize(
    "bad",
    [
        dict(auto_clear_precision=rate(5, 10)),  # implies leaked frauds while leakage is 0
        dict(
            thresholds=(
                s.ThresholdResult(metric="leakage", op="==", value=0, passed=True),
                s.ThresholdResult(metric="leakage", op=">=", value=5, passed=True),
            )
        ),
        dict(thresholds=(s.ThresholdResult(metric="leakage", op=">=", value=5, passed=True),)),
        dict(stage1_clear_rate=rate(12, 20)),
        dict(stage1_clear_rate=rate(10, 20), stage2_reach_rate=rate(20, 20)),
    ],
)
def test_summary_cannot_contradict_itself_on_safety(bad):
    with pytest.raises(ValidationError):
        summary(**bad)


@pytest.mark.safety
def test_leakage_result_must_match_the_count():
    with pytest.raises(ValidationError):
        summary(
            leakage=rate(1, 4),
            recall_end_to_end=rate(3, 4),
            auto_clear_precision=rate(9, 10),
        )  # leakage 1 with passed=True
    leaked = summary(
        leakage=rate(1, 4),
        recall_end_to_end=rate(3, 4),
        auto_clear_precision=rate(9, 10),
        thresholds=(s.ThresholdResult(metric="leakage", op="==", value=0, passed=False),),
    )
    assert leaked.leakage.k == 1


def test_threshold_metric_names_are_fixed():
    invalid(s.ThresholdResult, {"metric": "Leakage", "op": "==", "value": 0})
    invalid(s.ThresholdResult, {"metric": "recal", "op": ">=", "value": 0.9})


def test_clause_format_reaches_the_json_schema():
    schema = json.dumps(s.Stage2Output.model_json_schema())
    assert "P[0-9]{1,3}" in schema
    with pytest.raises(ValidationError):
        stage2(policy_clauses=("P" + "9" * 100 + ".1",))


# ---------- pass-3 regressions: one allow-list rule for every text field ----------


@pytest.mark.parametrize(
    "blank", ["\u3164", "\u115f\u1160", "\uffa0", "\U0001d159", "\u2800", "..."]
)
def test_text_needs_a_letter_or_digit_that_is_not_a_filler(blank):
    with pytest.raises(ValidationError):
        stage2(reasons=(blank,))
    invalid(s.TriageDecision, {**decision().model_dump(), "model": blank})


@pytest.mark.parametrize("bad", ["\u200b", "\x00\x07", "\u3164", "ok\u202e"])
def test_override_comment_follows_the_text_rule(bad):
    invalid(
        s.AnalystAction,
        {"alert_id": "a-ffffffffffffff01", "action": "override", "comment": bad, "analyst": "demo"},
    )


@pytest.mark.parametrize(
    "bad",
    [
        dict(prompt_hashes={"stage1": "\u200b", "stage2": "b"}),
        dict(prompt_hashes={"stage3": "a", "stage2": "b"}),
        dict(models={"stage1": "\u3164", "stage2": "s"}),
    ],
)
def test_mapping_keys_and_values_follow_the_text_rule(bad):
    with pytest.raises(ValidationError):
        summary(**bad)


def test_faithfulness_judge_names_follow_the_text_rule():
    with pytest.raises(ValidationError):
        explanation(faithfulness={"\u200b": interval(0.9), "gpt-sol": interval(0.88)})


def test_timestamp_overflow_is_a_validation_error():
    from datetime import timedelta, timezone

    edge = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(ValidationError):
        decision(at=edge)


def test_mapping_cannot_be_unsealed_by_attribute():
    sm = summary()
    with pytest.raises(TypeError):
        sm.queue_mix._sealed = False  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        sm.queue_mix["hold"] = 1  # type: ignore[index]


@pytest.mark.safety
@pytest.mark.parametrize(
    "bad",
    [
        # 10 auto-cleared, so at most 10 alerts reached a person; 11 caught frauds is impossible
        dict(n_frauds=11, recall_end_to_end=rate(11, 11), leakage=rate(0, 11, hi=0.3)),
        dict(errors={"stage1": 30, "stage2": 20}),
    ],
)
def test_summary_counts_must_be_possible(bad):
    with pytest.raises(ValidationError):
        summary(**bad)
