"""Router, dispositions and queues (Tech Spec §2.3, D-5, H2).

The model recommends; this module decides. A clear is accepted only when every router rule
holds; otherwise stage 1 passes the alert on and stage 2 escalates it. Neither queue touches
the card: `hold` means "likely fraud, review first" and `escalate` means "uncertain or system
failure". The package has no function that blocks a card or contacts a customer, and the model
client (N1.3) must pass every request through `guard_request`, which refuses tool definitions
(B6).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fraudreview.config import RunConfig
from fraudreview.policy import Policy
from fraudreview.schemas import AlertCase, Disposition, Stage2Output, TriageDecision

# Keys that would give the model tools, compared case-insensitively with `_` and `-` removed
# (Anthropic `tools`/`tool_choice`/`mcp_servers`, Bedrock Converse `toolConfig`).
FORBIDDEN_REQUEST_KEYS = frozenset({"tools", "toolchoice", "mcpservers", "toolconfig"})
_PLAIN = (str, int, float, bool, type(None))


class GuardError(Exception):
    """A model request carries tools; the agent may only recommend (B6)."""


@dataclass(frozen=True)
class RouterSettings:
    clear_ceiling: float  # rule 5: no auto-clear at or above this risk score
    clear_min_confidence: float  # rule 6, stage 2 only

    def __post_init__(self) -> None:
        # the same bounds as RunConfig, so a hand-built setting cannot switch a rule off
        if not 0.05 <= self.clear_ceiling <= 0.99:
            raise ValueError("clear_ceiling must be within [0.05, 0.99]")
        if not 0.5 <= self.clear_min_confidence <= 1.0:
            raise ValueError("clear_min_confidence must be within [0.5, 1.0]")

    @classmethod
    def from_config(cls, config: RunConfig) -> RouterSettings:
        return cls(config.clear_ceiling, config.clear_min_confidence)


@dataclass(frozen=True)
class ClearCheck:
    accepted: bool
    refusals: tuple[str, ...]  # in rule order; empty when accepted or when no clear was asked


def check_clear(
    decision: TriageDecision, case: AlertCase, policy: Policy, settings: RouterSettings
) -> ClearCheck:
    """Apply router rules 1-6 to one stage reply. Only a reply whose action is `clear` can be
    accepted; any other reply is not a clear and carries no reasons except an error."""
    if decision.error is not None or decision.output is None:  # rule 1
        return ClearCheck(False, (f"error:{decision.error}",))
    output = decision.output
    if output.action != "clear":
        return ClearCheck(False, ())
    reasons: list[str] = []
    if decision.alert_id != case.alert_id:
        reasons.append("alert_mismatch")
    if decision.policy_version != policy.version:
        reasons.append("policy_version_mismatch")
    # rules 2-4: cited clauses exist, a cited clear clause holds, no no-clear clause holds
    # (cited fields are limited to the allow-list by the schema)
    _, policy_reasons = policy.clearing_allowed(output.policy_clauses, case)
    reasons += policy_reasons
    if not case.risk_score < settings.clear_ceiling:  # rule 5
        reasons.append("risk_ceiling")
    if decision.stage == 2:  # rule 6
        assert isinstance(output, Stage2Output)
        if not output.confidence >= settings.clear_min_confidence:
            reasons.append("low_confidence")
    return ClearCheck(not reasons, tuple(reasons))


def _belongs(decision: TriageDecision, case: AlertCase, stage: Literal[1, 2]) -> None:
    if decision.alert_id != case.alert_id:
        raise ValueError(f"decision {decision.decision_id} is for another alert")
    if decision.stage != stage:
        raise ValueError(f"decision {decision.decision_id} is stage {decision.stage}, not {stage}")


def dispose(
    case: AlertCase,
    stage1: TriageDecision,
    stage2: TriageDecision | None,
    policy: Policy,
    settings: RouterSettings,
) -> Disposition:
    """Exactly one disposition for one alert from its stage replies."""
    _belongs(stage1, case, 1)
    first = check_clear(stage1, case, policy, settings)
    refusals = [f"s1:{r}" for r in first.refusals]
    if first.accepted:
        if stage2 is not None:
            raise ValueError("stage 1 cleared this alert; a stage-2 reply should not exist")
        return Disposition(
            alert_id=case.alert_id,
            final="auto_cleared",
            decided_at_stage=1,
            router_refusals=(),
            decision_ids=(stage1.decision_id,),
            queue=None,
        )
    if stage2 is None:
        raise ValueError(f"alert {case.alert_id} was not cleared at stage 1; stage 2 is needed")
    _belongs(stage2, case, 2)
    second = check_clear(stage2, case, policy, settings)
    refusals += [f"s2:{r}" for r in second.refusals]
    ids = (stage1.decision_id, stage2.decision_id)
    if second.accepted:
        final, queue = "auto_cleared", None
    elif stage2.output is not None and stage2.output.action == "hold":
        final, queue = "hold", "priority"
    else:
        final, queue = "escalate", "standard"
    return Disposition(
        alert_id=case.alert_id,
        final=final,
        decided_at_stage=2,
        router_refusals=tuple(refusals),
        decision_ids=ids,
        queue=queue,
    )


def reconcile(alert_ids: Iterable[str], dispositions: Iterable[Disposition]) -> list[str]:
    """Every problem with the one-disposition-per-alert rule; empty when it holds."""
    wanted = list(alert_ids)
    counts: dict[str, int] = {}
    for d in dispositions:
        counts[d.alert_id] = counts.get(d.alert_id, 0) + 1
    problems = [f"{a}: no disposition" for a in wanted if counts.get(a, 0) == 0]
    problems += [f"{a}: {n} dispositions" for a, n in counts.items() if n > 1]
    known = set(wanted)
    problems += [f"{a}: disposition for an unknown alert" for a in counts if a not in known]
    problems += [f"{a}: listed more than once" for a in sorted(set(wanted)) if wanted.count(a) > 1]
    return problems


def queues(dispositions: Sequence[Disposition]) -> Mapping[str, tuple[str, ...]]:
    """The alerts a person must review: holds first in the priority queue, escalations in the
    standard queue, each in input order. Auto-cleared alerts are in neither."""
    return {
        "priority": tuple(d.alert_id for d in dispositions if d.final == "hold"),
        "standard": tuple(d.alert_id for d in dispositions if d.final == "escalate"),
    }


def guard_request(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """Refuse a model request that offers the model tools, at any depth; return it unchanged.
    A request must be plain JSON data (dicts with string keys, lists, strings, numbers, booleans,
    None); anything else, or a structure that contains itself, is refused too."""

    def walk(item: Any, ancestors: frozenset[int]) -> None:
        if type(item) in _PLAIN:  # exact types: no subclasses carrying other behaviour
            if isinstance(item, float) and not math.isfinite(item):
                raise GuardError("model requests may not carry NaN or infinity (not JSON)")
            return
        if id(item) in ancestors:
            raise GuardError("a model request may not contain itself")
        inner = ancestors | {id(item)}
        if type(item) is dict:  # exact types throughout
            for key, value in item.items():
                if type(key) is not str:
                    raise GuardError("model request keys must be strings")
                if re.sub(r"[_-]", "", key).lower() in FORBIDDEN_REQUEST_KEYS:
                    raise GuardError(f"model requests may not carry {key!r} (B6)")
                walk(value, inner)
        elif type(item) in (list, tuple):
            for value in item:
                walk(value, inner)
        else:
            raise GuardError(f"model requests must be plain JSON data, not {type(item).__name__}")

    try:
        walk(body, frozenset())
    except RecursionError as exc:
        raise GuardError("a model request is nested too deeply") from exc
    return body
