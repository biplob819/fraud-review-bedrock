"""`fraud doctor`: is this machine and account ready to run? (W0.2, E5, Tech Spec §4.3).

Offline checks need no network: Python, the run config, `prices.yaml`, the Kaggle token (never
printed), and the pinned EvalKit. Online checks ask AWS who you are, whether each configured
model is authorised in the project's Region, and what its quotas are. `--smoke` adds one tiny
call per model (a fraction of a cent, D-33). Every AWS client is injectable for tests.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml

from fraudreview.config import ConfigError, RunConfig, load_config

Status = Literal["ok", "warn", "fail", "skip"]
EVALKIT_VERSION = "0.2.0"  # pinned in pyproject.toml by the v0.2.0 commit (PRE-06)
PRICE_MAX_AGE_DAYS = 90
PRICE_FIELDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def check_python() -> Check:
    ok = sys.version_info >= (3, 12)
    return Check("python", "ok" if ok else "fail", sys.version.split()[0])


def check_config(path: Path) -> tuple[Check, RunConfig | None]:
    try:
        config = load_config(path)
    except ConfigError as exc:
        return Check("config", "fail", str(exc)), None
    detail = (
        f"{config.provider} in {config.region}; stage 1 {config.stage1.model}, stage 2 "
        f"{config.stage2.model}; max ${config.max_cost_usd:g} per run"
    )
    return Check("config", "ok", detail), config


def check_prices(path: Path, config: RunConfig | None, today: date | None = None) -> Check:
    """Every configured model has all prices, for the config's Region, checked recently."""
    today = today or date.today()  # local date; the check date is written locally
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return Check("prices", "fail", f"{path}: {type(exc).__name__}")
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        return Check("prices", "fail", f"{path}: no models table")
    if data.get("unit") != "per_million_tokens" or data.get("currency") != "USD":
        return Check("prices", "fail", f"{path}: expected USD per_million_tokens")
    problems = []
    if config is not None:
        if data.get("region") != config.region:
            problems.append(f"prices are for {data.get('region')}, config is {config.region}")
        for model in (config.stage1.model, config.stage2.model):
            row = data["models"].get(model)
            if not isinstance(row, dict):
                problems.append(f"no prices for {model}")
                continue
            for field in PRICE_FIELDS:
                v = row.get(field)
                if not (isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v < 1000):
                    problems.append(f"{model}.{field} missing or not a price")
    if problems:
        return Check("prices", "fail", "; ".join(problems))
    try:
        checked = date.fromisoformat(str(data.get("checked")))
    except ValueError:
        return Check("prices", "fail", f"{path}: 'checked' is not a date")
    age = (today - checked).days
    if age < -1:  # a day of slack for time zones
        return Check("prices", "fail", f"checked date {checked} is in the future")
    if age > PRICE_MAX_AGE_DAYS:
        return Check("prices", "warn", f"checked {age} days ago; refresh from the rate card")
    return Check("prices", "ok", f"checked {age} days ago")


def check_kaggle(auth: Callable[[], str | None] | None = None) -> Check:
    """A Kaggle credential is present; its value is never shown."""
    from fraudreview.data import DataError, kaggle_auth

    try:
        value = (auth or kaggle_auth)()
    except DataError as exc:
        return Check("kaggle", "fail", str(exc))
    if value is None:
        return Check("kaggle", "warn", "no token (only needed to download the data again)")
    kind = "token" if value.startswith("Bearer ") else "kaggle.json"
    return Check("kaggle", "ok", f"{kind} present (not shown)")


def check_evalkit() -> Check:
    try:
        import evalkit
        from evalkit.scorers.faithfulness import FaithfulnessItem, faithfulness  # noqa: F401
    except ImportError as exc:
        return Check("evalkit", "fail", f"not importable ({exc.name})")
    version = getattr(evalkit, "__version__", "?")
    if version != EVALKIT_VERSION:
        return Check("evalkit", "fail", f"version {version}, pinned {EVALKIT_VERSION}")
    return Check("evalkit", "ok", f"{version}; faithfulness scorer imports")


# ---------------------------------------------------------------- online (AWS)


def check_identity(sts: Any) -> Check:
    try:
        who = sts.get_caller_identity()
    except Exception as exc:  # any auth or network failure: name it, show no secrets
        return Check("aws identity", "fail", f"{type(exc).__name__}; run `aws login`")
    account = str(who.get("Account", ""))
    return Check("aws identity", "ok", f"account ****{account[-4:]}")


def check_model_access(bedrock: Any, model: str) -> Check:
    name = f"access {model}"
    try:
        r = bedrock.get_foundation_model_availability(modelId=model)
    except Exception as exc:
        return Check(name, "fail", type(exc).__name__)
    auth = r.get("authorizationStatus")
    region = r.get("regionAvailability")
    if auth == "AUTHORIZED" and region == "AVAILABLE":
        return Check(name, "ok", "authorised in this Region")
    return Check(
        name,
        "fail",
        f"authorization {auth}, region {region}, agreement "
        f"{(r.get('agreementAvailability') or {}).get('status')}",
    )


def check_quotas(quotas: Any, display: str) -> Check:
    """Tokens and requests per minute for the model; 0 means it cannot be called."""
    name = f"quotas {display}"
    try:
        found: list[tuple[str, float]] = []
        paginator = quotas.get_paginator("list_service_quotas")
        for page in paginator.paginate(ServiceCode="bedrock"):
            for q in page.get("Quotas", []):
                if display in q.get("QuotaName", ""):
                    found.append((q["QuotaName"], float(q.get("Value", 0))))
    except Exception as exc:
        return Check(name, "fail", type(exc).__name__)
    if not found:
        return Check(name, "warn", "no quota listed for this model")
    usable = [(n, v) for n, v in found if v > 0]
    detail = "; ".join(f"{n}: {v:g}" for n, v in sorted(found))
    return Check(name, "ok" if usable else "fail", detail)


def check_smoke(client: Any, model: str, prices: dict[str, Any] | None) -> Check:
    """One tiny call; reports tokens and its cost from prices.yaml."""
    name = f"smoke {model}"
    try:
        m = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except Exception as exc:
        return Check(name, "fail", type(exc).__name__)
    used_in, used_out = m.usage.input_tokens, m.usage.output_tokens
    cost = ""
    if prices and model in prices:
        usd = (used_in * prices[model]["input"] + used_out * prices[model]["output"]) / 1e6
        cost = f", about ${usd:.6f}"
    return Check(name, "ok", f"{used_in} in / {used_out} out tokens{cost}")


DISPLAY = {
    "anthropic.claude-haiku-5-5": "Claude Haiku 5.5",
    "anthropic.claude-sonnet-5-5": "Claude Sonnet 5.5",
}


def run_checks(
    config_path: Path,
    prices_path: Path,
    *,
    offline: bool,
    smoke: bool = False,
    aws: Callable[[str, str], Any] | None = None,
    mantle: Callable[[str], Any] | None = None,
    kaggle: Callable[[], str | None] | None = None,
) -> list[Check]:
    """All checks in order. `aws(service, region)` and `mantle(region)` build clients."""
    checks = [check_python()]
    cfg_check, config = check_config(config_path)
    checks.append(cfg_check)
    checks.append(check_prices(prices_path, config))
    checks.append(check_kaggle(kaggle))
    checks.append(check_evalkit())
    if offline or config is None:
        checks.append(Check("aws", "skip", "offline" if offline else "no valid config"))
        return checks
    if config.provider != "bedrock":
        checks.append(Check("aws", "skip", f"provider is {config.provider}"))
        return checks
    make = aws or _boto3_client
    checks.append(check_identity(make("sts", config.region)))
    bedrock = make("bedrock", config.region)
    quotas = make("service-quotas", config.region)
    for model in (config.stage1.model, config.stage2.model):
        checks.append(check_model_access(bedrock, model))
        checks.append(check_quotas(quotas, DISPLAY.get(model, model)))
    if smoke:
        data = yaml.safe_load(prices_path.read_text(encoding="utf-8")) or {}
        client = (mantle or _mantle_client)(config.region)
        for model in (config.stage1.model, config.stage2.model):
            checks.append(check_smoke(client, model, data.get("models")))
    return checks


def _boto3_client(service: str, region: str) -> Any:
    import boto3

    return boto3.client(service, region_name=region)


def _mantle_client(region: str) -> Any:
    from anthropic.lib.bedrock._mantle import AnthropicBedrockMantle

    return AnthropicBedrockMantle(aws_region=region)


def exit_code(checks: list[Check]) -> int:
    """2 for an invalid config, 1 for any other failed check, 0 otherwise (warnings pass)."""
    if any(c.name == "config" and c.status == "fail" for c in checks):
        return 2
    return 1 if any(c.status == "fail" for c in checks) else 0
