"""One way to call a model (N1.3, Tech Spec §2.4, D-2, D-3, D-4, D-16, D-20, D-27, D-35).

`build_body` makes a schema-constrained request with adaptive thinking, the stage's effort, cached
system blocks and no tools or sampling parameters. `call_model` passes the body through
`router.guard_request` before every send, validates the reply with the pydantic output model, and
applies the §2.4 retry rules. Errors come back as error kinds for the router to escalate; a
failed call never becomes a clear. Provider and configuration errors raise `ProviderConfigError`
(exit 2) and are never counted as `transport`.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from fraudreview.config import RunConfig, StageConfig
from fraudreview.router import guard_request
from fraudreview.schemas import Stage1Output, Stage2Output, Tokens, TriageDecision

ErrorKind = Literal["schema", "refusal", "transport"]
CODE_VERSION = "5"  # the `model_call` step; bump with any change to this module (BRK-03)
MAX_TRANSPORT_ATTEMPTS = 6  # §2.4
MAX_SCHEMA_ATTEMPTS = 2  # one retry, same prompt (FR7)
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0


class ProviderConfigError(Exception):
    """Authentication, permission, unknown model or a request the API refuses as malformed.
    The CLI maps it to exit 2; it is never a `transport` error (EvalKit L-21). `tokens` and
    `unmetered` carry what earlier attempts of the same call billed, so it can still be settled."""

    def __init__(self, message: str, tokens: Tokens | None = None, unmetered: int = 0) -> None:
        super().__init__(message)
        self.tokens = tokens or Tokens(input=0, output=0)
        self.unmetered = unmetered


@dataclass
class CallResult:
    output: BaseModel | None
    error: ErrorKind | None
    attempts: int
    tokens: Tokens
    request_id: str | None
    latency_ms: int
    stop_reason: str | None = None
    errors_seen: list[str] = field(default_factory=list)  # exception type names, oldest first
    unmetered: int = 0  # billed replies whose usage could not be read (an unparseable 200)


def build_body(
    stage: StageConfig,
    system: Sequence[str],
    user: str,
    output_model: type[BaseModel],
) -> dict[str, Any]:
    """The request body. The last system block carries the cache breakpoint, so the role, rules
    and policy text are one cached prefix (§4.2)."""
    from anthropic import transform_schema

    if not system:
        raise ValueError("a request needs at least one system block")
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text} for text in system]
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return {
        "model": stage.model,
        "max_tokens": stage.max_output_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": stage.effort,
            "format": {"type": "json_schema", "schema": transform_schema(output_model)},
        },
        "system": blocks,
        "messages": [{"role": "user", "content": user}],
    }


MAX_PLAUSIBLE_TOKENS = 10_000_000  # far above any context window; larger is unreadable


def _count(v: Any) -> int | None:
    ok = isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_PLAUSIBLE_TOKENS
    return v if ok else None


def _usage(u: Any) -> Tokens | None:
    """The reply's metered tokens, or None when usage is missing or unreadable (the reply is
    still billed; the caller prices it at the worst case). Cache fields may be absent or null."""
    inp, out = _count(getattr(u, "input_tokens", None)), _count(getattr(u, "output_tokens", None))
    if u is None or inp is None or out is None:
        return None
    cache_read, cache_write = (
        getattr(u, "cache_read_input_tokens", None),
        getattr(u, "cache_creation_input_tokens", None),
    )
    for v in (cache_read, cache_write):
        if v is not None and _count(v) is None:
            return None
    return Tokens(input=inp, output=out, cache_read=cache_read or 0, cache_write=cache_write or 0)


def _plausible(t: Tokens, body: dict[str, Any]) -> bool:
    """One attempt cannot read more input than the body has characters (a token is at least one
    character, ASCII-escaped), nor write more than `max_tokens`."""
    import json

    chars = len(json.dumps(body))
    return t.input + t.cache_read + t.cache_write <= chars and t.output <= body["max_tokens"]


def _add(a: Tokens, b: Tokens) -> Tokens:
    return Tokens(
        input=a.input + b.input,
        output=a.output + b.output,
        cache_read=a.cache_read + b.cache_read,
        cache_write=a.cache_write + b.cache_write,
    )


# the SDKs' own wording when no key or AWS credentials can be resolved at send time
CREDENTIAL_PHRASES = ("could not resolve authentication", "could not resolve aws credentials")


def _classify(exc: BaseException) -> Literal["transient", "config"] | None:
    """Sort an exception from a send: retry it, stop the run (exit 2), or neither (re-raise)."""
    import anthropic

    if isinstance(exc, anthropic.APIConnectionError):  # includes timeouts
        return "transient"
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status in (408, 409, 429) or status >= 500:  # 529 is overload
            return "transient"
        return "config"  # every other 4xx: our request, key, model or account
    if isinstance(exc, anthropic.AnthropicError):  # client-side, e.g. no Region resolved
        return "config"
    try:  # boto3 signing for Bedrock: missing profile, expired login or SSO token, no Region
        from botocore.exceptions import BotoCoreError

        if isinstance(exc, BotoCoreError):
            return "config"
    except ImportError:  # pragma: no cover - botocore ships with the bedrock extra
        pass
    if isinstance(exc, ValueError) and "streaming is required" in str(exc).lower():
        return "config"  # the SDK refuses before sending: max_output_tokens is too high
    if isinstance(exc, (TypeError, RuntimeError)) and any(
        p in str(exc).lower() for p in CREDENTIAL_PHRASES
    ):
        return "config"
    return None


def backoff_s(attempt: int, rng: random.Random) -> float:
    """Full jitter: uniform in [0, min(cap, base * 2**(attempt-1))]."""
    return rng.uniform(0.0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1)))


def _reply_text(message: Any) -> str | None:
    """The first text block, or None for anything malformed (a reply is read defensively: the
    SDK builds replies without strict validation, so any field may hold any type)."""
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return None
    for block in content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            return text if isinstance(text, str) else None
    return None


async def call_model(
    client: Any,
    body: dict[str, Any],
    output_model: type[BaseModel],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> CallResult:
    """Send `body`, retrying per §2.4. Returns a result with an output or an error kind."""
    rng = rng or random.Random()
    tokens = Tokens(input=0, output=0)
    started = clock()
    attempts = transport_failures = schema_failures = 0
    request_id: str | None = None
    stop_reason: str | None = None
    seen: list[str] = []
    unmetered = 0

    def done(output: BaseModel | None, error: ErrorKind | None) -> CallResult:
        return CallResult(
            output=output,
            error=error,
            attempts=attempts,
            tokens=tokens,
            request_id=request_id,
            latency_ms=max(0, int((clock() - started) * 1000)),
            stop_reason=stop_reason,
            errors_seen=seen,
            unmetered=unmetered,
        )

    while True:
        guard_request(body)  # B6: immediately before every send, retries included
        attempts += 1
        try:
            raw = await client.messages.with_raw_response.create(**body)  # send only
        except Exception as exc:
            kind = _classify(exc)
            seen.append(type(exc).__name__)
            if kind == "config":
                status = getattr(exc, "status_code", None)
                where = f"HTTP {status}, " if isinstance(status, int) else ""
                raise ProviderConfigError(
                    f"the provider refused the request ({where}{type(exc).__name__}); check "
                    "ANTHROPIC_API_KEY (provider anthropic) or the AWS profile (provider "
                    "bedrock), the model id and config.yaml",
                    tokens=tokens,
                    unmetered=unmetered,
                ) from None
            if kind != "transient":
                raise
            transport_failures += 1
            if transport_failures >= MAX_TRANSPORT_ATTEMPTS:
                return done(None, "transport")
            await sleep(backoff_s(transport_failures, rng))
            continue
        # A 2xx reply is billed. Parse it apart from the send, so that any failure to read it
        # (bad JSON, bad encoding, nesting, huge numbers, wrong types) is an invalid reply
        # priced at the worst case, never a crash and never a send-time error class.
        headers = getattr(raw, "headers", None)
        rid = headers.get("request-id") if headers is not None else None
        request_id = rid if isinstance(rid, str) else None
        try:
            message = await raw.parse()
        except Exception:
            seen.append("UnreadableReply")
            unmetered += 1
            schema_failures += 1
            if schema_failures >= MAX_SCHEMA_ATTEMPTS:
                return done(None, "schema")
            continue
        metered = _usage(getattr(message, "usage", None))
        if metered is not None and not _plausible(metered, body):
            metered = None  # more than this body could bill: treat as unreadable
        if metered is None:  # billed, but we cannot read what it cost: price the worst case
            unmetered += 1
        else:
            tokens = _add(tokens, metered)
        stop_reason = getattr(message, "stop_reason", None)
        if not isinstance(stop_reason, str):
            stop_reason = None
        if stop_reason == "refusal":
            return done(None, "refusal")
        text = _reply_text(message)
        try:
            if stop_reason == "max_tokens" or text is None:
                raise ValueError("incomplete reply")
            output = output_model.model_validate_json(text)
        except (ValidationError, ValueError):
            seen.append("InvalidReply")
            schema_failures += 1
            if schema_failures >= MAX_SCHEMA_ATTEMPTS:
                return done(None, "schema")
            continue
        return done(output, None)


async def settled_call(
    client: Any,
    body: dict[str, Any],
    output_model: type[BaseModel],
    *,
    cap: Any,
    price: dict[str, float],
    **kw: Any,
) -> tuple[CallResult, float]:
    """Reserve the worst case, call, and settle the spend cap on every path (BRK-05).
    - Success: commit what was billed (`billed_cost`).
    - `ProviderConfigError`: commit what earlier attempts billed, then re-raise (exit 2).
    - Any other exception: commit the whole reservation, since a reply may have been billed
      before the failure, then re-raise. Spend is never released unrecorded.
    `commit` may raise `CapReached` (exit 3) after recording."""
    reserve = worst_case_cost(body, price)
    token = await cap.reserve(reserve)
    try:
        result = await call_model(client, body, output_model, **kw)
        usd = billed_cost(result.tokens, result.unmetered, body, price)
    except ProviderConfigError as exc:
        try:
            spent = billed_cost(exc.tokens, exc.unmetered, body, price)
        except Exception:
            spent = reserve
        await cap.commit(token, min(spent, reserve) if spent == spent else reserve)
        raise
    except BaseException:
        await cap.commit(token, reserve)
        raise
    await cap.commit(token, usd)
    return result, usd


def make_client(config: RunConfig) -> Any:
    """The async client for the configured provider, with SDK retries off (§2.4 is the policy).
    The Claude API key comes from ANTHROPIC_API_KEY; Bedrock uses the AWS profile."""
    if config.provider == "anthropic":
        import os

        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderConfigError("ANTHROPIC_API_KEY is not set (put it in .env)")
        return anthropic.AsyncAnthropic(max_retries=0)
    from anthropic.lib.bedrock._mantle import AsyncAnthropicBedrockMantle

    try:
        return AsyncAnthropicBedrockMantle(aws_region=config.region, max_retries=0)
    except Exception as exc:
        raise ProviderConfigError(
            f"could not build the Bedrock client ({type(exc).__name__}); run `aws login` and "
            "set AWS_PROFILE"
        ) from None


def billed_cost(
    tokens: Tokens, unmetered: int, body: dict[str, Any], price: dict[str, float]
) -> float:
    """What a call billed: its metered tokens, plus the worst case for each billed reply whose
    usage could not be read, so unread replies still count against the cap and the ledger."""
    from fraudreview.cost import call_cost

    return call_cost(tokens, price) + unmetered * worst_case_cost(body, price) / MAX_SCHEMA_ATTEMPTS


def worst_case_cost(body: dict[str, Any], price: dict[str, float]) -> float:
    """The most one `call_model` can bill: every schema attempt with the whole body as input at
    the dearer of the input and cache-write prices (a token is at least one character), and a
    full `max_tokens` reply. Reserving this keeps billed spend under the cap (BRK-05). Assumption:
    an attempt that fails in transport (timeout, dropped connection) is not billed; if one is,
    its tokens are unknown to us, and `SpendCap.commit` still stops the run once recorded spend
    passes the cap."""
    import json

    chars = len(json.dumps(body))  # ASCII escapes: a rare character counts as 6, not 1
    per_attempt = (
        chars * max(price["input"], price["cache_write_5m"]) + body["max_tokens"] * price["output"]
    )
    return MAX_SCHEMA_ATTEMPTS * per_attempt / 1e6


def to_decision(
    result: CallResult,
    *,
    decision_id: str,
    alert_id: str,
    stage: int,
    config: RunConfig,
    prompt_hash: str,
    policy_version: str,
    input_hash: str,
    cost_usd: float,
    cache_hit: bool = False,
    at: datetime | None = None,
) -> TriageDecision:
    """The decision record for one stage call; the provider is recorded on every decision."""
    stage_cfg = config.stage1 if stage == 1 else config.stage2
    expected = Stage1Output if stage == 1 else Stage2Output
    if result.output is not None and type(result.output) is not expected:
        raise ValueError(f"stage {stage} output must be {expected.__name__}")
    return TriageDecision(
        decision_id=decision_id,
        alert_id=alert_id,
        stage=stage,
        provider=config.provider,
        model=stage_cfg.model,
        effort=stage_cfg.effort,
        prompt_hash=prompt_hash,
        policy_version=policy_version,
        input_hash=input_hash,
        output=result.output,  # type: ignore[arg-type]
        error=result.error,
        attempts=min(result.attempts, 10),
        tokens=result.tokens,
        cost_usd=cost_usd,
        latency_ms=result.latency_ms,
        request_id=result.request_id,
        cache_hit=cache_hit,
        at=at or datetime.now(UTC),
    )
