"""E2.3 DSH-01: `fraud evaluate` and `summary.build`. Offline: synthetic and committed data only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fraudreview import summary
from fraudreview.cli import main
from fraudreview.config import load_config
from fraudreview.policy import load_policy
from fraudreview.schemas import RunSummary
from fraudreview.summary_model import EvaluatedSummary
from fraudreview.truth import AlertTruth
from tests.test_metrics import synthetic_run
from tests.test_stages import case

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "fixtures" / "golden"
FREEZE = {
    "digest": "0123456789abcdef",
    "policy_version": "pv1",
    "prompt_hashes": {"stage1": "a1", "stage2": "b2"},
    "models": {"stage1": "claude-haiku-5-5", "stage2": "claude-sonnet-5-5"},
    "provider": "anthropic",
}


def write_run(tmp_path: Path, leak: bool) -> tuple[Path, Path, Path]:
    disps, decs, labels = synthetic_run(60, 7, leak=leak)
    run = tmp_path / "dev-60-01234567"
    run.mkdir()
    (run / "dispositions.jsonl").write_text(
        "".join(d.model_dump_json() + "\n" for d in disps), encoding="utf-8"
    )
    (run / "decisions.jsonl").write_text(
        "".join(d.model_dump_json() + "\n" for d in decs), encoding="utf-8"
    )
    facts = {"n_total": 60, "n_done": 60, "stopped": None, "freeze": FREEZE, "errors": {}}
    (run / "run.json").write_text(json.dumps(facts), encoding="utf-8")
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "".join(case(alert_id=a).model_dump_json() + "\n" for a in labels), encoding="utf-8"
    )
    truth = tmp_path / "truth.jsonl"
    truth.write_text(
        "".join(
            AlertTruth(alert_id=a, label=fr, shap_top5=()).model_dump_json() + "\n"
            for a, fr in labels.items()
        ),
        encoding="utf-8",
    )
    return run, cases, truth


def evaluate(run: Path, cases: Path, truth: Path, out: Path) -> int:
    return main(
        [
            "evaluate",
            "--run",
            str(run),
            "--cases",
            str(cases),
            "--truth",
            str(truth),
            "--out",
            str(out),
            "--no-append",
            "--config",
            str(ROOT / "config.yaml"),
            "--baseline",
            str(ROOT / summary.BASELINE_SUMMARY),
        ]
    )


def test_exit_code_is_1_when_a_fraud_leaks(tmp_path):
    run, cases, truth = write_run(tmp_path, leak=True)
    out = tmp_path / "summary.json"
    assert evaluate(run, cases, truth, out) == 1
    s = EvaluatedSummary.model_validate_json(out.read_text(encoding="utf-8"))
    assert s.leakage.k > 0
    leakage = next(t for t in s.thresholds if t.metric == "leakage")
    assert leakage.passed is False


def test_exit_code_is_0_with_no_leak_and_the_summary_round_trips(tmp_path):
    run, cases, truth = write_run(tmp_path, leak=False)
    out = tmp_path / "summary.json"
    assert evaluate(run, cases, truth, out) == 0
    text = out.read_text(encoding="utf-8")
    s = EvaluatedSummary.model_validate_json(text)
    assert s.leakage.k == 0 and s.leakage.hi > 0  # an exact bound, not zero
    assert s.freeze_digest == FREEZE["digest"]
    assert s.explanation.judge_mode == "none"
    assert s.model_dump_json(indent=2) + "\n" == text


def test_evaluate_needs_its_inputs(capsys):
    assert main(["evaluate"]) == 2
    assert "--run" in capsys.readouterr().err


def test_unfinished_run_is_refused(tmp_path):
    run, cases, truth = write_run(tmp_path, leak=False)
    facts = json.loads((run / "run.json").read_text(encoding="utf-8"))
    facts["n_done"] = 10
    (run / "run.json").write_text(json.dumps(facts), encoding="utf-8")
    assert evaluate(run, cases, truth, tmp_path / "s.json") == 2


def golden_built(**kw: Any) -> summary.Built:
    return summary.build(
        summary.load_golden(GOLDEN),
        load_config(ROOT / "config.yaml"),
        load_policy(),
        summary.read_baseline(ROOT / summary.BASELINE_SUMMARY),
        **kw,
    )


def test_golden_summary_validates_and_round_trips():
    s = golden_built().summary
    assert (s.n_alerts, s.n_frauds, s.split) == (20, 4, "golden")
    assert s.leakage.k == 0 and s.explanation.value_shares is not None
    again = EvaluatedSummary.model_validate_json(summary.to_json(s))
    assert again == s and summary.to_json(again) == summary.to_json(s)


def test_faithfulness_partial_and_notes_are_carried_through():
    s = golden_built(
        faithfulness=ROOT / "docs" / "measurements" / "faithfulness-cache-summary.json"
    )
    e = s.summary.explanation
    assert e.judge_mode == "single" and list(e.faithfulness) == ["claude-opus-5-5"]
    assert e.faithfulness_detail is not None
    assert e.faithfulness_detail.partial is True and len(e.faithfulness_detail.notes) >= 4


def test_committed_eval2_summary_is_valid_and_reports_the_known_result():
    s = summary.read_summary(ROOT / "docs" / "measurements" / "summary-eval2.json")
    assert (s.leakage.k, s.leakage.n, s.auto_clear_rate.k, s.auto_clear_rate.n) == (0, 55, 44, 500)
    assert s.leakage.hi == pytest.approx(0.053, abs=5e-4)
    assert s.explanation.judge_mode == "single"


def test_the_v1_summary_fixture_stays_byte_identical():
    text = (ROOT / "tests" / "fixtures" / "v1" / "summary.json").read_text(encoding="utf-8")
    s = RunSummary.model_validate_json(text)
    assert s.model_dump_json(indent=2) + "\n" == text
    assert "alarms" not in text and "value_shares" not in text
