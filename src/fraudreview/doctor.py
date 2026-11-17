"""`fraud doctor`: is this machine and account ready to run? (W0.2, E5, Tech Spec §4.3).

Offline checks need no network: Python, the run config, `prices.yaml`, the Kaggle token (never
printed), and the pinned EvalKit. Online checks depend on the provider (D-27). For `anthropic`
(D-35) they check the API key is set (never printed) and that a free model listing shows each
configured model. For `bedrock` they ask AWS who you are, whether each model is authorised in the
project's Region, and what its quotas are. `--smoke` adds one tiny call per model (a fraction of
a cent, D-33). Every client is injectable for tests.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml

from fraudreview.config import ConfigError, RunConfig, load_config
from fraudreview.router import guard_request

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
    where = f" in {config.region}" if config.provider == "bedrock" else ""
    detail = (
        f"{config.provider}{where}; stage 1 {config.stage1.model}, stage 2 "
        f"{config.stage2.model}; max ${config.max_cost_usd:g} per run"
    )
    return Check("config", "ok", detail), config


def check_prices(path: Path, config: RunConfig | None, today: date | None = None) -> Check:
    """Every configured model has all prices in the config provider's table (and, for Bedrock,
    for the config's Region), checked recently."""
    today = today or date.today()  # local date; the check date is written locally
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return Check("prices", "fail", f"{path}: {type(exc).__name__}")
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return Check("prices", "fail", f"{path}: no providers table")
    if data.get("unit") != "per_million_tokens" or data.get("currency") != "USD":
        return Check("prices", "fail", f"{path}: expected USD per_million_tokens")
    problems = []
    if config is not None:
        table = data["providers"].get(config.provider)
        if not isinstance(table, dict) or not isinstance(table.get("models"), dict):
            return Check("prices", "fail", f"{path}: no prices for provider {config.provider}")
        if config.provider == "bedrock" and table.get("region") != config.region:
            problems.append(f"prices are for {table.get('region')}, config is {config.region}")
        for model in (config.stage1.model, config.stage2.model):
            row = table["models"].get(model)
            if not isinstance(row, dict):
                problems.append(f"no {config.provider} prices for {model}")
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


def price_table(path: Path, provider: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ((data.get("providers") or {}).get(provider) or {}).get("models") or {}


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


# ---------------------------------------------------------------- online (Bedrock and Claude API)


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
        return Check(name, "fail", "no quota listed for this model")
    usable = [(n, v) for n, v in found if v > 0]
    detail = "; ".join(f"{n}: {v:g}" for n, v in sorted(found))
    return Check(name, "ok" if usable else "fail", detail)


def check_smoke(client: Any, model: str, prices: dict[str, Any] | None) -> Check:
    """One tiny call; reports tokens and its cost from prices.yaml."""
    name = f"smoke {model}"
    body = {
        "model": model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
    }
    try:
        m = client.messages.create(**guard_request(body))
    except Exception as exc:
        return Check(name, "fail", type(exc).__name__)
    used_in = getattr(getattr(m, "usage", None), "input_tokens", None)
    used_out = getattr(getattr(m, "usage", None), "output_tokens", None)
    if not (isinstance(used_in, int) and isinstance(used_out, int)):
        return Check(name, "warn", "replied, but usage was unreadable")
    cost = ""
    if prices and model in prices:
        usd = (used_in * prices[model]["input"] + used_out * prices[model]["output"]) / 1e6
        cost = f", about ${usd:.6f}"
    return Check(name, "ok", f"{used_in} in / {used_out} out tokens{cost}")


def check_api_key(env: Mapping[str, str]) -> Check:
    """The Claude API key is set; its value is never shown."""
    key = env.get("ANTHROPIC_API_KEY", "")
    if not key:
        return Check("anthropic key", "fail", "ANTHROPIC_API_KEY is not set (see .env.example)")
    if not key.isascii() or not key.isprintable() or " " in key:
        return Check("anthropic key", "fail", "ANTHROPIC_API_KEY holds spaces or hidden characters")
    return Check("anthropic key", "ok", "present (not shown)")


def check_model_listed(client: Any, models: tuple[str, ...]) -> list[Check]:
    """A free model listing: each configured model must be available to this key."""
    try:
        ids = {m.id for m in client.models.list(limit=1000)}
    except Exception as exc:  # auth or network failure: name it, show no secrets
        return [Check(f"access {m}", "fail", type(exc).__name__) for m in models]
    return [
        Check(f"access {m}", "ok", "listed for this key")
        if m in ids
        else Check(f"access {m}", "fail", "not listed for this key")
        for m in models
    ]


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
    anthropic: Callable[[], Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[Check]:
    """All checks in order. `aws(service, region)`, `mantle(region)` and `anthropic()` build
    clients; `env` stands in for the environment (the Claude API key)."""
    checks = [check_python()]
    cfg_check, config = check_config(config_path)
    checks.append(cfg_check)
    checks.append(check_prices(prices_path, config))
    checks.append(check_kaggle(kaggle))
    checks.append(check_evalkit())
    if offline or config is None:
        checks.append(Check("online", "skip", "offline" if offline else "no valid config"))
        return checks
    models = (config.stage1.model, config.stage2.model)
    if config.provider == "anthropic":
        checks.append(Check("aws", "skip", "provider is anthropic; no AWS call"))
        key = check_api_key(os.environ if env is None else env)
        checks.append(key)
        if key.status != "ok":
            return checks
        try:
            client = (anthropic or _anthropic_client)()
        except Exception as exc:  # e.g. a bad base URL or proxy setting; no secret in the type
            checks.append(Check("anthropic client", "fail", type(exc).__name__))
            return checks
        checks.extend(check_model_listed(client, models))
        if smoke:
            prices = price_table(prices_path, "anthropic")
            checks.extend(check_smoke(client, m, prices) for m in models)
        return checks
    make = aws or _boto3_client
    checks.append(check_identity(make("sts", config.region)))
    bedrock = make("bedrock", config.region)
    quotas = make("service-quotas", config.region)
    for model in (config.stage1.model, config.stage2.model):
        checks.append(check_model_access(bedrock, model))
        checks.append(check_quotas(quotas, DISPLAY.get(model, model)))
    if smoke:
        prices = price_table(prices_path, "bedrock")
        client = (mantle or _mantle_client)(config.region)
        checks.extend(check_smoke(client, m, prices) for m in models)
    return checks


def _boto3_client(service: str, region: str) -> Any:
    import boto3

    return boto3.client(service, region_name=region)


def _anthropic_client() -> Any:
    import anthropic

    return anthropic.Anthropic(max_retries=0)


def _mantle_client(region: str) -> Any:
    from anthropic.lib.bedrock._mantle import AnthropicBedrockMantle

    return AnthropicBedrockMantle(aws_region=region, max_retries=0)


# ---------------------------------------------------------------- preflight record (PRE-01..03)

READY_SCHEMA = {
    "type": "object",
    "properties": {"ready": {"type": "boolean"}, "model_family": {"type": "string"}},
    "required": ["ready", "model_family"],
    "additionalProperties": False,
}
LIMIT_HEADERS = (
    "anthropic-ratelimit-requests-limit",
    "anthropic-ratelimit-input-tokens-limit",
    "anthropic-ratelimit-output-tokens-limit",
)
SECONDS_PER_CALL = 10  # a worker makes at most about 6 calls a minute (PRE-02)


def _usage(u: Any) -> dict[str, int]:
    return {
        k: int(getattr(u, k, 0) or 0)
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    }


def usage_cost(usage: dict[str, int], price: dict[str, float]) -> float:
    """USD for one call; cache writes priced at the 5-minute rate (the default TTL)."""
    return (
        usage["input_tokens"] * price["input"]
        + usage["output_tokens"] * price["output"]
        + usage["cache_read_input_tokens"] * price["cache_read"]
        + usage["cache_creation_input_tokens"] * price["cache_write_5m"]
    ) / 1e6


def structured_call(client: Any, model: str) -> tuple[dict[str, Any], dict[str, int], dict]:
    """One schema-constrained call; returns the parsed reply, usage and the rate-limit headers."""
    body = {
        "model": model,
        "max_tokens": 1024,
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": READY_SCHEMA},
        },
        "messages": [{"role": "user", "content": "Are you ready? Name your model family."}],
    }
    raw = client.messages.with_raw_response.create(**guard_request(body))
    m = raw.parse()
    text = next(b.text for b in m.content if b.type == "text")
    reply = json.loads(text)
    if (
        not isinstance(reply, dict)
        or set(reply) != {"ready", "model_family"}
        or not isinstance(reply["ready"], bool)
        or not isinstance(reply["model_family"], str)
    ):
        raise ValueError("reply does not match the schema")
    limits = {h: raw.headers.get(h) for h in LIMIT_HEADERS}
    return reply, _usage(m.usage), limits


def cache_pair(client: Any, model: str, system: str) -> list[dict[str, int]]:
    """The same cacheable prompt twice; the second call should read the cache (PRE-03)."""
    out = []
    for _ in range(2):
        body = {
            "model": model,
            "max_tokens": 256,
            "output_config": {"effort": "low"},
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        }
        m = client.messages.create(**guard_request(body))
        out.append(_usage(m.usage))
    return out


def max_concurrency(requests_per_minute: int) -> int:
    """The most workers the requests-per-minute limit allows, at one call per worker per
    SECONDS_PER_CALL, capped at the config's own limit of 64."""
    return max(1, min(64, requests_per_minute * SECONDS_PER_CALL // 60))


def record_preflight(
    client: Any, config: RunConfig, prices: dict[str, Any], system: str
) -> dict[str, Any]:
    """Live proof for W0.2 on the Claude API: a structured call and a cache pair per model, the
    rate limits, and the cost of each call. No key, account or request id is recorded."""
    models: dict[str, Any] = {}
    total = 0.0
    rpm: list[int] = []
    for model in (config.stage1.model, config.stage2.model):
        reply, usage, limits = structured_call(client, model)
        pair = cache_pair(client, model, system)
        calls = [usage, *pair]
        cost = sum(usage_cost(u, prices[model]) for u in calls)
        total += cost
        if limits.get(LIMIT_HEADERS[0]) is not None:
            rpm.append(int(limits[LIMIT_HEADERS[0]]))
        models[model] = {
            "structured_reply_valid": True,
            "reply_ready": reply["ready"],
            "rate_limits": {
                h.removeprefix("anthropic-ratelimit-"): (int(v) if v is not None else None)
                for h, v in limits.items()
            },
            "cache": {
                "first": pair[0],
                "second": pair[1],
                "second_read_cache": pair[1]["cache_read_input_tokens"] > 0,
            },
            "calls": calls,
            "cost_usd": round(cost, 6),
        }
    return {
        "provider": config.provider,
        "models": models,
        "requests_per_minute_limit": min(rpm) if rpm else None,
        "max_concurrency": max_concurrency(min(rpm)) if rpm else None,
        "config_concurrency": config.concurrency,
        "total_cost_usd": round(total, 6),
        "seconds_per_call_assumed": SECONDS_PER_CALL,
    }


def exit_code(checks: list[Check]) -> int:
    """2 for an invalid config, 1 for any other failed check, 0 otherwise (warnings pass)."""
    if any(c.name == "config" and c.status == "fail" for c in checks):
        return 2
    return 1 if any(c.status == "fail" for c in checks) else 0
