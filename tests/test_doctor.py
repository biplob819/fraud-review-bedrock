"""W0.2: `fraud doctor` with injected AWS and Claude API clients (no network in tests), prices,
the EvalKit pin and the `.env` loader."""

from __future__ import annotations

import json
import re
import tomllib
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from fraudreview import doctor as d
from fraudreview.config import load_config

ROOT = Path(__file__).resolve().parent.parent
HAIKU, SONNET = "anthropic.claude-haiku-5-5", "anthropic.claude-sonnet-5-5"  # Bedrock ids
API_HAIKU, API_SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"  # Claude API ids (D-35)
KEY_ENV = {"ANTHROPIC_API_KEY": "sk-ant-supersecret"}


def bedrock_config(tmp_path: Path) -> Path:
    """The committed config switched to provider bedrock with the Bedrock model ids."""
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    text = text.replace("provider: anthropic", "provider: bedrock")
    text = text.replace("model: claude-", "model: anthropic.claude-")
    path = tmp_path / "bedrock.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class FakeAWS:
    """Stands in for boto3 clients; records every call."""

    def __init__(self, authorised: bool = True, quota: float = 200_000, fail: str = ""):
        self.authorised, self.quota, self.fail = authorised, quota, fail
        self.calls: list[tuple[str, str]] = []

    def __call__(self, service: str, region: str) -> Any:
        self.calls.append((service, region))
        return self

    def get_caller_identity(self) -> dict[str, str]:
        if self.fail == "sts":
            raise RuntimeError("ExpiredToken secret-detail")
        return {"Account": "586752323883", "Arn": "arn:aws:sts::586752323883:assumed-role/x"}

    def get_foundation_model_availability(self, modelId: str) -> dict[str, Any]:
        return {
            "modelId": modelId,
            "authorizationStatus": "AUTHORIZED" if self.authorised else "NOT_AUTHORIZED",
            "regionAvailability": "AVAILABLE",
            "agreementAvailability": {
                "status": "AVAILABLE" if self.authorised else "NOT_AVAILABLE"
            },
        }

    def get_paginator(self, name: str) -> Any:
        quota = self.quota

        class P:
            def paginate(self, **_: Any):
                yield {
                    "Quotas": [
                        {
                            "QuotaName": f"On-demand tokens per minute for Anthropic {n}",
                            "Value": quota,
                        }
                        for n in ("Claude Haiku 5.5", "Claude Sonnet 5.5")
                    ]
                }

        return P()


class FakeMantle:
    """Stands in for the Mantle or first-party client (same Messages shape, D-27)."""

    def __init__(self, listed: tuple[str, ...] = (API_HAIKU, API_SONNET), fail: bool = False):
        self.called: list[str] = []
        self.messages = self.models = self  # client.messages.create, client.models.list
        self.listed, self.fail, self.listings = listed, fail, 0

    def list(self, **_: Any) -> list[Any]:  # client.models.list
        self.listings += 1
        if self.fail:
            raise RuntimeError("AuthenticationError sk-ant-supersecret")
        return [SimpleNamespace(id=m) for m in self.listed]

    def create(self, **kw: Any) -> Any:
        self.called.append(kw["model"])
        assert kw["max_tokens"] <= 16
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=14, output_tokens=3))


def run(tmp_path: Path, **kw: Any) -> list[d.Check]:
    """Bedrock provider (the AWS path)."""
    return d.run_checks(
        bedrock_config(tmp_path),
        ROOT / "prices.yaml",
        kaggle=lambda: "Bearer KGAT_secret",
        **kw,
    )


def run_api(tmp_path: Path, **kw: Any) -> list[d.Check]:
    """The committed config: provider anthropic (D-35)."""
    kw.setdefault("env", KEY_ENV)
    return d.run_checks(
        ROOT / "config.yaml", ROOT / "prices.yaml", kaggle=lambda: "Bearer KGAT_secret", **kw
    )


def by_name(checks: list[d.Check]) -> dict[str, d.Check]:
    return {c.name: c for c in checks}


# ------------------------------------------------------------------ PRE-01 / PRE-02 (logic)


def test_access_and_quotas_pass_only_when_authorised_with_quota(tmp_path):
    good = by_name(run(tmp_path, offline=False, aws=FakeAWS()))
    for model in (HAIKU, SONNET):
        assert good[f"access {model}"].status == "ok"
    assert good["quotas Claude Haiku 5.5"].status == "ok"

    class NoQuotas(FakeAWS):
        def get_paginator(self, name: str) -> Any:
            return SimpleNamespace(paginate=lambda **_: iter([{"Quotas": []}]))

    unlisted = by_name(run(tmp_path, offline=False, aws=NoQuotas()))
    assert unlisted["quotas Claude Haiku 5.5"].status == "fail"
    bad = run(tmp_path, offline=False, aws=FakeAWS(authorised=False, quota=0))
    names = by_name(bad)
    assert names[f"access {HAIKU}"].status == "fail"
    assert "NOT_AUTHORIZED" in names[f"access {HAIKU}"].detail
    assert names["quotas Claude Sonnet 5.5"].status == "fail"
    assert d.exit_code(bad) == 1


# ------------------------------------------------------------------ PRE-03 (logic)


def test_smoke_calls_each_model_once_and_prices_the_call(tmp_path):
    mantle = FakeMantle()
    checks = by_name(
        run(tmp_path, offline=False, smoke=True, aws=FakeAWS(), mantle=lambda r: mantle)
    )
    assert mantle.called == [HAIKU, SONNET]
    assert checks[f"smoke {HAIKU}"].status == "ok"
    want = (14 * 0.11 + 3 * 0.55) / 1e6
    assert f"${want:.6f}" in checks[f"smoke {HAIKU}"].detail
    no_smoke = FakeMantle()
    run(tmp_path, offline=False, aws=FakeAWS(), mantle=lambda r: no_smoke)
    assert no_smoke.called == []


# ------------------------------------------------------------------ PRE-04 prices


@pytest.mark.contract
def test_prices_cover_every_configured_model_for_both_providers(tmp_path):
    data = yaml.safe_load((ROOT / "prices.yaml").read_text(encoding="utf-8"))
    assert data["unit"] == "per_million_tokens" and data["currency"] == "USD"
    for path in (ROOT / "config.yaml", bedrock_config(tmp_path)):
        config = load_config(path)
        table = data["providers"][config.provider]["models"]
        for model in (config.stage1.model, config.stage2.model):
            assert set(d.PRICE_FIELDS) <= set(table[model])
        assert d.check_prices(ROOT / "prices.yaml", config).status == "ok"
    assert data["providers"]["bedrock"]["region"] == "us-east-2"
    api = data["providers"]["anthropic"]["models"][API_HAIKU]
    assert api["input"] == pytest.approx(0.10) and api["output"] == pytest.approx(0.50)
    aws = data["providers"]["bedrock"]["models"][HAIKU]
    assert aws["input"] == pytest.approx(0.11) and aws["output"] == pytest.approx(0.55)


def test_price_problems_fail_and_old_prices_warn(tmp_path):
    config = load_config(bedrock_config(tmp_path))
    base = yaml.safe_load((ROOT / "prices.yaml").read_text(encoding="utf-8"))

    def check(edit, today=None):
        data = yaml.safe_load(yaml.safe_dump(base))
        edit(data)
        path = tmp_path / "p.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return d.check_prices(path, config, today=today)

    aws = lambda x: x["providers"]["bedrock"]  # noqa: E731
    assert check(lambda x: aws(x).update(region="us-east-1")).status == "fail"
    assert check(lambda x: aws(x)["models"].pop(SONNET)).status == "fail"
    assert check(lambda x: aws(x)["models"][HAIKU].update(input=-1)).status == "fail"
    assert check(lambda x: aws(x)["models"][HAIKU].update(input="cheap")).status == "fail"
    assert check(lambda x: x["providers"].pop("bedrock")).status == "fail"
    assert check(lambda x: x.update(unit="per_token")).status == "fail"
    checked = date.fromisoformat(base["checked"])
    assert check(lambda x: None, today=checked + timedelta(days=91)).status == "warn"
    assert check(lambda x: None, today=checked - timedelta(days=5)).status == "fail"
    assert check(lambda x: None, today=checked + timedelta(days=10)).status == "ok"
    # the Claude API table has no Region to match
    api = load_config(ROOT / "config.yaml")
    assert d.check_prices(ROOT / "prices.yaml", api).status == "ok"


# ------------------------------------------------------------------ D-35 Claude API provider


def test_api_provider_lists_models_and_smokes_without_aws(tmp_path):
    aws, client = FakeAWS(), FakeMantle()
    checks = by_name(
        run_api(tmp_path, offline=False, smoke=True, aws=aws, anthropic=lambda: client)
    )
    assert aws.calls == [] and checks["aws"].status == "skip"
    assert checks["anthropic key"].status == "ok"
    for model in (API_HAIKU, API_SONNET):
        assert checks[f"access {model}"].status == "ok"
    assert client.called == [API_HAIKU, API_SONNET] and client.listings == 1
    want = (14 * 0.10 + 3 * 0.50) / 1e6
    assert f"${want:.6f}" in checks[f"smoke {API_HAIKU}"].detail
    quiet = FakeMantle()
    run_api(tmp_path, offline=False, anthropic=lambda: quiet)
    assert quiet.called == []


def test_api_provider_fails_without_key_or_listing(tmp_path):
    called = []
    no_key = by_name(run_api(tmp_path, offline=False, env={}, anthropic=lambda: called.append(1)))
    assert no_key["anthropic key"].status == "fail" and called == []
    bad = by_name(run_api(tmp_path, offline=False, env={"ANTHROPIC_API_KEY": "a b"}))
    assert bad["anthropic key"].status == "fail"
    missing = run_api(tmp_path, offline=False, anthropic=lambda: FakeMantle(listed=(API_HAIKU,)))
    assert by_name(missing)[f"access {API_SONNET}"].status == "fail"
    assert d.exit_code(missing) == 1
    broken = run_api(tmp_path, offline=False, anthropic=lambda: FakeMantle(fail=True))
    text = "\n".join(f"{c.name} {c.detail}" for c in broken)
    assert "supersecret" not in text and by_name(broken)[f"access {API_HAIKU}"].status == "fail"


@pytest.mark.safety
def test_env_file_fills_only_unset_names_and_never_prints(tmp_path, capsys):
    from fraudreview.cli import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY='sk-ant-supersecret'\nexport KAGGLE_API_TOKEN=KGAT_x\n# note\n"
        "lower=skip\n",
        encoding="utf-8",
    )
    environ = {"KAGGLE_API_TOKEN": "already"}
    assert load_env_file(env_file, environ) == ["ANTHROPIC_API_KEY"]
    assert environ == {"KAGGLE_API_TOKEN": "already", "ANTHROPIC_API_KEY": "sk-ant-supersecret"}
    assert load_env_file(tmp_path / "missing", environ) == []
    assert "supersecret" not in capsys.readouterr().out
    # the committed template, copied as is, sets nothing (empty values count as unset)
    assert load_env_file(ROOT / ".env.example", {}) == []
    tricky = tmp_path / "tricky.env"
    tricky.write_text(
        "ANTHROPIC_API_KEY = sk-ant-x   # my key\nKAGGLE_API_TOKEN=   # empty\n"
        "OPENAI_API_KEY='a # b'\n",
        encoding="utf-8",
    )
    env: dict[str, str] = {}
    assert load_env_file(tricky, env) == ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    assert env == {"ANTHROPIC_API_KEY": "sk-ant-x", "OPENAI_API_KEY": "a # b"}
    odd = tmp_path / "odd.env"
    odd.write_text('\ufeffANTHROPIC_API_KEY="sk-ant-q" # quoted then a comment\n', encoding="utf-8")
    blank = {"ANTHROPIC_API_KEY": ""}  # exported but empty counts as unset
    assert load_env_file(odd, blank) == ["ANTHROPIC_API_KEY"]
    assert blank == {"ANTHROPIC_API_KEY": "sk-ant-q"}


# ------------------------------------------------------------------ PRE-05 Kaggle


@pytest.mark.safety
def test_kaggle_token_is_reported_but_never_shown():
    from fraudreview.data import DataError

    ok = d.check_kaggle(lambda: "Bearer KGAT_supersecret")
    assert ok.status == "ok" and "supersecret" not in ok.detail
    assert d.check_kaggle(lambda: None).status == "warn"

    def broken() -> str | None:
        raise DataError("the Kaggle token holds spaces, line breaks or other characters")

    assert d.check_kaggle(broken).status == "fail"
    from fraudreview.data import kaggle_auth

    real = d.check_kaggle(lambda: kaggle_auth(env={"KAGGLE_API_TOKEN": "bad secret"}))
    assert real.status == "fail" and "secret" not in real.detail


# ------------------------------------------------------------------ PRE-06 EvalKit pin


@pytest.mark.contract
def test_evalkit_is_pinned_to_an_exact_commit_and_imports():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    pin = next(x for x in deps if x.startswith("evalkit-harness"))  # exact in every installer
    assert re.fullmatch(
        r"evalkit-harness @ git\+https://github\.com/biplob819/evalkit@[0-9a-f]{40}", pin
    )
    check = d.check_evalkit()
    assert check.status == "ok" and d.EVALKIT_VERSION in check.detail


# ------------------------------------------------------------------ PRE-07 doctor


def test_offline_makes_no_aws_call(tmp_path):
    aws = FakeAWS()
    checks = run(tmp_path, offline=True, aws=aws)
    assert aws.calls == []
    assert by_name(checks)["online"].status == "skip"
    assert d.exit_code(checks) == 0

    def no_client() -> Any:
        raise AssertionError("offline built a Claude API client")

    api = run_api(tmp_path, offline=True, anthropic=no_client)
    assert by_name(api)["online"].status == "skip" and d.exit_code(api) == 0


@pytest.mark.safety
def test_no_output_shows_the_account_or_a_secret(tmp_path, capsys):
    for aws in (FakeAWS(), FakeAWS(fail="sts")):
        text = "\n".join(f"{c.name} {c.detail}" for c in run(tmp_path, offline=False, aws=aws))
        assert "586752323883" not in text and "KGAT_secret" not in text
        assert "secret-detail" not in text
    assert "****3883" in by_name(run(tmp_path, offline=False, aws=FakeAWS()))["aws identity"].detail
    for client in (FakeMantle(), FakeMantle(fail=True)):
        checks = run_api(tmp_path, offline=False, smoke=True, anthropic=lambda c=client: c)
        text = "\n".join(f"{c.name} {c.detail}" for c in checks)
        assert "supersecret" not in text and "KGAT_secret" not in text
    test_env_file_fills_only_unset_names_and_never_prints(tmp_path, capsys)  # `.env` clause


def test_exit_codes_and_regions(tmp_path):
    aws = FakeAWS()
    run(tmp_path, offline=False, aws=aws)
    assert {r for _, r in aws.calls} == {"us-east-2"}
    bad = tmp_path / "c.yaml"
    bad.write_text("provider: [", encoding="utf-8")
    checks = d.run_checks(bad, ROOT / "prices.yaml", offline=False, aws=aws, kaggle=lambda: None)
    assert d.exit_code(checks) == 2
    assert d.exit_code([d.Check("x", "warn", "")]) == 0
    assert d.exit_code([d.Check("x", "fail", "")]) == 1


def test_cli_doctor_offline(capsys):
    from fraudreview import cli

    code = cli.main(
        [
            "doctor",
            "--offline",
            "--config",
            str(ROOT / "config.yaml"),
            "--prices",
            str(ROOT / "prices.yaml"),
        ]
    )
    out = capsys.readouterr().out
    assert code in (0, 1) and "config" in out and "SKIP" in out


# ------------------------------------------------------------------ PRE-01..03 live record (logic)


class FakeRecorder:
    """A Claude API client for record_preflight: structured replies, cache reads, headers."""

    def __init__(self, reply: str = '{"ready": true, "model_family": "Claude"}', cache=True):
        self.reply, self.cache, self.seen = reply, cache, set()
        self.messages = self
        self.with_raw_response = self
        self.requests: list[dict[str, Any]] = []

    def create(self, **kw: Any) -> Any:
        self.requests.append(kw)
        if "output_config" in kw and "format" in kw["output_config"]:
            msg = SimpleNamespace(
                content=[SimpleNamespace(type="text", text=self.reply)],
                usage=SimpleNamespace(input_tokens=200, output_tokens=20),
            )
            headers = {"anthropic-ratelimit-requests-limit": "50"}
            return SimpleNamespace(parse=lambda: msg, headers=headers)
        key = (kw["model"], kw["system"][0]["text"])
        hit = self.cache and key in self.seen
        self.seen.add(key)
        usage = SimpleNamespace(
            input_tokens=16,
            output_tokens=4,
            cache_read_input_tokens=1000 if hit else 0,
            cache_creation_input_tokens=0 if hit else 1000,
        )
        return SimpleNamespace(content=[], usage=usage)


def test_record_preflight_proves_schema_cache_limits_and_cost():
    config = load_config(ROOT / "config.yaml")
    prices = d.price_table(ROOT / "prices.yaml", "anthropic")
    client = FakeRecorder()
    report = d.record_preflight(client, config, prices, "policy " * 600)
    assert set(report["models"]) == {API_HAIKU, API_SONNET}
    haiku = report["models"][API_HAIKU]
    assert haiku["structured_reply_valid"] and haiku["cache"]["second_read_cache"]
    assert haiku["rate_limits"]["requests-limit"] == 50
    want = 200 * 0.10 + 20 * 0.50 + 2 * 16 * 0.10 + 2 * 4 * 0.50 + 1000 * 0.125 + 1000 * 0.01
    assert haiku["cost_usd"] == pytest.approx(want / 1e6, abs=1e-6)
    assert report["requests_per_minute_limit"] == 50 and report["max_concurrency"] == 8
    assert report["config_concurrency"] <= report["max_concurrency"]
    text = json.dumps(report)
    assert "sk-ant" not in text and "request-id" not in text
    for bad in ('{"ready": "yes", "model_family": "C"}', '{"ready": true, "model_family": 5}'):
        with pytest.raises(ValueError):
            d.record_preflight(FakeRecorder(reply=bad), config, prices, "x")
    cold = d.record_preflight(FakeRecorder(cache=False), config, prices, "x")
    assert not cold["models"][API_HAIKU]["cache"]["second_read_cache"]


def test_cli_record_writes_proof_and_refuses_quietly_bad_paths(tmp_path, capsys):
    import argparse

    from fraudreview import cli

    out = tmp_path / "m" / "preflight.json"
    args = argparse.Namespace(
        config=ROOT / "config.yaml", prices=ROOT / "prices.yaml", record=out, offline=False
    )
    assert cli._record(args, client_factory=FakeRecorder) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["provider"] == "anthropic"

    class Boom(FakeRecorder):
        def create(self, **kw: Any) -> Any:
            raise RuntimeError("sk-ant-supersecret")

    assert cli._record(args, client_factory=Boom) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "supersecret" not in err
    args.config = bedrock_config(tmp_path)
    assert cli._record(args, client_factory=FakeRecorder) == 1
    code = cli.main(
        ["doctor", "--offline", "--record", str(out), "--config", str(ROOT / "config.yaml")]
    )
    assert code == 2 and "--offline" in capsys.readouterr().err


def test_client_build_failure_is_a_failed_check(tmp_path):
    def broken() -> Any:
        raise ValueError("bad base url")

    checks = by_name(run_api(tmp_path, offline=False, anthropic=broken))
    assert checks["anthropic client"].status == "fail"


def test_max_concurrency_follows_requests_per_minute():
    assert d.max_concurrency(10_000) == 64
    assert d.max_concurrency(48) == 8 and d.max_concurrency(1) == 1


@pytest.mark.contract
def test_committed_preflight_record_is_green():
    record = json.loads(
        (ROOT / "docs" / "measurements" / "preflight.json").read_text(encoding="utf-8")
    )
    config = load_config(ROOT / "config.yaml")
    assert record["provider"] == config.provider
    assert set(record["models"]) == {config.stage1.model, config.stage2.model}
    for m in record["models"].values():
        assert m["structured_reply_valid"] and m["cache"]["second_read_cache"]
        assert m["rate_limits"]["requests-limit"] > 0
    assert config.concurrency <= record["max_concurrency"]
    assert 0 < record["total_cost_usd"] < 0.05
