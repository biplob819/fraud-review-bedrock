"""E2.2: two-judge faithfulness (EXP-05). Fake adapters only: no network, no key, no cost."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from evalkit.adapters import ModelAdapter
from evalkit.adapters.base import BatchRequest, BatchResult, CompletionRequest, RawCompletion
from evalkit.scorers.faithfulness import FaithfulnessResult, JudgedClaim

from fraudreview import judging
from fraudreview.prompts import GLOSSARY
from fraudreview.schemas import Stage2Output, TriageDecision
from tests.test_evalkit_bridge import case, decision

CAP = 10.0


def _decisions(n: int = 3):
    rng = random.Random(5)
    cases = [case(rng, i) for i in range(n)]
    out = []
    for c in cases:
        out.append(decision(c, "pass", ("P6.1",), stage=1))
        d2 = decision(c, "clear", ("P6.1",), stage=2)
        assert isinstance(d2.output, Stage2Output)
        reasons = (
            f"Amount {c.amount} is routine for this card.",
            "The merchant has been used before.",
        )
        out.append(
            d2.model_copy(update={"output": d2.output.model_copy(update={"reasons": reasons})})
        )
    failed = out[1].model_copy(update={"decision_id": "d-none", "output": None, "error": "schema"})
    out.append(failed)
    return out, cases


class Fake(ModelAdapter):
    """Extractor and judge in one: claims are the explanation's sentences; verdicts by judge."""

    provider = "fake"

    def __init__(self, explanations: list[str], bad_judge: str | None = None, boom: bool = False):
        self.explanations = explanations
        self.bad_judge = bad_judge
        self.boom = boom
        self.calls = 0

    async def complete(self, req: CompletionRequest) -> RawCompletion:
        self.calls += 1
        await asyncio.sleep(0.01)  # calls overlap, so their reservations do too
        schema = dict(req.json_schema or {})
        if "claims" in schema.get("properties", {}):
            if self.boom:
                raise ValueError("extractor exploded")
            text = next(e for e in self.explanations if e in req.prompt)
            claims = [
                {"text": s.strip() + ".", "addresses_question": True}
                for s in text.rstrip(".").split(". ")
            ]
            body = json.dumps({"claims": claims})
        elif req.model_id == self.bad_judge:
            body = "not json"
        else:
            body = json.dumps(
                {"verdict": "supported", "evidence": [1], "quote": None, "rationale": "ok"}
            )
        return RawCompletion(
            model=req.model,
            model_snapshot="",
            params={},
            raw_text=body,
            tokens_in=10,
            tokens_out=10,
            latency_ms=1,
            cost_usd=0.001,
        )

    async def submit_batch(self, reqs: list[BatchRequest]) -> str:
        raise AssertionError("live only")

    async def collect_batch(
        self, batch_id: str, reqs: Mapping[str, CompletionRequest]
    ) -> list[BatchResult] | None:
        raise AssertionError("live only")


def _adapters(fake: Fake) -> dict[str, ModelAdapter]:
    return {"anthropic": fake, "openai": fake}


def _items():
    decs, cases = _decisions()
    return decs, judging.build_items(decs, cases)


def test_build_items_keeps_stage2_outputs_and_leaks_no_alert_id() -> None:
    decs, cases = _decisions()
    items = judging.build_items(decs, cases)
    assert len(items) == 3  # the stage-1 outputs and the stage-2 error are left out
    assert len({i.id for i in items}) == 3
    for i in items:
        assert ":" not in i.id
        blob = json.dumps(i.model_dump())
        assert not any(c.alert_id in blob for c in cases)
        assert i.question == judging.QUESTION
        assert len(i.evidence) == len(GLOSSARY)  # one sentence per glossary field
        assert i.explanation.count("routine") == 1


def test_estimate_prints_a_table_and_refuses_over_the_cap() -> None:
    _, items = _items()
    est = judging.estimate(items)
    assert est["items"] == 3 and est["claims"] >= 3
    assert {ln["node"] for ln in est["lines"]} == {"extract_claims", "judge"}
    assert len(est["judges"]) == 2 and est["total_usd"] > 0
    assert "estimated total" in judging.format_estimate(est)
    with pytest.raises(judging.JudgingRefused):
        judging.estimate(items, cap_usd=est["total_usd"] / 2)
    assert judging.estimate(items, cap_usd=est["total_usd"] * 2)["items"] == 3


def test_judge_refuses_over_the_cap_before_any_call_or_ledger_line(tmp_path: Path) -> None:
    _, items = _items()
    fake = Fake([i.explanation for i in items])
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(judging.JudgingRefused):
        judging.judge(
            items,
            cap_usd=0.0001,
            store_dir=tmp_path / "store",
            ledger_path=ledger,
            adapters=_adapters(fake),
        )
    assert fake.calls == 0
    assert not ledger.exists()


def test_two_judges_score_every_claim_and_a_rerun_is_free(tmp_path: Path) -> None:
    decs, items = _items()
    fake = Fake([i.explanation for i in items])
    ledger, store = tmp_path / "docs" / "ledger.jsonl", tmp_path / "store"
    kw: dict[str, Any] = {"store_dir": store, "ledger_path": ledger, "cap_usd": CAP}
    first = judging.judge(items, adapters=_adapters(fake), **kw)
    assert all(set(r.score) == set(judging.JUDGES) for r in first)
    assert all(r.n_claims == 2 and r.fully_faithful[judging.JUDGES[0]] for r in first)
    calls = fake.calls
    assert calls == 3 + 3 * 2 * 2  # extraction, then one call per claim per judge
    again = judging.judge(items, adapters=_adapters(fake), **kw)
    assert fake.calls == calls  # cached through EvalKit's store
    assert [r.score for r in again] == [r.score for r in first]
    lines = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert [x["calls"] for x in lines] == [calls, 0]
    assert lines[0]["actual_usd"] == pytest.approx(calls * 0.001)
    assert lines[1]["actual_usd"] == 0
    assert lines[0]["estimate_usd"] > 0
    summ = judging.summary(first, decs)
    assert summ["judge_mode"] == "dual"
    assert all(summ["judges"][j]["fully_faithful"]["n"] == 3 for j in judging.JUDGES)


def test_the_ledger_line_is_written_even_if_judging_raises(tmp_path: Path) -> None:
    _, items = _items()
    fake = Fake([i.explanation for i in items], boom=True)
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(ValueError, match="exploded"):
        judging.judge(
            items,
            cap_usd=CAP,
            store_dir=tmp_path / "store",
            ledger_path=ledger,
            adapters=_adapters(fake),
        )
    (line,) = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert line["calls"] == fake.calls > 0
    assert line["estimate_usd"] > 0
    assert "raised" in line["note"]


def test_spend_stops_at_the_cap(tmp_path: Path) -> None:
    from evalkit.runtime.executor import BudgetExceeded

    _, items = _items()
    fake = Fake([i.explanation for i in items])
    ledger = tmp_path / "ledger.jsonl"
    est = judging.estimate(items)
    # the estimate fits the cap, but a call's worst-case reservation does not fit what is left
    with pytest.raises(BudgetExceeded):
        judging.judge(
            items,
            cap_usd=est["total_usd"],
            store_dir=tmp_path / "store",
            ledger_path=ledger,
            adapters=_adapters(fake),
        )
    assert ledger.exists()


def test_missing_keys_refuse_without_a_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _, items = _items()
    with pytest.raises(judging.JudgingRefused, match="ANTHROPIC_API_KEY"):
        judging.judge(items, cap_usd=CAP, store_dir=tmp_path / "s", ledger_path=tmp_path / "l")


def test_summary_with_one_judge_failing_is_single(tmp_path: Path) -> None:
    decs, items = _items()
    fake = Fake([i.explanation for i in items], bad_judge=judging.JUDGES[1])
    results = judging.judge(
        items,
        cap_usd=CAP,
        store_dir=tmp_path / "store",
        ledger_path=tmp_path / "ledger.jsonl",
        adapters=_adapters(fake),
    )
    summ = judging.summary(results, decs)
    assert summ["judge_mode"] == "single"
    assert summ["judges"][judging.JUDGES[1]] is None
    assert summ["judges"][judging.JUDGES[0]]["items"] == 3
    assert summ["judge_errors"][judging.JUDGES[1]] == 6
    assert summ["judge_errors"][judging.JUDGES[0]] == 0


def _result(i: str, scores: dict[str, float], n: int = 4, errors: dict[str, int] | None = None):
    errors = errors or {}
    claims = tuple(
        JudgedClaim(
            text=f"c{k}",
            cited=(1,),
            verdicts={
                j: "supported" if k < round(s * n) else "unsupported" for j, s in scores.items()
            },
            evidence={j: () for j in scores},
        )
        for k in range(n)
    )
    return FaithfulnessResult(
        id=i,
        score=scores,
        fully_faithful={j: s == 1 for j, s in scores.items()},
        n_claims=n,
        claims=claims,
        judge_errors={j: errors.get(j, 0) for j in scores},
        pending={j: 0 for j in scores},
    )


def test_summary_none_and_lowest_three_by_fixed_rule() -> None:
    decs: list[TriageDecision] = _decisions(5)[0]
    d2 = [d for d in decs if d.stage == 2 and d.output is not None]
    a, b = judging.JUDGES
    scores = [1.0, 0.25, 0.5, 0.75, 0.25]
    results = [_result(judging.item_id(d), {a: s, b: s}) for d, s in zip(d2, scores, strict=True)]
    summ = judging.summary(results, decs)
    assert summ["judge_mode"] == "dual"
    assert summ["judges"][a]["fully_faithful"]["k"] == 1
    assert summ["judges"][a]["fully_faithful"]["n"] == 5
    ids = [d.decision_id for d in d2]
    # 0.25 twice (decision id breaks the tie), then 0.5; decision ids, never item ids
    assert summ["lowest"] == [*sorted([ids[1], ids[4]]), ids[2]]
    empty = judging.summary([], decs)
    assert empty["judge_mode"] == "none"
    assert empty["lowest"] == []


def test_every_judge_failing_is_none() -> None:
    decs = _decisions(1)[0]
    d = next(x for x in decs if x.stage == 2 and x.output is not None)
    a, b = judging.JUDGES
    r = _result(judging.item_id(d), {a: 0.0, b: 0.0}, errors={a: 4, b: 4})
    assert judging.summary([r], decs)["judge_mode"] == "none"


def test_eval_config_has_two_families() -> None:
    from evalkit.pipeline.generate import provider_for

    cfg = judging.eval_config()
    assert {provider_for(j) for j in cfg.judges} == {"anthropic", "openai"}
    assert provider_for(cfg.extractor) == "anthropic"


def test_cli_dry_run_prints_the_estimate_and_calls_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from fraudreview.cli import main

    decs, cases = _decisions()
    run = tmp_path / "run"
    run.mkdir()
    (run / "decisions.jsonl").write_text(
        "\n".join(d.model_dump_json() for d in decs) + "\n", encoding="utf-8"
    )
    cases_file = tmp_path / "cases.jsonl"
    cases_file.write_text("\n".join(c.model_dump_json() for c in cases) + "\n", encoding="utf-8")
    argv = ["judge", "--run", str(run), "--cases", str(cases_file), "--dry-run"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "3 items" in out
    assert "no model calls made" in out
    assert main([*argv, "--cap-usd", "0.0001"]) == 2  # over the cap: estimate shown, exit 2
    assert not (run / "faithfulness.json").exists()
