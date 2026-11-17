"""A2.5: batch run, cap stop and resume, freeze, dev log and the golden fixture recorder. The
model client is a fake; no network."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from fraudreview import run, stages
from fraudreview.audit import read_log
from fraudreview.cost import SpendCap
from fraudreview.policy import load_policy
from tests.test_stage2 import out
from tests.test_stages import CONFIG, POLICY, PRICES, Fake, case

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    case(alert_id=f"a-{ch * 16}", features={"card_history_n": 600 + i})
    for i, ch in enumerate("abcdef")
]


class Slow(Fake):
    """Counts calls in flight; every alert passes stage 1 and is held at stage 2."""

    def __init__(self) -> None:
        super().__init__()
        self.live = self.peak = 0

    async def create(self, **body: Any) -> Any:
        self.bodies.append(body)
        self.live += 1
        self.peak = max(self.peak, self.live)
        await asyncio.sleep(0.002)
        self.live -= 1
        from tests.test_stages import Raw

        stage2 = body["model"] == CONFIG.stage2.model
        return Raw(out("hold" if stage2 else "pass"))


def context(tmp_path: Path, client: Any, cap: float = 5.0, concurrency: int = 2) -> Any:
    cfg = CONFIG.model_copy(update={"concurrency": concurrency})
    return stages.Context(
        config=cfg,
        policy=POLICY,
        client=client,
        store=stages.Store(tmp_path / "store"),
        cap=SpendCap(cap),
        prices=PRICES,
    )


def go(cases: list[Any], cx: Any, folder: Path, **kw: Any) -> run.RunResult:
    return asyncio.run(run.run_triage(cases, cx, folder, "dev-test", **kw))


# ------------------------------------------------------------------ RUN-01


def test_batch_writes_one_disposition_per_alert_in_order_with_a_verified_audit(tmp_path):
    client = Slow()
    seen: list[tuple[int, int]] = []
    res = go(
        CASES,
        context(tmp_path, client),
        tmp_path / "r",
        progress=lambda d, n, usd: seen.append((d, n)),
        every=2,
    )
    assert client.peak <= 2 and len(client.bodies) == 12
    folder = tmp_path / "r"
    disp = [
        json.loads(x)
        for x in (folder / "dispositions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [d["alert_id"] for d in disp] == [c.alert_id for c in CASES]
    assert all(d["final"] == "hold" for d in disp)
    facts = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    events = read_log(
        folder / "audit.jsonl",
        expect_head=[facts["audit_head"]["seq"], facts["audit_head"]["hash"]],
    )
    assert len(events) == 6 * 3  # two decisions and one disposition per alert
    assert facts["n_done"] == 6 and facts["stopped"] is None and facts["billed_calls"] == 12
    assert facts["freeze"]["digest"] == run.freeze_record(CONFIG, POLICY)["digest"]
    assert seen[-1] == (6, 6) and len(seen) == 3
    assert res.billed_usd == pytest.approx(facts["decision_cost_usd"], abs=1e-6)


@pytest.mark.safety
def test_the_cap_stops_cleanly_and_a_rerun_resumes_for_free(tmp_path):
    from fraudreview import prompts
    from fraudreview.llm import build_body, worst_case_cost

    worst = 0.0
    for stage in (1, 2):
        cfg = CONFIG.stage1 if stage == 1 else CONFIG.stage2
        b = build_body(
            cfg,
            prompts.system_blocks(stage, POLICY),
            prompts.render_alert(CASES[0]),
            prompts.OUTPUT_MODELS[stage],
        )
        worst = max(worst, worst_case_cost(b, PRICES[cfg.model]))
    per_alert = 0.0047  # what Slow's two replies bill per alert (input, output, cache read)
    tight = context(tmp_path, Slow(), cap=worst + 2.2 * per_alert, concurrency=1)
    first = go(CASES, tight, tmp_path / "r")
    assert first.stopped == "cap" and 0 < len(first.triaged) < len(CASES)
    facts = json.loads((tmp_path / "r" / "run.json").read_text(encoding="utf-8"))
    assert facts["stopped"] == "cap" and facts["n_done"] == len(first.triaged)
    again_client = Slow()
    again = go(CASES, context(tmp_path, again_client), tmp_path / "r")
    assert again.stopped is None and len(again.triaged) == len(CASES)
    done_before = len(first.triaged)
    left = len(CASES) - done_before
    # finished alerts cost nothing; the alert the cap interrupted keeps its cached stage 1
    assert 2 * left - 1 <= len(again_client.bodies) <= 2 * left


def test_cli_exit_codes_for_cap_and_eval_without_freeze(tmp_path, monkeypatch, capsys):
    from fraudreview import cli, llm

    monkeypatch.setattr(llm, "make_client", lambda config: Slow())
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    text = "".join(c.model_dump_json() + "\n" for c in CASES)
    (cases_dir / "cases_dev.jsonl").write_text(text, encoding="utf-8")
    (cases_dir / "cases_eval.jsonl").write_text(text, encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    (tmp_path / "docs" / "measurements").mkdir(parents=True)
    base = [
        "triage",
        "--config",
        str(ROOT / "config.yaml"),
        "--prices",
        str(ROOT / "prices.yaml"),
        "--cases",
        str(cases_dir),
        "--out",
        str(tmp_path / "results"),
        "--store",
        str(tmp_path / "store"),
    ]
    assert cli.main([*base, "--split", "dev"]) == 0
    ledger = (tmp_path / "docs" / "measurements" / "spend-ledger.jsonl").read_text(encoding="utf-8")
    assert json.loads(ledger.splitlines()[-1])["calls"] == 12
    assert cli.main([*base, "--split", "eval"]) == 2  # nothing frozen
    assert "nothing is frozen" in capsys.readouterr().err
    # RUN-02: with labels present, a dev run appends its result to dev-runs.jsonl
    rows = [{"alert_id": c.alert_id, "label": i == 0, "shap_top5": []} for i, c in enumerate(CASES)]
    (cases_dir / "truth_dev.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    assert cli.main([*base, "--split", "dev"]) == 0
    dev_log = (tmp_path / "docs" / "measurements" / "dev-runs.jsonl").read_text(encoding="utf-8")
    line = json.loads(dev_log.splitlines()[-1])
    assert line["n"] == 6 and line["frauds"] == 1 and line["leakage"]["k"] == 0
    # RUN-01: the spend cap stops a live run with exit 3 (the estimate is made to fit)
    from fraudreview import cost

    tiny = tmp_path / "tiny.yaml"
    tiny.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("max_cost_usd: 5 ", "max_cost_usd: 0.02 "),
        encoding="utf-8",
    )
    monkeypatch.setattr(cost, "estimate_run", lambda *a, **k: cost.Estimate(1, 1, 0, 0.0, 0.0))
    capped = [*base, "--config", str(tiny), "--store", str(tmp_path / "store2")]
    assert cli.main([*capped, "--split", "dev"]) == 3
    assert "spend cap" in capsys.readouterr().err


# ------------------------------------------------------------------ RUN-03 freeze


@pytest.mark.safety
def test_freeze_digest_moves_with_every_setting_and_eval_refuses_a_change(tmp_path):
    base = run.freeze_record(CONFIG, POLICY)
    changes = [
        CONFIG.model_copy(update={"provider": "bedrock"}),
        CONFIG.model_copy(update={"seed": CONFIG.seed + 1}),
        CONFIG.model_copy(update={"clear_ceiling": 0.8}),
        CONFIG.model_copy(update={"clear_min_confidence": 0.8}),
        CONFIG.model_copy(update={"stage2": CONFIG.stage2.model_copy(update={"effort": "max"})}),
        CONFIG.model_copy(
            update={"stage1": CONFIG.stage1.model_copy(update={"max_output_tokens": 5000})}
        ),
        CONFIG.model_copy(update={"stage1": CONFIG.stage1.model_copy(update={"model": "x"})}),
    ]
    for cfg in changes:
        assert run.freeze_record(cfg, POLICY)["digest"] != base["digest"]
    other_policy = type(POLICY)(
        clauses=POLICY.clauses, version="v-other", text=POLICY.text, _prompt=POLICY.prompt_text()
    )
    assert run.freeze_record(CONFIG, other_policy)["digest"] != base["digest"]
    path = tmp_path / "freeze.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    assert run.freeze_problems(path, base) == []
    moved = run.freeze_record(changes[1], POLICY)
    problems = run.freeze_problems(path, moved)
    assert any(p.startswith("seed:") for p in problems)
    assert run.freeze_problems(tmp_path / "none.json", base)[0].startswith("nothing is frozen")


@pytest.mark.contract
def test_committed_freeze_matches_the_current_settings():
    path = ROOT / run.FREEZE
    assert run.freeze_problems(path, run.freeze_record(CONFIG, load_policy())) == []


# ------------------------------------------------------------------ RUN-02 dev log


def test_dev_log_line_scores_the_run_against_labels(tmp_path):
    res = go(CASES, context(tmp_path, Slow()), tmp_path / "r")
    labels = tmp_path / "truth.jsonl"
    rows = [{"alert_id": c.alert_id, "label": i < 2, "shap_top5": []} for i, c in enumerate(CASES)]
    labels.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    line = run.dev_log_line(res, labels, CONFIG)
    assert line["n"] == 6 and line["frauds"] == 2 and line["leakage"]["k"] == 0
    assert line["queue_mix"] == {"hold": 6, "escalate": 0}
    assert line["auto_clear_rate"]["k"] == 0


# ------------------------------------------------------------------ RUN-05 golden


def test_golden_recorder_round_trips_with_no_client(tmp_path):
    cx = context(tmp_path, Slow())
    res = go(CASES, cx, tmp_path / "r")
    labels = tmp_path / "truth.jsonl"
    rows = [{"alert_id": c.alert_id, "label": False, "shap_top5": []} for c in CASES]
    labels.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    golden = tmp_path / "golden"
    run.record_golden(res, CASES, cx, labels, golden)
    assert "req_" not in "".join(
        p.read_text(encoding="utf-8") for p in (golden / "store").rglob("*.json")
    )
    replayed = replay(golden)
    expected = json.loads((golden / "expected.json").read_text(encoding="utf-8"))["dispositions"]
    assert replayed == expected


class NoNetwork:
    def __init__(self) -> None:
        self.messages = self.with_raw_response = self

    async def create(self, **_: Any) -> Any:
        raise AssertionError("the golden replay called a model")


def replay(golden: Path) -> list[dict[str, Any]]:
    from fraudreview.schemas import AlertCase

    cases = [
        AlertCase.model_validate_json(x)
        for x in (golden / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    cx = stages.Context(
        config=CONFIG,
        policy=load_policy(),
        client=NoNetwork(),
        store=stages.Store(golden / "store"),
        cap=SpendCap(CONFIG.max_cost_usd),
        prices=PRICES,
    )

    async def all_() -> list[Any]:
        return [await stages.triage_alert(c, cx) for c in cases]

    return [t.disposition.model_dump(mode="json") for t in asyncio.run(all_())]


@pytest.mark.golden
def test_golden_replay_reproduces_the_recorded_dispositions():
    golden = ROOT / run.GOLDEN
    expected = json.loads((golden / "expected.json").read_text(encoding="utf-8"))
    assert expected["freeze"] == run.freeze_record(CONFIG, load_policy())  # same settings
    assert replay(golden) == expected["dispositions"]
    assert len(expected["dispositions"]) == 20


# ------------------------------------------------------------------ §9 planning from a run


def test_planning_tokens_come_from_a_finished_runs_live_calls():
    from fraudreview.cost import assumptions_from, estimate_run
    from fraudreview.schemas import Tokens

    t1 = Tokens(input=100, output=300, cache_read=1500, cache_write=0)
    t2 = Tokens(input=120, output=900, cache_read=1600, cache_write=0)
    a = assumptions_from([t1, t1, t2], [1, 1, 2], n_alerts=2, stage2_reached=1)
    assert a.stage1_out == 300 and a.stage2_out == 900 and a.stage2_share == 0.5
    assert a.cached_prefix == round((1500 * 2 + 1600) / 3)
    est = estimate_run(10, PRICES, CONFIG, a)
    assert est.stage2_calls == 5 and est.total_usd > 0
    with pytest.raises(ValueError):
        assumptions_from([], [], n_alerts=0, stage2_reached=0)


def test_plan_from_refuses_other_settings_and_counts_cached_stage2(tmp_path):
    from fraudreview import cli

    cx = context(tmp_path, Slow())
    go(CASES, cx, tmp_path / "r")
    plan = cli._plan_problem(tmp_path / "r", cx.config)
    assert not isinstance(plan, str) and plan.stage2_share == 1.0
    again = go(CASES, cx, tmp_path / "r2")  # all cached: no live calls to plan from
    assert again.billed_calls == 0
    assert "no live stage-1 calls" in cli._plan_problem(tmp_path / "r2", cx.config)
    other = cx.config.model_copy(update={"seed": cx.config.seed + 1})
    assert "other settings" in cli._plan_problem(tmp_path / "r", other)
    assert "not a finished run" in cli._plan_problem(tmp_path / "missing", cx.config)


@pytest.mark.contract
def test_committed_dev_runs_log_every_dev_result():
    rows = [
        json.loads(x)
        for x in (ROOT / "docs" / "measurements" / "dev-runs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if x
    ]
    assert rows and rows[0]["leakage"]["k"] == 2  # the first dev run's leak stays logged
    full = [r for r in rows if r["n"] == 100]
    assert full[-1]["leakage"]["k"] == 0
    for r in rows:
        assert r["leakage"]["n"] == r["frauds"] and r["digest"] in r["run_id"]


# ------------------------------------------------------------------ RUN-04 eval runs


@pytest.mark.contract
def test_committed_eval_runs_are_logged_honestly():
    rows = [
        json.loads(x)
        for x in (ROOT / "docs" / "measurements" / "eval-runs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if x
    ]
    assert [r["run_id"].split("-")[0] for r in rows] == ["eval", "eval2"]  # run 1 kept
    for r in rows:
        assert r["data_label"] == "simulated" and r["n"] == 500
        assert r["leakage_bar_met"] == (r["leakage"]["k"] == 0)
        assert r["leakage"]["n"] == r["frauds"] and r["auto_clear_rate"]["n"] == r["n"]
        assert (
            r["queue_mix"]["hold"] + r["queue_mix"]["escalate"] + r["auto_clear_rate"]["k"] == 500
        )
    assert rows[0]["leakage_bar_met"] is False and rows[1]["leakage_bar_met"] is True
    frozen = json.loads((ROOT / run.FREEZE).read_text(encoding="utf-8"))
    eval2_facts = json.loads(
        (ROOT / "docs" / "measurements" / "eval2-freeze.json").read_text(encoding="utf-8")
    )
    assert eval2_facts["digest"] == rows[1]["digest"]
    # the freeze later grew source hashes (logic_sources); every setting eval2 ran under is
    # still the frozen one
    shared = [k for k in eval2_facts if k != "digest"]
    assert {k: frozen[k] for k in shared} == {k: eval2_facts[k] for k in shared}
    ledger = (ROOT / "docs" / "measurements" / "spend-ledger.jsonl").read_text(encoding="utf-8")
    for r in rows:
        line = [json.loads(x) for x in ledger.splitlines() if r["run_id"] in x][-1]
        assert line["actual_usd"] == pytest.approx(r["billed_usd"], abs=1e-6)
