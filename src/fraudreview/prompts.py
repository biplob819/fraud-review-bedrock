"""Prompt shape (A2.3, Tech Spec §4.2, D-7, NFR2).

1. System (cached): the role, the behaviour rules B1-B8 in plain words, "you recommend, people
   decide", the stage's job, and a glossary of the fields the model may cite.
2. Policy (cached): `policy.md` prose and clause ids, without the YAML predicates.
3. Alert: the agent-visible `AlertCase` fields as JSON, built from an allow-list.

`prompt_hash` covers 1, 2 and the output schema, so any change to what the model is told or
must return gives a new hash (and new cache keys). The alert id, merchant, timestamp, label and
SHAP drivers never enter a prompt.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel

from fraudreview.policy import Policy
from fraudreview.schemas import AlertCase, Stage1Output, Stage2Output

PROMPTS_CODE_VERSION = "2"  # bump with any change to this module (code_versions.json)

# the only keys a rendered alert may hold (NFR2); everything else is refused
ALERT_KEYS = ("ts_hour", "ts_dow", "category", "amount", "features", "risk_score")
FEATURE_KEYS = ("amt_vs_usual", "hour", "category_freq", "merchant_first_seen", "card_history_n")

GLOSSARY = {
    "amount": "the purchase amount in US dollars",
    "category": "the merchant category",
    "risk_score": "the screening model's fraud score, 0 to 1 (it flagged this alert)",
    "amt_vs_usual": "the amount divided by the card's usual (median) amount; null if the card "
    "has fewer than 3 earlier purchases",
    "hour": "the local hour of the purchase, 0 to 23",
    "category_freq": "the share of the card's earlier purchases in this category, 0 to 1",
    "merchant_first_seen": "true if the card has never been used at this merchant before",
    "card_history_n": "how many earlier purchases the card has",
}

RULES = """\
You triage card-fraud alerts for a bank's review team. You recommend; people decide. A router
checks every recommendation against the policy in code, and a person reviews everything that is
not cleared.

Rules:
1. Return exactly one decision record in the required format: an action, one to five short
   reasons, the policy clause ids you relied on, the fields you used, and your confidence (0-1).
   Where it helps, compare the purchase with the card's usual behaviour (amt_vs_usual,
   category_freq, card_history_n).
2. Cite only the fields listed in the glossary, and only values present in this alert. State
   numbers as they appear (for example "amount is 9.1 times the card's usual").
3. Cite only clause ids that appear in the policy. Never invent a clause.
4. Recommend clear only when a clear clause in the policy applies to this alert and no
   never-auto-clear clause applies. When in doubt, or when the evidence points both ways,
   do not clear.
5. Never use gender, age, birth date, location or any other personal attribute as a reason.
   This alert does not contain them; do not guess them.
6. You have no tools and cannot block a card or contact anyone. Your decision record is your
   only output."""

STAGE_JOB = {
    1: """\
Your job (stage 1, fast screen): choose "clear" only for an alert that is clearly routine under
a clear clause; choose "pass" for everything else. A pass sends the alert to a more careful
reviewer, so pass whenever you are unsure.""",
    2: """\
Your job (stage 2, careful review): choose "clear" only when a clear clause applies, no
never-auto-clear clause applies and you are confident; "hold" when the evidence points to fraud
(it goes to the priority queue); "escalate" when the evidence is mixed or unclear (it goes to the
standard queue). Set confidence honestly; low confidence never clears.""",
}

OUTPUT_MODELS: dict[int, type[BaseModel]] = {1: Stage1Output, 2: Stage2Output}


def glossary_text() -> str:
    return "Fields you may cite:\n" + "\n".join(f"- {k}: {v}" for k, v in GLOSSARY.items())


def system_blocks(stage: Literal[1, 2], policy: Policy) -> list[str]:
    """Two cached blocks: rules, stage job and glossary; then the policy prose."""
    if stage not in STAGE_JOB:
        raise ValueError("stage is 1 or 2")
    head = "\n\n".join((RULES, STAGE_JOB[stage], glossary_text()))
    return [head, "Policy:\n\n" + policy.prompt_text()]


def alert_payload(case: AlertCase) -> dict[str, Any]:
    """The agent-visible alert, from the allow-list only (NFR2)."""
    data = case.model_dump(mode="json")
    out = {k: data[k] for k in ALERT_KEYS}
    out["features"] = {k: data["features"][k] for k in FEATURE_KEYS}
    check_payload(out)
    return out


def check_payload(payload: dict[str, Any]) -> None:
    """Refuse a rendered alert holding any key off the allow-list, at any level."""
    if set(payload) != set(ALERT_KEYS):
        raise ValueError(f"alert keys must be exactly {ALERT_KEYS}")
    features = payload["features"]
    if not isinstance(features, dict) or set(features) != set(FEATURE_KEYS):
        raise ValueError(f"feature keys must be exactly {FEATURE_KEYS}")
    for v in [*payload.values(), *features.values()]:
        if isinstance(v, (dict, list)) and v is not features:
            raise ValueError("alert values must be plain scalars")


def render_alert(case: AlertCase) -> str:
    return "Alert:\n" + json.dumps(alert_payload(case), sort_keys=True, ensure_ascii=True)


def prompt_hash(stage: Literal[1, 2], policy: Policy) -> str:
    """sha256 over the cached blocks and the output schema (first 16 hex)."""
    from anthropic import transform_schema

    parts = {
        "system": system_blocks(stage, policy),
        "schema": transform_schema(OUTPUT_MODELS[stage]),
    }
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
