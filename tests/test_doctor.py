"""W0.2: `fraud doctor` with injected AWS clients (no AWS in tests), prices, EvalKit pin."""

from __future__ import annotations

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
HAIKU, SONNET = "anthropic.claude-haiku-5-5", "anthropic.claude-sonnet-5-5"


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
    def __init__(self) -> None:
        self.models: list[str] = []
        self.messages = self

    def create(self, **kw: Any) -> Any:
        self.models.append(kw["model"])
        assert kw["max_tokens"] <= 16
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=14, output_tokens=3))


def run(tmp_path: Path, **kw: Any) -> list[d.Check]:
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
    assert mantle.models == [HAIKU, SONNET]
    assert checks[f"smoke {HAIKU}"].status == "ok"
    want = (14 * 0.11 + 3 * 0.55) / 1e6
    assert f"${want:.6f}" in checks[f"smoke {HAIKU}"].detail
    no_smoke = FakeMantle()
    run(tmp_path, offline=False, aws=FakeAWS(), mantle=lambda r: no_smoke)
    assert no_smoke.models == []


# ------------------------------------------------------------------ PRE-04 prices


@pytest.mark.contract
def test_prices_cover_every_configured_model_in_the_config_region():
    config = load_config(ROOT / "config.yaml")
    data = yaml.safe_load((ROOT / "prices.yaml").read_text(encoding="utf-8"))
    assert data["region"] == config.region == "us-east-2"
    assert data["unit"] == "per_million_tokens" and data["currency"] == "USD"
    for model in (config.stage1.model, config.stage2.model):
        assert set(d.PRICE_FIELDS) <= set(data["models"][model])
    assert d.check_prices(ROOT / "prices.yaml", config).status == "ok"
    haiku = data["models"][HAIKU]
    assert haiku["input"] == pytest.approx(0.11) and haiku["output"] == pytest.approx(0.55)


def test_price_problems_fail_and_old_prices_warn(tmp_path):
    config = load_config(ROOT / "config.yaml")
    base = yaml.safe_load((ROOT / "prices.yaml").read_text(encoding="utf-8"))

    def check(edit, today=None):
        data = yaml.safe_load(yaml.safe_dump(base))
        edit(data)
        path = tmp_path / "p.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return d.check_prices(path, config, today=today)

    assert check(lambda x: x.update(region="us-east-1")).status == "fail"
    assert check(lambda x: x["models"].pop(SONNET)).status == "fail"
    assert check(lambda x: x["models"][HAIKU].update(input=-1)).status == "fail"
    assert check(lambda x: x["models"][HAIKU].update(input="cheap")).status == "fail"
    assert check(lambda x: x.update(unit="per_token")).status == "fail"
    checked = date.fromisoformat(base["checked"])
    assert check(lambda x: None, today=checked + timedelta(days=91)).status == "warn"
    assert check(lambda x: None, today=checked - timedelta(days=5)).status == "fail"
    assert check(lambda x: None, today=checked + timedelta(days=10)).status == "ok"


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
    assert by_name(checks)["aws"].status == "skip"
    assert d.exit_code(checks) == 0


@pytest.mark.safety
def test_no_output_shows_the_account_or_a_secret(tmp_path):
    for aws in (FakeAWS(), FakeAWS(fail="sts")):
        text = "\n".join(f"{c.name} {c.detail}" for c in run(tmp_path, offline=False, aws=aws))
        assert "586752323883" not in text and "KGAT_secret" not in text
        assert "secret-detail" not in text
    assert "****3883" in by_name(run(tmp_path, offline=False, aws=FakeAWS()))["aws identity"].detail


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
