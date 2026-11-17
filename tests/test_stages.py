"""A2.3: stage-1 prompt, stage runner, router hand-off and prompt safety. No network: the model
client is a fake that returns scripted replies through `with_raw_response`."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fraudreview import cache, llm, prompts, stages
from fraudreview.cache import Store
from fraudreview.config import load_config
from fraudreview.cost import SpendCap
from fraudreview.data import DROPPED
from fraudreview.doctor import price_table
from fraudreview.policy import load_policy
from fraudreview.schemas import AlertCase, Stage1Output

ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(ROOT / "config.yaml")
POLICY = load_policy()
PRICES = price_table(ROOT / "prices.yaml", "anthropic")
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "v1" / "alert.json").read_text("utf-8"))


def case(**over: Any) -> AlertCase:
    data = dict(FIXTURE["case"])
    feats = dict(data["features"])
    feats.update(over.pop("features", {}))
    data.update(over, features=feats)
    return AlertCase.model_validate(data)


ROUTINE = case(
    alert_id="a-aaaaaaaaaaaaaaa1",
    amount=12.5,
    ts_hour=14,
    risk_score=0.02,  # below the 0.045 ceiling (OWNER D-39)
    features={
        "amt_vs_usual": 0.6,
        "hour": 14,
        "category_freq": 0.4,
        "merchant_first_seen": False,
        "card_history_n": 400,
    },
)
RISKY = case(alert_id="a-bbbbbbbbbbbbbbb2")  # 9.1x usual, first merchant, night, score 0.83


def reply(action: str = "clear", clauses: tuple[str, ...] = ("P7.1",)) -> Any:
    text = json.dumps(
        {
            "reasons": ["Routine purchase in a usual category"],
            "policy_clauses": list(clauses),
            "fields_used": ["category_freq"],
            "confidence": 0.9,
            "action": action,
        }
    )
    usage = SimpleNamespace(
        input_tokens=120,
        output_tokens=300,
        cache_read_input_tokens=1500,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage
    )


class Raw:
    def __init__(self, m: Any) -> None:
        self.m, self.headers = m, {"request-id": "req_s"}

    async def parse(self) -> Any:
        return self.m


class Fake:
    def __init__(self, *replies: Any) -> None:
        self.replies, self.bodies = list(replies), []
        self.messages = self.with_raw_response = self

    async def create(self, **body: Any) -> Any:
        self.bodies.append(body)
        step = self.replies.pop(0)
        if isinstance(step, BaseException):
            raise step
        return Raw(step)


def ctx(tmp_path: Path, client: Any) -> stages.Context:
    return stages.Context(
        config=CONFIG,
        policy=POLICY,
        client=client,
        store=Store(tmp_path / "store"),
        cap=SpendCap(CONFIG.max_cost_usd),
        prices=PRICES,
    )


def go(stage: int, c: AlertCase, cx: stages.Context) -> Any:
    return asyncio.run(stages.run_stage(stage, c, cx))  # type: ignore[arg-type]


# ------------------------------------------------------------------ ST1-01 prompt


@pytest.mark.contract
def test_prompt_has_rules_job_glossary_and_the_policy_without_yaml():
    head, policy_block = prompts.system_blocks(1, POLICY)
    for words in ("You recommend; people decide", "Never invent a clause", "pass"):
        assert words in head
    assert all(f"- {k}:" in head for k in prompts.GLOSSARY)
    for clause_id in POLICY.clauses:
        assert clause_id in policy_block
    assert "when:" not in policy_block and "```" not in policy_block
    assert len(head) + len(policy_block) > 2048  # well over 512 tokens at ~4 characters each
    b = llm.build_body(
        CONFIG.stage1, [head, policy_block], prompts.render_alert(ROUTINE), Stage1Output
    )
    assert b["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_prompt_hash_tracks_rules_policy_and_schema(monkeypatch):
    h1, h2 = prompts.prompt_hash(1, POLICY), prompts.prompt_hash(2, POLICY)
    assert h1 != h2 and h1 == prompts.prompt_hash(1, POLICY)
    monkeypatch.setattr(prompts, "RULES", prompts.RULES + " Be brief.")
    assert prompts.prompt_hash(1, POLICY) != h1
    monkeypatch.undo()
    monkeypatch.setitem(prompts.OUTPUT_MODELS, 1, prompts.OUTPUT_MODELS[2])  # another schema
    assert prompts.prompt_hash(1, POLICY) != h1
    monkeypatch.undo()
    changed = type(POLICY)(
        clauses=POLICY.clauses, version="x", text=POLICY.text, _prompt=POLICY.prompt_text() + "!"
    )
    assert prompts.prompt_hash(1, changed) != h1


# ------------------------------------------------------------------ ST1-02 stage 1 and hand-off


def test_stage1_records_the_decision_and_caches_it(tmp_path):
    fake = Fake(reply("clear"))
    cx = ctx(tmp_path, fake)
    d = go(1, ROUTINE, cx)
    assert isinstance(d.output, Stage1Output) and d.output.action == "clear"
    assert d.provider == "anthropic" and d.model == "claude-haiku-5-5" and d.effort == "medium"
    assert d.prompt_hash == prompts.prompt_hash(1, POLICY) and d.policy_version == POLICY.version
    assert d.decision_id == f"{ROUTINE.alert_id}-s1" and d.cost_usd > 0 and not d.cache_hit
    assert cx.cap.actual == pytest.approx(d.cost_usd)
    again = go(1, ROUTINE, cx)
    assert again.cache_hit and len(fake.bodies) == 1  # no second call
    assert again.model_copy(update={"cache_hit": False}) == d


def test_transport_failures_are_not_cached(tmp_path):
    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    down = anthropic.APIConnectionError(request=req)

    async def no_sleep(_: float) -> None:
        return None

    fake = Fake(*([down] * 6), reply("pass"))
    cx = ctx(tmp_path, fake)
    cx.call_kw = {"sleep": no_sleep}
    d = go(1, ROUTINE, cx)
    assert d.error == "transport" and d.output is None
    retry = go(1, ROUTINE, cx)  # not a cache hit: the call is made again
    assert retry.output is not None and not retry.cache_hit and len(fake.bodies) == 7


def refusal() -> Any:
    usage = SimpleNamespace(input_tokens=120, output_tokens=3)
    return SimpleNamespace(content=[], stop_reason="refusal", usage=usage)


def test_only_an_accepted_clear_skips_stage2(tmp_path):
    cx = ctx(
        tmp_path,
        Fake(reply("clear"), reply("pass"), reply("clear"), reply("clear", ("P9.9",)), refusal()),
    )
    assert not stages.needs_stage2(go(1, ROUTINE, cx), ROUTINE, cx)  # P7.1 holds: accepted
    passed = case(alert_id="a-ccccccccccccccc3", features={"card_history_n": 401})
    assert stages.needs_stage2(go(1, passed, cx), passed, cx)  # a pass
    assert stages.needs_stage2(go(1, RISKY, cx), RISKY, cx)  # a clear the router refuses
    odd = case(alert_id="a-ddddddddddddddd4", features={"card_history_n": 402})
    assert stages.needs_stage2(go(1, odd, cx), odd, cx)  # an invented clause
    refused = case(alert_id="a-fffffffffffffff6", features={"card_history_n": 403})
    d = go(1, refused, cx)
    assert d.error == "refusal" and stages.needs_stage2(d, refused, cx)  # any error


def test_cache_key_covers_everything_that_decides_the_call(tmp_path, monkeypatch):
    fake = Fake(*[reply("pass") for _ in range(6)])
    cx = ctx(tmp_path, fake)
    go(1, ROUTINE, cx)
    go(1, ROUTINE, cx)
    assert len(fake.bodies) == 1  # same everything: a hit
    twin = ROUTINE.model_copy(update={"alert_id": "a-abababababababab"})
    assert go(1, twin, cx).alert_id == twin.alert_id and len(fake.bodies) == 2  # no collision
    real_render = prompts.render_alert
    monkeypatch.setattr(prompts, "render_alert", lambda c: real_render(c) + " ")
    go(1, ROUTINE, cx)
    assert len(fake.bodies) == 3  # the rendered text changed: a miss
    monkeypatch.undo()
    monkeypatch.setattr(llm, "CODE_VERSION", "999")
    go(1, ROUTINE, cx)
    assert len(fake.bodies) == 4  # reply handling changed: a miss
    monkeypatch.undo()
    bigger = CONFIG.model_copy(
        update={"stage1": CONFIG.stage1.model_copy(update={"max_output_tokens": 8000})}
    )
    cx.config = bigger
    go(1, ROUTINE, cx)
    assert len(fake.bodies) == 5  # a different output limit: a miss
    monkeypatch.setattr(prompts, "PROMPTS_CODE_VERSION", "999")
    go(1, ROUTINE, cx)
    assert len(fake.bodies) == 6  # the prompt module's version changed: a miss
    monkeypatch.undo()
    monkeypatch.setattr(stages, "SCHEMAS_HASH", "0" * 16)
    fake.replies.append(reply("pass"))
    go(1, ROUTINE, cx)
    assert len(fake.bodies) == 7  # the reply validators changed: a miss
    monkeypatch.undo()
    other = Fake(reply("pass"))
    cx.client = other
    cx.config = bigger.model_copy(update={"provider": "bedrock"})
    go(1, ROUTINE, cx)
    assert len(other.bodies) == 1  # another provider: a miss, never the other's decision


def test_a_cache_entry_for_another_alert_is_refused(tmp_path, monkeypatch):
    fake = Fake(reply("pass"), reply("pass"))
    cx = ctx(tmp_path, fake)
    go(1, ROUTINE, cx)
    monkeypatch.setattr(stages, "cache_key", lambda **_: "e" * 64)  # force a collision
    cx.store.put("e" * 64, stages.STAGE_CODE_VERSION, go(1, ROUTINE, cx).model_dump(mode="json"))
    with pytest.raises(ValueError, match="another alert"):
        go(1, RISKY, cx)


# ------------------------------------------------------------------ ST1-03 prompt safety


def all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in all_keys(v)}
    return set()


@pytest.mark.safety
def test_rendered_prompts_hold_only_allow_listed_keys(tmp_path):
    cases = [ROUTINE, RISKY, case(alert_id="a-eeeeeeeeeeeeeee5", features={"amt_vs_usual": None})]
    check_rendering(cases)
    fake = Fake(reply("pass"))
    go(1, RISKY, ctx(tmp_path, fake))
    sent = json.dumps(fake.bodies[0])
    assert RISKY.alert_id not in sent and FIXTURE["merchant"] not in sent
    with pytest.raises(ValueError):
        prompts.check_payload({**prompts.alert_payload(RISKY), "merchant": "Kirlin"})
    with pytest.raises(ValueError):
        bad = prompts.alert_payload(RISKY)
        bad["features"] = {**bad["features"], "gender": "F"}
        prompts.check_payload(bad)


SAMPLES = ROOT / "results" / "baseline"


@pytest.mark.skipif(
    not (SAMPLES / "cases_dev.jsonl").exists(),
    reason="the dev and eval samples are local (results/ is git-ignored); run `fraud baseline`",
)
def test_every_real_dev_and_eval_case_renders_only_allow_listed_keys():
    cases = []
    for name in ("cases_dev.jsonl", "cases_eval.jsonl"):
        lines = (SAMPLES / name).read_text("utf-8").splitlines()
        cases += [AlertCase.model_validate_json(x) for x in lines if x.strip()]
    assert len(cases) == 600
    check_rendering(cases)


def check_rendering(cases: list[AlertCase]) -> None:
    allowed = set(prompts.ALERT_KEYS) | set(prompts.FEATURE_KEYS)
    truth_words = ("label", "shap", "is_fraud", "merchant", "alert_id", "trans_ts", "card_id")
    for c in cases:
        assert all_keys(prompts.alert_payload(c)) <= allowed
        text = prompts.render_alert(c)
        assert c.alert_id not in text
        for word in (*DROPPED, *truth_words):
            assert f'"{word}"' not in text


AGENT_ROOTS = ("stages",)  # the agent path starts here; its import closure is checked
BANNED = ("truth", "baseline", "data", "features", "metrics", "cli")


def package_imports(name: str) -> set[str]:
    """`fraudreview` modules imported anywhere in a module (top level or inside functions)."""
    tree = ast.parse((ROOT / "src" / "fraudreview" / f"{name}.py").read_text("utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            mods = [mod] if mod != "fraudreview" else [f"fraudreview.{a.name}" for a in node.names]
        else:
            continue
        found |= {m.split(".")[1] for m in mods if m.startswith("fraudreview.")}
    return found


@pytest.mark.safety
def test_agent_modules_never_import_labels_or_the_baseline():
    seen: set[str] = set()
    todo = list(AGENT_ROOTS)
    while todo:  # the transitive closure of the agent path
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += sorted(package_imports(name) - seen)
    assert {"stages", "prompts", "llm", "router", "policy", "schemas"} <= seen
    assert not (seen & set(BANNED)), sorted(seen & set(BANNED))


@pytest.mark.contract
def test_all_registered_steps_match_their_code_versions():
    steps = {
        "model_call": (llm.CODE_VERSION, llm),
        "stages": (stages.STAGE_CODE_VERSION, stages),
        "prompts": (prompts.PROMPTS_CODE_VERSION, prompts),
    }
    assert cache.code_version_problems(steps) == []


@pytest.mark.contract
def test_committed_stage1_sample_is_live_cached_and_consistent():
    rec = json.loads((ROOT / "docs" / "measurements" / "stage1-sample.json").read_text("utf-8"))
    assert rec["provider"] == CONFIG.provider and rec["model"] == CONFIG.stage1.model
    assert rec["n"] == len(rec["rows"]) == 10 and rec["rerun_cache_hits"] == rec["n"]
    assert rec["accepted_clears"] + rec["to_stage2"] == rec["n"]
    for row in rec["rows"]:
        assert row["action"] in ("clear", "pass") or row["error"]
        assert row["to_stage2"] or row["action"] == "clear"  # only clears can skip stage 2
        assert set(row["clauses"]) <= set(POLICY.clauses)  # no invented clause
    assert 0 < rec["cost_usd"] < 0.02
    assert rec["cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in rec["rows"]), abs=1e-5)
    text = json.dumps(rec)
    assert '"a-' not in text and "req_" not in text  # no alert or request id
