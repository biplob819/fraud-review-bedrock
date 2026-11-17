"""N1.3: the model client, retries, cache, cost, spend cap, ledger, concurrency and provider
switch. Every client is a fake; no test touches the network."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from fraudreview import cache, cost, ledger, llm
from fraudreview.config import load_config
from fraudreview.doctor import price_table
from fraudreview.router import GuardError
from fraudreview.schemas import Stage1Output, Stage2Output, Tokens

ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(ROOT / "config.yaml")
PRICES = price_table(ROOT / "prices.yaml", "anthropic")
GOOD = json.dumps(
    {
        "reasons": ["Routine purchase in a usual category"],
        "policy_clauses": ["P7.1"],
        "fields_used": ["amt_vs_usual"],
        "confidence": 0.8,
        "action": "clear",
    }
)
REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(code: int) -> anthropic.APIStatusError:
    cls = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        529: anthropic.InternalServerError,
    }.get(code, anthropic.APIStatusError)
    return cls(
        "sk-ant-supersecret in message", response=httpx2.Response(code, request=REQ), body=None
    )


def reply(text: str | None = GOOD, stop: str = "end_turn", **usage: int) -> Any:
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    u = {"input_tokens": 700, "output_tokens": 200, "cache_read_input_tokens": 1500}
    u.update(usage)
    return SimpleNamespace(
        content=content, stop_reason=stop, usage=SimpleNamespace(**u), _request_id="req_1"
    )


class Script:
    """A fake async client: each call pops the next scripted reply or exception."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.bodies: list[dict[str, Any]] = []
        self.messages = self

    async def create(self, **body: Any) -> Any:
        self.bodies.append(body)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def body(stage: int = 1) -> dict[str, Any]:
    model = Stage1Output if stage == 1 else Stage2Output
    cfg = CONFIG.stage1 if stage == 1 else CONFIG.stage2
    return llm.build_body(cfg, ["You recommend; people decide.", "Policy text"], "{}", model)


def run(client: Any, b: dict[str, Any] | None = None, model: Any = Stage1Output) -> Any:
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    result = asyncio.run(
        llm.call_model(client, b or body(), model, sleep=sleep, rng=random.Random(0))
    )
    result.sleeps = sleeps  # type: ignore[attr-defined]
    return result


# ------------------------------------------------------------------ BRK-01 client wrapper


@pytest.mark.safety
def test_body_is_schema_constrained_cached_and_has_no_tools_or_sampling():
    b = body(2)
    assert b["model"] == "claude-sonnet-5-5" and b["max_tokens"] == 8192
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"]["effort"] == "high"
    schema = b["output_config"]["format"]["schema"]
    assert b["output_config"]["format"]["type"] == "json_schema"
    assert schema["additionalProperties"] is False and "action" in schema["required"]
    assert b["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in b["system"][0]
    for banned in ("temperature", "top_p", "top_k", "tools", "tool_choice"):
        assert banned not in b
    assert b["messages"] == [{"role": "user", "content": "{}"}]
    with pytest.raises(ValueError):
        llm.build_body(CONFIG.stage1, [], "{}", Stage1Output)


@pytest.mark.safety
def test_every_send_is_guarded_and_a_tool_body_never_leaves():
    class Mutating(Script):  # a body that gains a tool between attempts is refused on retry
        async def create(self, **b: Any) -> Any:
            self.bodies.append(b)
            if len(self.bodies) == 1:
                shared["output_config"]["tools"] = [{"name": "block_card"}]
                raise status_error(529)
            return reply()

    shared = body()
    sneaky = Mutating()
    with pytest.raises(GuardError):
        run(sneaky, shared)
    assert len(sneaky.bodies) == 1  # the second send never happened

    client = Script(reply())
    bad = {**body(), "tools": [{"name": "block_card"}]}
    with pytest.raises(GuardError):
        run(client, bad)
    assert client.bodies == []  # refused before any call
    nested = body()
    nested["messages"][0]["content"] = [{"type": "text", "text": "x", "tool_choice": "any"}]
    with pytest.raises(GuardError):
        run(Script(reply()), nested)


def test_reply_is_validated_by_the_pydantic_model_not_only_the_api():
    ok = run(Script(reply()))
    assert isinstance(ok.output, Stage1Output) and ok.error is None and ok.attempts == 1
    # the API drops lengths and ranges; pydantic still enforces them
    too_confident = json.loads(GOOD) | {"confidence": 1.5}
    bad = run(Script(reply(json.dumps(too_confident)), reply(json.dumps(too_confident))))
    assert bad.output is None and bad.error == "schema"
    stage1_only = json.loads(GOOD) | {"action": "pass"}  # "pass" exists only at stage 1
    wrong = run(
        Script(reply(json.dumps(stage1_only)), reply(json.dumps(stage1_only))), model=Stage2Output
    )
    assert wrong.error == "schema"


# ------------------------------------------------------------------ BRK-02 errors and retries


def test_transient_errors_back_off_then_give_transport_after_six():
    errors = [status_error(529), status_error(429), status_error(500)]
    errors += [anthropic.APIConnectionError(request=REQ), anthropic.APITimeoutError(REQ)]
    errors += [status_error(529)]
    out = run(Script(*errors))
    assert out.error == "transport" and out.attempts == 6 and out.output is None
    assert len(out.sleeps) == 5 and all(0 <= s <= llm.BACKOFF_CAP_S for s in out.sleeps)
    recovered = run(Script(status_error(529), status_error(429), reply()))
    assert recovered.error is None and recovered.attempts == 3


def test_backoff_is_exponential_with_full_jitter():
    rng = random.Random(1)
    for attempt in range(1, 10):
        cap = min(llm.BACKOFF_CAP_S, llm.BACKOFF_BASE_S * 2 ** (attempt - 1))
        draws = [llm.backoff_s(attempt, rng) for _ in range(200)]
        assert all(0 <= d <= cap for d in draws) and max(draws) > cap * 0.8


def test_invalid_reply_retries_once_refusal_never():
    once = run(Script(reply("not json"), reply()))
    assert once.error is None and once.attempts == 2
    twice = run(Script(reply("not json"), reply("{}")))
    assert twice.error == "schema" and twice.attempts == 2
    cut = run(Script(reply(GOOD, stop="max_tokens"), reply(None)))
    assert cut.error == "schema"
    refused = run(Script(reply(None, stop="refusal"), reply()))
    assert refused.error == "refusal" and refused.attempts == 1


def test_tokens_from_every_attempt_are_summed():
    out = run(Script(reply("bad", output_tokens=50), reply(output_tokens=60, input_tokens=10)))
    assert out.tokens.output == 110 and out.tokens.input == 710
    assert out.tokens.cache_read == 3000


@pytest.mark.safety
def test_config_errors_raise_exit_2_never_transport_and_show_no_secret():
    for code in (400, 401, 403, 404):
        with pytest.raises(llm.ProviderConfigError) as exc:
            run(Script(status_error(code)))
        assert "supersecret" not in str(exc.value) and exc.value.__cause__ is None
    with pytest.raises(KeyError):  # an unknown bug is not swallowed as transport
        run(Script(KeyError("x")))
    for missing in (
        TypeError("Could not resolve authentication method. Expected api_key to be set"),
        RuntimeError("Could not resolve AWS credentials from session"),
        anthropic.AnthropicError("No AWS region found"),
        status_error(402),
    ):
        with pytest.raises(llm.ProviderConfigError):
            run(Script(missing))


def test_aws_credential_errors_are_config_and_bugs_are_not_hidden():
    from botocore.exceptions import NoCredentialsError, ProfileNotFound

    for exc in (ProfileNotFound(profile="nonexistent"), NoCredentialsError()):
        with pytest.raises(llm.ProviderConfigError):
            run(Script(exc))
    with pytest.raises(llm.ProviderConfigError, match="HTTP 400"):
        run(Script(status_error(400)))
    with pytest.raises(TypeError):  # an ordinary bug mentioning a region stays a bug
        run(Script(TypeError("__init__() got an unexpected keyword argument 'aws_region'")))
    bad_200 = anthropic.APIResponseValidationError(
        response=httpx2.Response(200, request=REQ), body=None
    )
    recovered = run(Script(bad_200, reply()))  # billed but unreadable: an invalid reply
    assert recovered.error is None and recovered.attempts == 2 and recovered.unmetered == 1
    lost = run(Script(bad_200, bad_200, reply()))
    assert lost.error == "schema" and lost.unmetered == 2
    b, price = body(), PRICES["claude-haiku-5-5"]
    assert llm.billed_cost(lost.tokens, 2, b, price) == pytest.approx(llm.worst_case_cost(b, price))
    with pytest.raises(llm.ProviderConfigError) as err:  # earlier billed tokens are kept
        run(Script(reply("not json", output_tokens=40), status_error(400)))
    assert err.value.tokens.output == 40


def message_json(usage: Any = None, text: str = GOOD) -> dict[str, Any]:
    m: dict[str, Any] = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
    }
    if usage is not None:
        m["usage"] = usage
    return m


def test_unreadable_billed_replies_are_priced_at_worst_case(monkeypatch):
    """Through the real SDK client on a mock transport: a 200 whose body is not JSON, is plain
    text, or has no readable usage is billed, so it is unmetered and priced at the worst case."""
    replies: list[httpx2.Response] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        return replies.pop(0)

    def client() -> anthropic.AsyncAnthropic:
        transport = httpx2.MockTransport(handler)
        return anthropic.AsyncAnthropic(
            api_key="sk-ant-test",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=transport),
        )

    def json_reply(data: Any) -> httpx2.Response:
        return httpx2.Response(200, json=data)

    def raw(text: str, ctype: str) -> httpx2.Response:
        return httpx2.Response(200, content=text.encode(), headers={"content-type": ctype})

    good_usage = {"input_tokens": 700, "output_tokens": 50}
    replies[:] = [json_reply(message_json(good_usage))]
    ok = run(client())
    assert ok.error is None and ok.unmetered == 0 and ok.tokens.output == 50
    cases = [
        ([raw("{not json", "application/json"), json_reply(message_json(good_usage))], 1, None),
        ([raw("plain words", "text/plain"), raw("more", "text/plain")], 2, "schema"),
        ([json_reply(message_json())], 1, None),  # valid reply, no usage: still billed
        ([json_reply(message_json({"input_tokens": "7", "output_tokens": None}))], 1, None),
        (  # invalid UTF-8 in a JSON body: the SDK raises UnicodeDecodeError
            [raw('{"a":"\xc3\x28"}', "application/json"), json_reply(message_json(good_usage))],
            1,
            None,
        ),
        (  # content that is not a list, with readable usage: an invalid reply, metered
            [json_reply({**message_json(good_usage), "content": 5})] * 2,
            0,
            "schema",
        ),
    ]
    b, price = body(), PRICES["claude-haiku-5-5"]
    for script, unmetered, error in cases:
        replies[:] = script
        out = run(client())
        assert out.unmetered == unmetered and out.error == error, script
        billed = llm.billed_cost(out.tokens, out.unmetered, b, price)
        assert billed >= unmetered * llm.worst_case_cost(b, price) / 2 and billed > 0


def test_settled_call_records_spend_on_every_path():
    b, price = body(), PRICES["claude-haiku-5-5"]
    worst = llm.worst_case_cost(b, price)

    async def go(client: Any, cap: cost.SpendCap) -> Any:
        async def sleep(_: float) -> None:
            return None

        return await llm.settled_call(
            client, b, Stage1Output, cap=cap, price=price, sleep=sleep, rng=random.Random(0)
        )

    cap = cost.SpendCap(5.0)
    result, usd = asyncio.run(go(Script(reply()), cap))
    assert usd == pytest.approx(cost.call_cost(result.tokens, price)) and cap.reserved == {}
    assert cap.actual == pytest.approx(usd)
    cap = cost.SpendCap(5.0)
    with pytest.raises(llm.ProviderConfigError):
        asyncio.run(go(Script(reply("bad", output_tokens=40), status_error(400)), cap))
    assert cap.actual > 0 and cap.reserved == {}  # the first, billed attempt is recorded
    cap = cost.SpendCap(5.0)
    with pytest.raises(KeyError):  # an unknown failure: the whole reservation is recorded
        asyncio.run(go(Script(KeyError("bug")), cap))
    assert cap.actual == pytest.approx(worst) and cap.reserved == {}


def test_missing_key_is_named_before_any_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.ProviderConfigError, match="ANTHROPIC_API_KEY"):
        llm.make_client(CONFIG)


def test_cli_maps_config_errors_to_2_and_the_cap_to_3(monkeypatch, capsys):
    from fraudreview import cli

    def config_error(_: Any) -> int:
        raise llm.ProviderConfigError("ANTHROPIC_API_KEY is not set (put it in .env)")

    def cap(_: Any) -> int:
        raise cost.CapReached("spend would pass the $5 cap")

    monkeypatch.setattr(cli, "_doctor", config_error)
    assert cli.main(["doctor"]) == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().err
    monkeypatch.setattr(cli, "_doctor", cap)
    assert cli.main(["doctor"]) == 3 and "cached" in capsys.readouterr().err


# ------------------------------------------------------------------ BRK-03 cache


@pytest.mark.contract
def test_cache_key_is_stable_and_sensitive():
    base: dict[str, Any] = dict(
        step="stage1",
        code_version="1",
        inp={"a": 1, "b": [1, 2]},
        model="m",
        effort="medium",
        prompt_hash="p",
        policy_version="v1",
        seed=7,
    )
    k = cache.cache_key(**base)
    assert k == cache.cache_key(**{**base, "inp": {"b": [1, 2], "a": 1}})
    for field, other in [
        ("step", "stage2"),
        ("code_version", "2"),
        ("inp", {"a": 2, "b": [1, 2]}),
        ("model", "n"),
        ("effort", "high"),
        ("prompt_hash", "q"),
        ("policy_version", "v2"),
        ("seed", 8),
    ]:
        assert cache.cache_key(**{**base, field: other}) != k, field


def test_store_first_writer_wins_and_checks_key_and_version(tmp_path):
    store = cache.Store(tmp_path)
    key = "a" * 64
    assert store.get(key, "1") is None
    assert store.put(key, "1", {"answer": 1}) is True
    assert store.put(key, "1", {"answer": 2}) is False
    assert store.get(key, "1") == {"answer": 1}
    assert not list(tmp_path.rglob(".tmp-*"))  # no temporary file left behind
    with pytest.raises(cache.CacheError):
        store.get(key, "2")
    other = "b" * 64
    store.path(other).parent.mkdir(parents=True, exist_ok=True)
    store.path(other).write_text(store.path(key).read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(cache.CacheError):  # an entry filed under the wrong key
        store.get(other, "1")
    store.path("c" * 64).parent.mkdir(parents=True, exist_ok=True)
    store.path("c" * 64).write_text("{trunc", encoding="utf-8")
    with pytest.raises(cache.CacheError):
        store.get("c" * 64, "1")
    with pytest.raises(cache.CacheError):
        store.path("../../etc")


def test_concurrent_writers_leave_exactly_one_entry(tmp_path):
    store = cache.Store(tmp_path)
    key = "d" * 64

    async def write(i: int) -> bool:
        return await asyncio.to_thread(store.put, key, "1", {"i": i})

    async def main() -> list[bool]:
        return list(await asyncio.gather(*(write(i) for i in range(16))))

    wins = asyncio.run(main())
    assert wins.count(True) == 1
    assert store.get(key, "1") == {"i": wins.index(True)}


@pytest.mark.contract
def test_registered_steps_match_their_code_versions(tmp_path):
    steps = {"model_call": (llm.CODE_VERSION, llm)}  # the whole module: helpers and constants count
    assert cache.code_version_problems(steps) == []
    reg = tmp_path / "r.json"
    reg.write_text(
        json.dumps({"model_call": {"code_version": llm.CODE_VERSION, "source_sha256": "0" * 64}}),
        encoding="utf-8",
    )
    assert "bump code_version" in cache.code_version_problems(steps, reg)[0]
    assert "stale" in cache.code_version_problems({"model_call": ("99", llm)}, reg)[0]
    assert "not in" in cache.code_version_problems({"x": ("1", llm)}, reg)[0]


# ------------------------------------------------------------------ BRK-04 cost


def test_call_cost_uses_every_price_and_counts_thinking_as_output():
    haiku = PRICES["claude-haiku-5-5"]
    t = Tokens(input=1000, output=500, cache_read=2000, cache_write=1000)
    want = (1000 * 0.10 + 500 * 0.50 + 2000 * 0.01 + 1000 * 0.125) / 1e6
    assert cost.call_cost(t, haiku) == pytest.approx(want)
    out = run(Script(reply(output_tokens=1200)))  # usage output_tokens includes thinking
    assert out.tokens.output == 1200


# ------------------------------------------------------------------ BRK-05 estimate and cap


def test_estimate_follows_section_9():
    est = cost.estimate_run(500, PRICES, CONFIG)
    s1 = 500 * (700 * 0.10 + 450 * 0.50 + 1500 * 0.01) / 1e6
    s2 = 500 * (700 * 2.0 + 1150 * 10.0 + 1500 * 0.10) / 1e6
    assert est.stage1_usd == pytest.approx(s1) and est.stage2_usd == pytest.approx(s2)
    assert est.stage2_calls == 500
    half = cost.estimate_run(100, PRICES, CONFIG, cost.Assumptions(stage2_share=0.5))
    assert half.stage2_calls == 50
    with pytest.raises(ValueError):
        cost.estimate_run(10, PRICES, CONFIG, cost.Assumptions(stage2_share=1.5))


def test_worst_case_reservation_bounds_any_bill_and_commit_stops_overspend():
    price = PRICES["claude-sonnet-5-5"]
    b = body(2)
    worst = llm.worst_case_cost(b, price)
    chars = len(json.dumps(b))
    # the dearest possible call: two attempts, all input written to cache, full max_tokens
    bill = 2 * cost.call_cost(Tokens(input=0, output=8192, cache_write=chars), price)
    assert worst >= bill > 0

    async def main() -> None:
        cap = cost.SpendCap(1.0)
        t1 = await cap.reserve(0.5)
        with pytest.raises(cost.CapReached):
            await cap.commit(t1, 1.2)  # recorded, then the run stops
        assert cap.actual == pytest.approx(1.2)
        with pytest.raises(cost.CapReached):
            await cap.reserve(0.0001)
        fresh = cost.SpendCap(1.0)
        t2 = await fresh.reserve(0.1)
        for bad in (float("nan"), -0.5, float("inf"), True):
            with pytest.raises(ValueError):
                await fresh.commit(t2, bad)

    asyncio.run(main())


def test_spend_cap_reserves_before_spending_and_cannot_overshoot():
    async def main() -> None:
        cap = cost.SpendCap(1.0)
        a = await cap.reserve(0.6)
        with pytest.raises(cost.CapReached):
            await cap.reserve(0.5)
        await cap.commit(a, 0.55)
        b = await cap.reserve(0.45)
        await cap.release(b)
        with pytest.raises(KeyError):
            await cap.release(b)  # settled once only
        tokens = await asyncio.gather(
            *(cap.reserve(0.1) for _ in range(10)), return_exceptions=True
        )
        granted = [t for t in tokens if isinstance(t, int)]
        assert len(granted) == 4  # 0.55 spent + 4 × 0.1 = 0.95 ≤ 1.0; a fifth would pass
        assert all(isinstance(t, cost.CapReached) for t in tokens if not isinstance(t, int))

    asyncio.run(main())


@pytest.mark.contract
def test_dry_run_prints_estimate_makes_no_call_and_refuses_over_cap(capsys, monkeypatch):
    from fraudreview import cli

    def no_client(*_: Any, **__: Any) -> Any:
        raise AssertionError("dry run built a client")

    from anthropic.lib.bedrock import _mantle

    monkeypatch.setattr(anthropic, "AsyncAnthropic", no_client)
    monkeypatch.setattr(_mantle, "AsyncAnthropicBedrockMantle", no_client)
    base = ["triage", "--config", str(ROOT / "config.yaml"), "--prices", str(ROOT / "prices.yaml")]
    assert cli.main([*base, "--split", "dev", "--alerts", "100", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "100 calls" in out and "estimated total $1.34" in out
    assert cli.main([*base, "--split", "eval", "--alerts", "500", "--dry-run"]) == 2
    assert "over max_cost_usd" in capsys.readouterr().err
    assert cli.main([*base, "--split", "dev", "--alerts", "5"]) == 2  # live arrives in A2.5


# ------------------------------------------------------------------ BRK-06 ledger


def test_ledger_appends_one_line_per_run(tmp_path):
    path = tmp_path / "m" / "spend-ledger.jsonl"
    ledger.append_spend(
        path,
        run_id="r1",
        provider="anthropic",
        split="dev",
        calls=3,
        estimate_usd=1,
        actual_usd=0.5,
    )
    first = path.read_text(encoding="utf-8")
    ledger.append_spend(
        path,
        run_id="r2",
        provider="anthropic",
        split="dev",
        calls=1,
        estimate_usd=1,
        actual_usd=0.25,
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path.read_text(encoding="utf-8").startswith(first) and len(lines) == 2
    ledger.append_spend(
        path, run_id="r3", provider="p", split="s", calls=1, estimate_usd=0, actual_usd=0, note="x"
    )
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[2])["note"] == "x"
    assert set(json.loads(lines[1])) == {
        "at",
        "run_id",
        "provider",
        "split",
        "calls",
        "estimate_usd",
        "actual_usd",
    }
    assert ledger.total_spend(path) == pytest.approx(0.75)
    with pytest.raises(ValueError):
        ledger.append_spend(
            path, run_id="r", provider="p", split="s", calls=1, estimate_usd=1, actual_usd=-1
        )


# ------------------------------------------------------------------ BRK-07 concurrency


def test_bounded_never_exceeds_concurrency_and_keeps_order():
    live = peak = 0

    async def work(i: int) -> int:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.001 * (i % 3))
        live -= 1
        return i * 2

    out = asyncio.run(cost.bounded(range(40), work, 8))
    assert out == [i * 2 for i in range(40)] and peak == 8
    with pytest.raises(ValueError):
        asyncio.run(cost.bounded([1], work, 0))


# ------------------------------------------------------------------ BRK-08 provider switch


def test_one_switch_builds_either_client_with_sdk_retries_off(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    api = llm.make_client(CONFIG)
    assert isinstance(api, anthropic.AsyncAnthropic) and api.max_retries == 0
    from anthropic.lib.bedrock._mantle import AsyncAnthropicBedrockMantle

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "example")
    bedrock_cfg = CONFIG.model_copy(update={"provider": "bedrock"})
    mantle = llm.make_client(bedrock_cfg)
    assert isinstance(mantle, AsyncAnthropicBedrockMantle) and mantle.max_retries == 0


def test_decision_records_provider_and_refuses_the_wrong_stage_output():
    result = run(Script(reply()))
    d = llm.to_decision(
        result,
        decision_id="d-1",
        alert_id="a-0123456789abcdef",
        stage=1,
        config=CONFIG,
        prompt_hash="ph",
        policy_version="v1",
        input_hash="ih",
        cost_usd=cost.call_cost(result.tokens, PRICES["claude-haiku-5-5"]),
    )
    assert d.provider == "anthropic" and d.model == "claude-haiku-5-5" and d.effort == "medium"
    assert d.request_id == "req_1" and d.attempts == 1
    with pytest.raises(ValueError):
        llm.to_decision(
            result,
            decision_id="d-2",
            alert_id="a-0123456789abcdef",
            stage=2,
            config=CONFIG,
            prompt_hash="ph",
            policy_version="v1",
            input_hash="ih",
            cost_usd=0.0,
        )


@pytest.mark.contract
def test_committed_live_schema_check_is_green():
    record = json.loads(
        (ROOT / "docs" / "measurements" / "structured-schemas.json").read_text(encoding="utf-8")
    )
    assert record["provider"] == CONFIG.provider
    for key, cfg, schema in (
        ("stage1", CONFIG.stage1, "Stage1Output"),
        ("stage2", CONFIG.stage2, "Stage2Output"),
    ):
        stage = record["stages"][key]
        assert stage["accepted_and_valid"] and stage["error"] is None
        assert stage["model"] == cfg.model and stage["schema"] == schema
    assert 0 < record["total_cost_usd"] < 0.05
    for key, cfg in (("stage1", CONFIG.stage1), ("stage2", CONFIG.stage2)):
        stage = record["stages"][key]
        want = cost.call_cost(Tokens(**stage["tokens"]), PRICES[cfg.model])
        assert stage["cost_usd"] == pytest.approx(want, abs=1e-6)
    lines = [
        json.loads(x)
        for x in (ROOT / "docs" / "measurements" / "spend-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    mine = [x for x in lines if x["run_id"] == "n1.3-schema-check"]
    assert mine[-1]["actual_usd"] == pytest.approx(record["total_cost_usd"], abs=1e-6)
    assert all(x["estimate_usd"] > 0 or x.get("note") for x in lines)  # no silent $0 estimate
    preflight = json.loads(
        (ROOT / "docs" / "measurements" / "preflight.json").read_text(encoding="utf-8")
    )
    w02 = next(x for x in lines if x["run_id"] == "w0.2-preflight")
    assert w02["actual_usd"] >= preflight["total_cost_usd"] and w02["calls"] == 8
