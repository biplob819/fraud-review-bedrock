"""The triage stages (A2.3 stage 1, A2.4 stage 2; Tech Spec §2.1, §2.3, §4.2).

`run_stage` renders the prompt, looks the call up in the content-addressed cache, and otherwise
calls the model through `llm.settled_call` (which reserves the worst case and settles the
spend cap on every path), records the decision and stores it. Transport failures are not
cached, so a rerun retries them.
`needs_stage2` is the router hand-off after stage 1: an accepted clear ends the alert; anything
else (a pass, a refused clear or an error) goes to stage 2. `triage_alert` runs both stages and
the router and returns exactly one disposition per alert.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from fraudreview import llm, prompts, schemas
from fraudreview.cache import Store, cache_key, source_hash
from fraudreview.config import RunConfig
from fraudreview.cost import SpendCap
from fraudreview.llm import build_body, settled_call, to_decision
from fraudreview.policy import Policy
from fraudreview.router import RouterSettings, check_clear, dispose
from fraudreview.schemas import AlertCase, Disposition, TriageDecision

STAGE_CODE_VERSION = "4"
SCHEMAS_HASH = source_hash(schemas)[
    :16
]  # bump when this module's logic changes (code_versions.json)


@dataclass
class Context:
    config: RunConfig
    policy: Policy
    client: Any
    store: Store
    cap: SpendCap
    prices: dict[str, dict[str, float]]
    call_kw: dict[str, Any] = field(default_factory=dict)  # e.g. sleep and rng, for tests


def decision_id(alert_id: str, stage: int) -> str:
    return f"{alert_id}-s{stage}"


async def run_stage(stage: Literal[1, 2], case: AlertCase, ctx: Context) -> TriageDecision:
    cfg = ctx.config.stage1 if stage == 1 else ctx.config.stage2
    output_model = prompts.OUTPUT_MODELS[stage]
    system = prompts.system_blocks(stage, ctx.policy)
    user = prompts.render_alert(case)
    phash = prompts.prompt_hash(stage, ctx.policy)
    key = cache_key(
        step=f"stage{stage}",
        code_version=STAGE_CODE_VERSION,
        inp={  # everything else that decides the call or how its reply is handled
            "alert_id": case.alert_id,
            "user": user,
            "provider": ctx.config.provider,
            "max_output_tokens": cfg.max_output_tokens,
            "prompts_code_version": prompts.PROMPTS_CODE_VERSION,
            "model_call_code_version": llm.CODE_VERSION,
            "schemas_source": SCHEMAS_HASH,  # validators the JSON schema cannot express
        },
        model=cfg.model,
        effort=cfg.effort,
        prompt_hash=phash,
        policy_version=ctx.policy.version,
        seed=ctx.config.seed,
    )
    cached = ctx.store.get(key, STAGE_CODE_VERSION)
    if cached is not None:
        hit = TriageDecision.model_validate_json(json.dumps(cached))
        if (
            hit.alert_id != case.alert_id
            or hit.stage != stage
            or hit.provider != ctx.config.provider
            or hit.model != cfg.model
        ):
            raise ValueError(f"cache entry {key[:12]} belongs to another alert or stage")
        return hit.model_copy(update={"cache_hit": True})
    body = build_body(cfg, system, user, output_model)
    result, usd = await settled_call(
        ctx.client, body, output_model, cap=ctx.cap, price=ctx.prices[cfg.model], **ctx.call_kw
    )
    decision = to_decision(
        result,
        decision_id=decision_id(case.alert_id, stage),
        alert_id=case.alert_id,
        stage=stage,
        config=ctx.config,
        prompt_hash=phash,
        policy_version=ctx.policy.version,
        input_hash=hashlib.sha256(user.encode("utf-8")).hexdigest()[:16],
        cost_usd=usd,
    )
    if decision.error != "transport":  # a rerun retries transport failures
        ctx.store.put(key, STAGE_CODE_VERSION, decision.model_dump(mode="json"))
    return decision


def needs_stage2(stage1: TriageDecision, case: AlertCase, ctx: Context) -> bool:
    """The hand-off: only a clear the router accepts ends the alert at stage 1 (FR5)."""
    settings = RouterSettings.from_config(ctx.config)
    return not check_clear(stage1, case, ctx.policy, settings).accepted


@dataclass(frozen=True)
class Triaged:
    decisions: tuple[TriageDecision, ...]  # stage 1, then stage 2 when it ran
    disposition: Disposition


async def triage_alert(case: AlertCase, ctx: Context) -> Triaged:
    """Stage 1, the hand-off, stage 2 when needed, and exactly one disposition (A2.4, §2.3).
    A stage-2 error or refusal escalates; a refused stage-2 clear escalates with its reasons
    recorded on the disposition; nothing but an accepted clear is ever auto-cleared."""
    s1 = await run_stage(1, case, ctx)
    s2 = await run_stage(2, case, ctx) if needs_stage2(s1, case, ctx) else None
    settings = RouterSettings.from_config(ctx.config)
    disposition = dispose(case, s1, s2, ctx.policy, settings)
    return Triaged(tuple(d for d in (s1, s2) if d is not None), disposition)


def error_counts(decisions: list[TriageDecision]) -> dict[str, int]:
    """Failures by type (FR7, D-20): every decision with an error, counted once."""
    counts = {"schema": 0, "refusal": 0, "transport": 0}
    for d in decisions:
        if d.error is not None:
            counts[d.error] += 1
    return counts
