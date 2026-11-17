"""A2.4: stage 2, failures escalating and counted, the router hand-off and latency. The model
client is a fake; no network."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from fraudreview import metrics, prompts, stages
from fraudreview.schemas import Stage2Output
from tests.test_stages import CONFIG, RISKY, ROUTINE, Fake, case, ctx


def out(action: str, clauses: tuple[str, ...] = ("P7.1",), confidence: float = 0.9) -> Any:
    text = json.dumps(
        {
            "reasons": ["Routine purchase in a usual category"],
            "policy_clauses": list(clauses),
            "fields_used": ["category_freq"],
            "confidence": confidence,
            "action": action,
        }
    )
    usage = SimpleNamespace(
        input_tokens=150,
        output_tokens=400,
        cache_read_input_tokens=1500,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage
    )


def bad() -> Any:
    usage = SimpleNamespace(input_tokens=150, output_tokens=40)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="{oops")], stop_reason="end_turn", usage=usage
    )


def refused() -> Any:
    usage = SimpleNamespace(input_tokens=150, output_tokens=3)
    return SimpleNamespace(content=[], stop_reason="refusal", usage=usage)


def triage(tmp_path: Any, c: Any, *replies: Any) -> tuple[stages.Triaged, Fake]:
    fake = Fake(*replies)
    cx = ctx(tmp_path, fake)
    return asyncio.run(stages.triage_alert(c, cx)), fake


# ------------------------------------------------------------------ ST2-01 stage 2 call


def test_stage2_calls_sonnet_at_high_effort_with_its_own_job(tmp_path):
    t, fake = triage(tmp_path, ROUTINE, out("pass"), out("hold"))
    s2 = fake.bodies[1]
    assert s2["model"] == "claude-sonnet-5-5" and s2["output_config"]["effort"] == "high"
    assert prompts.STAGE_JOB[2] in s2["system"][0]["text"]
    assert "hold" in s2["output_config"]["format"]["schema"]["properties"]["action"]["enum"]
    d2 = t.decisions[1]
    assert isinstance(d2.output, Stage2Output) and d2.stage == 2 and d2.effort == "high"


# ------------------------------------------------------------------ ST2-03 hand-off


@pytest.mark.parametrize(
    "replies, final, queue, stage, refusal",
    [
        ((out("clear"),), "auto_cleared", None, 1, None),
        ((out("pass"), out("hold")), "hold", "priority", 2, None),
        ((out("pass"), out("escalate")), "escalate", "standard", 2, None),
        ((out("pass"), out("clear")), "auto_cleared", None, 2, None),
        (
            (out("pass"), out("clear", confidence=0.5)),
            "escalate",
            "standard",
            2,
            "s2:low_confidence",
        ),
    ],
)
def test_each_route_gives_exactly_one_disposition(tmp_path, replies, final, queue, stage, refusal):
    t, fake = triage(tmp_path, ROUTINE, *replies)
    d = t.disposition
    assert (d.final, d.queue, d.decided_at_stage) == (final, queue, stage)
    assert len(fake.bodies) == len(replies) == len(t.decisions)
    assert d.decision_ids == tuple(x.decision_id for x in t.decisions)
    if refusal:
        assert refusal in d.router_refusals


@pytest.mark.safety
def test_a_refused_stage2_clear_escalates_with_its_reasons(tmp_path):
    riskier = RISKY.model_copy(update={"risk_score": 0.95})  # at or above the 0.9 ceiling
    t, _ = triage(tmp_path, riskier, out("clear"), out("clear", confidence=0.99))
    d = t.disposition
    assert d.final == "escalate" and d.queue == "standard"
    reasons = set(d.router_refusals)
    assert any(r.startswith("s1:") for r in reasons) and any(r.startswith("s2:") for r in reasons)
    assert "s2:risk_ceiling" in reasons and any("no_clear:" in r for r in reasons)


# ------------------------------------------------------------------ ST2-02 failures


@pytest.mark.safety
def test_stage2_failures_escalate_and_are_counted_by_type(tmp_path):
    t1, f1 = triage(tmp_path / "a", ROUTINE, out("pass"), bad(), bad())
    assert t1.decisions[1].error == "schema" and t1.decisions[1].attempts == 2
    assert t1.disposition.final == "escalate" and len(f1.bodies) == 3  # one retry, then stop
    t2, f2 = triage(tmp_path / "b", ROUTINE, out("pass"), refused(), out("clear"))
    assert t2.decisions[1].error == "refusal" and len(f2.bodies) == 2  # never retried
    assert t2.disposition.final == "escalate"
    t3, _ = triage(tmp_path / "c", ROUTINE, refused(), out("hold"))  # a stage-1 refusal
    assert t3.decisions[0].error == "refusal" and t3.disposition.final == "hold"
    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    down = anthropic.APIConnectionError(request=req)

    async def no_sleep(_: float) -> None:
        return None

    fake4 = Fake(out("pass"), *([down] * 6))
    cx4 = ctx(tmp_path / "d", fake4)
    cx4.call_kw = {"sleep": no_sleep}
    t4 = asyncio.run(stages.triage_alert(ROUTINE, cx4))
    assert t4.decisions[1].error == "transport" and t4.decisions[1].attempts == 6
    assert t4.disposition.final == "escalate" and t4.disposition.queue == "standard"
    every = [*t1.decisions, *t2.decisions, *t3.decisions, *t4.decisions]
    assert stages.error_counts(every) == {"schema": 1, "refusal": 2, "transport": 1}


# ------------------------------------------------------------------ ST2-04 latency


def test_latency_counts_live_calls_only(tmp_path):
    fake = Fake(*[out("pass"), out("hold")] * 3)
    cx = ctx(tmp_path, fake)
    cases = [
        case(alert_id=f"a-{c * 16}", features={"card_history_n": 500 + i})
        for i, c in enumerate("abc")
    ]
    live = [d for c in cases for d in asyncio.run(stages.triage_alert(c, cx)).decisions]
    again = [d for c in cases for d in asyncio.run(stages.triage_alert(c, cx)).decisions]
    assert all(not d.cache_hit for d in live) and all(d.cache_hit for d in again)
    slow = [d.model_copy(update={"latency_ms": 10**9}) for d in again]  # extreme on purpose
    lat = metrics.latency(live + slow)
    assert set(lat) == {"stage1", "stage2"}
    assert lat == metrics.latency(live)  # cached replays, however slow, change nothing
    assert all(s.p95 < 10**9 for s in lat.values())
    assert all(d.latency_ms >= 0 for d in live)


@pytest.mark.contract
def test_committed_stage2_sample_is_consistent():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    rec = json.loads((root / "docs" / "measurements" / "stage2-sample.json").read_text("utf-8"))
    assert rec["provider"] == CONFIG.provider and rec["n"] == len(rec["rows"]) == 10
    from collections import Counter

    finals = [r["final"] for r in rec["rows"]]
    assert rec["dispositions"] == dict(Counter(finals)) and len(finals) == 10
    assert rec["stage2_cost_usd"] == pytest.approx(
        sum(r["stage2_cost_usd"] for r in rec["rows"]), abs=1e-5
    )
    errs = Counter(r["stage2_error"] for r in rec["rows"] if r["stage2_error"])
    assert all(rec["errors"][k] >= v for k, v in errs.items())  # stage-1 errors add to these
    ledger = [
        json.loads(x)
        for x in (root / "docs" / "measurements" / "spend-ledger.jsonl")
        .read_text("utf-8")
        .split("\n")
        if x
    ]
    line = [x for x in ledger if x["run_id"] == "a2.4-stage2-sample"][-1]
    assert line["actual_usd"] == pytest.approx(rec["total_billed_usd"], abs=1e-6)
    assert rec["total_billed_usd"] >= rec["stage2_cost_usd"]
    for stage in rec["latency_ms"].values():
        assert stage["p50"] <= stage["p95"]
    for r in rec["rows"]:
        assert r["final"] in ("auto_cleared", "hold", "escalate")
        if r["final"] == "auto_cleared":
            assert not r["refusals"]
        if r["decided_at_stage"] == 1:  # no stage 2: only an accepted stage-1 clear
            assert r["final"] == "auto_cleared" and r["stage2_action"] is None
        if r["stage2_error"]:
            assert r["final"] == "escalate"
    assert set(rec["errors"]) == {"schema", "refusal", "transport"}
    assert rec["latency_ms"]["stage2"]["p50"] <= rec["latency_ms"]["stage2"]["p95"]
    assert 0 < rec["stage2_cost_usd"] < 0.15
    assert '"a-' not in json.dumps(rec)
