"""E2.5: EvalKit v0.3 scorers on our records agree with the router and read our alerts."""

import random
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fraudreview import evalkit_bridge as b
from fraudreview import schemas as s
from fraudreview.policy import CATEGORIES, load_policy
from fraudreview.prompts import GLOSSARY, alert_payload
from fraudreview.router import RouterSettings, check_clear

ROOT = Path(__file__).resolve().parent.parent
POLICY = load_policy(ROOT / "policy.md")
RULES = b.ruleset(POLICY)
PIN = "add5af9b8af3bc8482f1408aa1206edd6340bfbe"
AT = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
LOOSE = RouterSettings(clear_ceiling=0.99, clear_min_confidence=0.5)  # rules 5-6 kept out


def case(rng: random.Random, i: int, **over: Any) -> s.AlertCase:
    """A synthetic alert drawn near every policy threshold."""
    f = {
        "amt_vs_usual": rng.choice([None, *(rng.uniform(0, 14) for _ in range(4)), 1.5, 2, 5, 10]),
        "hour": rng.randrange(24),
        "category_freq": rng.choice([0.0, 0.05, 0.1, 0.2, rng.random(), rng.random()]),
        "merchant_first_seen": rng.random() < 0.4,
        "card_history_n": rng.choice([0, 2, 3, 4, 5, 9, 10, rng.randrange(2000)]),
    }
    top = {
        "amount": rng.choice([49.99, 50.0, 199.99, 200.0, round(rng.uniform(0, 1500), 2)]),
        "risk_score": rng.choice([0.9, rng.random(), rng.random()]),
        "category": rng.choice(sorted(CATEGORIES)),
    }
    f.update({k: v for k, v in over.items() if k in f})
    top.update({k: v for k, v in over.items() if k in top})
    return s.AlertCase(
        alert_id=f"a-abc{i:05x}de{i:05x}f",  # never 12 digits in a row
        ts_hour=f["hour"],
        ts_dow=rng.randrange(7),
        features=s.Features(**f),
        **top,
    )


def decision(c: s.AlertCase, action: Any, clauses: tuple[str, ...], stage: int = 1):
    cls = s.Stage1Output if stage == 1 else s.Stage2Output
    return s.TriageDecision(
        decision_id=f"d-{c.alert_id}",
        alert_id=c.alert_id,
        stage=stage,
        provider="bedrock",
        model="m",
        effort="medium",
        prompt_hash="ph",
        policy_version=POLICY.version,
        input_hash="ih",
        output=cls(
            action=action,
            reasons=("Routine purchase",),
            policy_clauses=clauses,
            fields_used=("amt_vs_usual",),
            confidence=0.9,
        ),
        error=None,
        attempts=1,
        tokens=s.Tokens(input=1, output=1, cache_read=0),
        cost_usd=0.0,
        latency_ms=1,
        request_id="r",
        cache_hit=False,
        at=AT,
    )


_MAP = {"unknown_clause": "unknown_rule", "no_clear": "never"}


def ours_as_evalkit(reasons: list[str]) -> set[str]:
    out = set()
    for r in reasons:
        if r == "no_clear_clause_cited":
            out.add("no_allow_rule_cited")
            continue
        head, _, tail = r.partition(":")
        out.add(f"{_MAP.get(head, head)}:{tail}")
    return out


# ---------- equivalence with router rules 2-4 ----------


def test_rule_compliance_agrees_with_the_router_on_2000_seeded_alerts():
    rng = random.Random(25)
    ids = [*POLICY.clauses, "P9.9", "P1.0"]  # two unknown ids
    night = 0
    seen = {True: 0, False: 0}
    clears = [i for i, c in POLICY.clauses.items() if c.kind == "clear"]
    for i in range(2400):
        over: dict[str, Any] = {}
        if i % 2:  # a routine alert that a clear clause could cover
            over = {
                "amt_vs_usual": rng.uniform(0, 1.6),
                "hour": rng.randrange(6, 22),
                "category_freq": rng.uniform(0.08, 1),
                "merchant_first_seen": rng.random() < 0.1,
                "card_history_n": rng.randrange(3, 2000),
                "amount": round(rng.uniform(0, 80), 2),
                "risk_score": rng.uniform(0, 0.95),
            }
        if i % 6 in (0, 1):  # a P5.9 night case, routine or not
            over["hour"] = rng.choice([22, 23, 0, 3, 5])
        c = case(rng, i, **over)
        night += c.features.hour >= 22 or c.features.hour < 6
        cited = tuple(rng.sample(ids, rng.randint(0, 3)))
        if not cited or rng.random() < 0.5:
            cited = tuple(dict.fromkeys((*cited, rng.choice(clears))))
        stage = rng.choice([1, 2])
        action = "clear" if rng.random() < 0.75 else ("pass" if stage == 1 else "hold")
        d = decision(c, action, cited, stage)
        ek = b.compliance(d, c, RULES)
        router = check_clear(d, c, POLICY, LOOSE)
        if action == "clear":
            allowed, why = POLICY.clearing_allowed(cited, c)
            rules_2_4 = [r for r in router.refusals if r != "risk_ceiling"]
            assert rules_2_4 == why
            assert (ek.value == 1.0) == allowed, (c, cited, ek.details, why)
            assert set(ek.details["reasons"]) == ours_as_evalkit(why)
            assert ek.details["allow"] is True
            seen[allowed] += 1
        else:  # not a clear: the router never accepts; EvalKit only checks the ids exist
            assert not router.accepted and ek.details["allow"] is False
            assert all(r.startswith("unknown_rule:") for r in ek.details["reasons"])
    assert night >= 400 and min(seen.values()) >= 100, (night, seen)


@pytest.mark.parametrize("hour", [22, 23, 0, 5])
def test_p5_9_night_refuses_a_clear_that_otherwise_holds(hour):
    c = case(
        random.Random(hour),
        1,
        hour=hour,
        amt_vs_usual=1.0,
        category_freq=0.5,
        merchant_first_seen=False,
        card_history_n=50,
        amount=20.0,
        risk_score=0.1,
    )
    ek = b.compliance(decision(c, "clear", ("P7.1",)), c, RULES)
    assert ek.value == 0.0 and "never:P5.9" in ek.details["reasons"]
    day = c.model_copy(
        update={"ts_hour": 14, "features": c.features.model_copy(update={"hour": 14})}
    )
    assert b.compliance(decision(day, "clear", ("P7.1",)), day, RULES).value == 1.0


def test_a_missing_amt_vs_usual_fails_safe_like_the_router():
    c = case(random.Random(1), 2, amt_vs_usual=None, hour=14, risk_score=0.1)
    ek = b.compliance(decision(c, "clear", ("P7.1",)), c, RULES)
    assert "missing_value:P7.1" in ek.details["reasons"] and "never:P3.9" in ek.details["reasons"]
    assert not POLICY.clearing_allowed(("P7.1",), c)[0]


def test_the_rule_set_mirrors_the_policy_and_the_record_mirrors_the_prompt():
    assert [r.id for r in RULES.rules] == list(POLICY.clauses)
    assert {r.id: r.kind for r in RULES.rules} == {
        c.id: b.KIND[c.kind] for c in POLICY.clauses.values()
    }
    c = case(random.Random(3), 3)
    payload = alert_payload(c)
    assert b.record(c) == {
        **{k: v for k, v in payload.items() if k != "features"},
        **payload["features"],
    }
    assert set(b.record(c)) == set(RULES.fields)
    RULES.check_record(b.record(c))


# ---------- evidence ----------


def test_render_evidence_gives_one_sentence_per_glossary_field():
    c = case(random.Random(4), 4, amt_vs_usual=2.04, merchant_first_seen=True, category_freq=0.25)
    sentences = b.evidence(c)
    assert len(sentences) == len(GLOSSARY) == len(set(sentences))
    assert "The amount is 2.0 times the card's usual spend." in sentences
    assert "25% of the card's earlier purchases were in this category." in sentences
    assert "The card has never been used at this merchant before." in sentences
    assert all(sen.endswith(".") and "{" not in sen for sen in sentences)
    blank = case(random.Random(4), 5, amt_vs_usual=None)
    assert "amt_vs_usual is not known." in b.evidence(blank)


# ---------- value checks ----------


def _check(reason: str, **over: Any) -> float:
    from evalkit.scorers import value_check

    c = case(random.Random(5), 6, **over)
    return value_check(reason, b.record(c), phrases=b.PHRASES).value


@pytest.mark.parametrize(
    ("reason", "over", "ok"),
    [
        ("The amount is 2 times the card's usual spend", {"amt_vs_usual": 2.0}, True),
        ("The amount is 9 times usual", {"amt_vs_usual": 2.0}, False),
        ("Amount is 9.1x the card's typical amount", {"amt_vs_usual": 9.08}, True),
        ("Purchase made at night", {"hour": 23}, True),
        ("Purchase made at night", {"hour": 14}, False),
        ("Not at night, so P5.9 does not apply", {"hour": 14}, True),
        ("First purchase at this merchant", {"merchant_first_seen": True}, True),
        ("First purchase at this merchant", {"merchant_first_seen": False}, False),
        ("A rare category for this card", {"category_freq": 0.01}, True),
        ("A rare category for this card", {"category_freq": 0.4}, False),
        ("Thin history on this card", {"card_history_n": 2}, True),
        ("Thin history on this card", {"card_history_n": 400}, False),
        (
            "Card has 400 earlier purchases, so thin history does not apply",
            {"card_history_n": 400},
            True,
        ),
    ],
)
def test_value_check_passes_true_claims_and_fails_false_ones(reason, over, ok):
    assert (_check(reason, **over) == 1.0) is ok


def test_values_true_counts_only_reasons_with_a_checkable_claim():
    c = case(random.Random(6), 7, amt_vs_usual=2.0)
    d = decision(c, "pass", ("P3.1",)).model_copy(
        update={
            "output": s.Stage1Output(
                action="pass",
                reasons=("Amount is 2 times usual", "Amount is 9 times usual", "Looks routine"),
                policy_clauses=("P3.1",),
                fields_used=("amt_vs_usual",),
                confidence=0.5,
            )
        }
    )
    rate, total, failed = b.values_detail([d], [c])
    assert (rate.k, rate.n, total) == (1, 2, 3) and failed == ["9 times usual"]
    assert b.values_true([d], [c]) == rate


# ---------- contract ----------


@pytest.mark.contract
def test_evalkit_is_pinned_to_the_v0_3_development_commit_and_its_api_imports():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pin = next(x for x in project["project"]["dependencies"] if x.startswith("evalkit-harness"))
    assert pin == f"evalkit-harness @ git+https://github.com/biplob819/evalkit@{PIN}"
    assert PIN in (ROOT / "uv.lock").read_text(encoding="utf-8")
    from evalkit.scorers import (  # noqa: F401
        Rule,
        RuleError,
        RuleSet,
        ValuePhrase,
        render_evidence,
        rule_compliance,
        value_check,
    )
    from evalkit.scorers.faithfulness import (  # noqa: F401
        FaithfulnessItem,
        FaithfulnessResult,
        faithfulness,
    )
