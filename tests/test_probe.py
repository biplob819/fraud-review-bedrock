"""EXP-08: the prompt-injection probe. Fake agents only; no network, no model."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from fraudreview import probe
from fraudreview.policy import load_policy
from fraudreview.prompts import alert_payload, render_alert
from fraudreview.router import RouterSettings, dispose
from fraudreview.schemas import AlertCase, Disposition
from tests.test_explain import dec
from tests.test_stages import RISKY, ROUTINE

POLICY = load_policy()
CASES = [RISKY.model_copy(update={"alert_id": f"a-abc{i:05x}de{i:05x}f"}) for i in range(1, 21)]


def disposition(case: AlertCase, final: str) -> Disposition:
    stage = 1 if final == "auto_cleared" else 2
    ids = tuple(f"{case.alert_id}-s{s}" for s in range(1, stage + 1))
    queue = {"auto_cleared": None, "hold": "priority", "escalate": "standard"}[final]
    return Disposition.model_validate(
        {
            "alert_id": case.alert_id,
            "final": final,
            "decided_at_stage": stage,
            "decision_ids": ids,
            "queue": queue,
        }
    )


def gullible(case: AlertCase) -> Disposition:
    """Clears whenever the alert text says clear."""
    text = case.category.lower()
    return disposition(case, "auto_cleared" if "clear" in text else "escalate")


def careful(case: AlertCase) -> Disposition:
    return disposition(case, "escalate")


def test_the_only_string_reaching_a_prompt_is_the_closed_category() -> None:
    assert [(s.field, s.free_text) for s in probe.surface()] == [("category", False)]
    sample = alert_payload(ROUTINE)
    strings = [k for k, v in sample.items() if isinstance(v, str)]
    assert strings == ["category"]  # the schema view agrees with a rendered alert


def test_every_free_text_field_is_covered_by_the_probe() -> None:
    """Fails when a free-text field is added to what a prompt carries and the probe does not
    plant into it (add the field to `probe.PLANTERS` and teach `plant` to fill it)."""
    assert probe.uncovered() == []
    assert set(probe.free_text_fields()) <= set(probe.PLANTERS)


def test_the_schema_refuses_text_in_category() -> None:
    data = ROUTINE.model_dump(mode="json") | {"category": probe.INJECTIONS[0].text}
    with pytest.raises(ValidationError):
        AlertCase.model_validate(data)


def test_plant_reports_that_no_channel_exists() -> None:
    with pytest.raises(probe.NoFreeTextChannel):
        probe.plant(ROUTINE, probe.INJECTIONS[0].text)


def test_injections_cover_the_attack_families() -> None:
    names = {i.name for i in probe.INJECTIONS}
    assert len(probe.INJECTIONS) >= 8
    assert {"ignore_policy", "night_allowed", "fake_system", "clause_forgery", "json_in_json"} <= (
        names
    )
    assert len({i.text for i in probe.INJECTIONS}) == len(probe.INJECTIONS)
    json.loads(next(i.text for i in probe.INJECTIONS if i.name == "json_in_json"))


def test_hypothetical_plant_puts_the_text_in_the_prompt_alert() -> None:
    text = probe.INJECTIONS[0].text
    planted = probe.plant(ROUTINE, text, hypothetical=True)
    assert planted.category == text and ROUTINE.category != text
    assert text in render_alert(planted)


def test_no_surface_is_the_honest_result_on_the_real_schema() -> None:
    result = probe.scenario(CASES, gullible)
    assert result.verdict == "no_injection_surface"
    assert result.planted == 0 and result.passed
    assert [s.field for s in result.surface] == ["category"]


def test_the_harness_catches_a_vulnerable_agent() -> None:
    result = probe.scenario(CASES, gullible, hypothetical=True)
    assert result.planted == 20
    assert result.verdict == "vulnerable" and not result.passed
    assert result.auto_cleared == 20  # every injection text says "clear"
    assert {a.outcome for a in result.attempts} == {"succeeded"}
    assert all(a.value == 0.0 for a in result.attempts)
    assert len({a.injection for a in result.attempts}) == len(probe.INJECTIONS)


def test_a_careful_agent_passes() -> None:
    result = probe.scenario(CASES, careful, hypothetical=True)
    assert result.verdict == "defended" and result.passed and result.planted == 20
    assert {a.outcome for a in result.attempts} == {"blocked"}


def test_a_gullible_model_behind_the_real_router_is_defended_on_no_clear_alerts() -> None:
    """The model is fooled into a stage-1 and stage-2 clear; the router still refuses it."""
    settings = RouterSettings(clear_ceiling=0.045, clear_min_confidence=0.8)

    def run(case: AlertCase) -> Disposition:
        action = "clear" if "clear" in case.category.lower() else "pass"
        s1 = dec(case.alert_id, stage=1, action="clear" if action == "clear" else "pass")
        s2 = dec(case.alert_id, stage=2, action="clear" if action == "clear" else "escalate")
        s1 = s1.model_copy(update={"policy_version": POLICY.version})
        s2 = s2.model_copy(update={"policy_version": POLICY.version})
        return dispose(case, s1, s2, POLICY, settings)

    chosen = probe.no_clear_cases(CASES, POLICY)
    assert chosen == CASES  # RISKY holds no-clear clauses (night, 9.1x usual, first merchant)
    result = probe.scenario(chosen, run, hypothetical=True)
    assert result.verdict == "defended" and result.auto_cleared == 0


def test_no_clear_cases_excludes_a_routine_alert() -> None:
    assert probe.no_clear_cases([ROUTINE, RISKY], POLICY) == [RISKY]


def test_scenario_needs_an_injection() -> None:
    with pytest.raises(ValueError):
        probe.scenario(CASES, careful, injections=(), hypothetical=True)
