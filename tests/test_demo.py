"""D4.1 analyst review screen: `fraud demo-cache` (DEM-01), the demo package (§4.4, DEM-03) and
its import ban (DEM-04), and the Streamlit page importing without a server (DEM-02)."""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fraudreview import demo
from fraudreview.audit import event_hash, read_log
from fraudreview.cli import main
from fraudreview.demo_cache import build_replay, build_replay_guarded, write_replay
from fraudreview.schemas import AnalystAction
from fraudreview.truth import Alert

ROOT = Path(__file__).resolve().parent.parent
REPLAY = ROOT / "demo" / "replay.jsonl"
APP = ROOT / "demo" / "app.py"
AT = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
BANNED = ("fraudreview.llm", "fraudreview.stages", "anthropic", "boto3", "botocore")


def _id(n: int) -> str:
    return f"a-{n:x}abcdefabcdefabc"[:18]  # 16 hex characters, no long digit run


def _alert(n: int, risk: float) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "case": {
            "alert_id": _id(n),
            "ts_hour": 3,
            "ts_dow": 2,
            "category": "misc_net",
            "amount": 120.5,
            "features": {
                "amt_vs_usual": 4.0,
                "hour": 3,
                "category_freq": 0.1,
                "merchant_first_seen": True,
                "card_history_n": 20,
            },
            "risk_score": risk,
        },
        "truth": {
            "alert_id": _id(n),
            "label": False,
            "shap_top5": [{"feature": "amount", "contribution": 1.5}],
        },
        "merchant": "Example Store",
        "trans_ts": "2020-07-01T03:10:00Z",
    }


def _decision(n: int, stage: int, action: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "decision_id": f"{_id(n)}-s{stage}",
        "alert_id": _id(n),
        "stage": stage,
        "provider": "anthropic",
        "model": "claude-haiku-5-5" if stage == 1 else "claude-sonnet-5-5",
        "effort": "medium",
        "prompt_hash": "p",
        "policy_version": "v",
        "input_hash": "i",
        "output": {
            "reasons": ["Night-time purchase."],
            "policy_clauses": ["P5.9"],
            "fields_used": ["hour"],
            "confidence": 0.8,
            "action": action,
        },
        "error": None,
        "attempts": 1,
        "tokens": {"input": 1, "output": 1},
        "cost_usd": 0.001,
        "latency_ms": 10,
        "request_id": f"req_secret{n}",
        "cache_hit": False,
        "at": "2026-01-05T14:30:00Z",
    }


# (alert number, risk, final) for a small run: two holds, two escalations, one auto-clear
CASES = [
    (1, 0.20, "escalate"),
    (2, 0.95, "auto_cleared"),
    (3, 0.40, "hold"),
    (4, 0.90, "escalate"),
    (5, 0.10, "hold"),
]


def _write_run(tmp: Path) -> tuple[Path, Path]:
    run = tmp / "run"
    run.mkdir()
    decisions, dispositions, alerts = [], [], []
    for n, risk, final in CASES:
        alerts.append(_alert(n, risk))
        if final == "auto_cleared":
            decisions.append(_decision(n, 1, "clear"))
            ids, stage, q = [f"{_id(n)}-s1"], 1, None
        else:
            decisions += [_decision(n, 1, "pass"), _decision(n, 2, final)]
            ids, stage = [f"{_id(n)}-s1", f"{_id(n)}-s2"], 2
            q = "priority" if final == "hold" else "standard"
        refusals = ["s2:risk_ceiling"] if n == 4 else []
        dispositions.append(
            {
                "kind": "disposition",
                "alert_id": _id(n),
                "final": final,
                "decided_at_stage": stage,
                "router_refusals": refusals,
                "decision_ids": ids,
                "queue": q,
            }
        )
    for name, rows in (("decisions", decisions), ("dispositions", dispositions)):
        (run / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
    alerts_path = tmp / "alerts_eval2.jsonl"
    alerts_path.write_text("".join(json.dumps(a) + "\n" for a in alerts), encoding="utf-8")
    return run, alerts_path


# ---------- DEM-01 demo-cache ----------


@pytest.mark.contract
def test_demo_cache_orders_priority_first_then_risk_and_drops_auto_clears(tmp_path):
    run, alerts = _write_run(tmp_path)
    records = build_replay(run, alerts)
    order = [r["alert"]["case"]["alert_id"] for r in records]
    assert order == [_id(3), _id(5), _id(4), _id(1)]  # holds by risk, then escalations by risk
    assert _id(2) not in order  # auto-cleared
    assert [r["disposition"]["queue"] for r in records] == ["priority"] * 2 + ["standard"] * 2
    by_id = {r["alert"]["case"]["alert_id"]: r for r in records}
    assert by_id[_id(4)]["disposition"]["router_refusals"] == ["s2:risk_ceiling"]
    assert [d["stage"] for d in by_id[_id(3)]["decisions"]] == [1, 2]
    assert by_id[_id(3)]["decisions"][1]["action"] == "hold"
    for r in records:
        Alert.model_validate_json(json.dumps(r["alert"]))
        assert r["data_label"] == "simulated"


@pytest.mark.contract
def test_demo_cache_strips_request_ids(tmp_path):
    run, alerts = _write_run(tmp_path)
    out = tmp_path / "replay.jsonl"
    assert main(["demo-cache", "--run", str(run), "--alerts", str(alerts), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "request_id" not in text and "req_" not in text
    assert len(text.splitlines()) == 4


@pytest.mark.contract
def test_demo_cache_cap_keeps_holds_first(tmp_path):
    run, alerts = _write_run(tmp_path)
    n, _, _ = write_replay(run, alerts, tmp_path / "r.jsonl", max_cases=2)
    lines = (tmp_path / "r.jsonl").read_text(encoding="utf-8").splitlines()
    assert n == 2 and all(json.loads(x)["disposition"]["queue"] == "priority" for x in lines)


@pytest.mark.contract
def test_demo_cache_refuses_a_queued_alert_without_its_view(tmp_path):
    run, alerts = _write_run(tmp_path)
    keep = [x for x in alerts.read_text(encoding="utf-8").splitlines() if _id(3) not in x]
    alerts.write_text("\n".join(keep) + "\n", encoding="utf-8")
    code = main(["demo-cache", "--run", str(run), "--alerts", str(alerts), "--out", "-"])
    assert code == 2


@pytest.mark.contract
def test_committed_replay_is_small_ordered_and_clean():
    data = REPLAY.read_bytes()
    assert len(data) < 2_000_000
    text = data.decode("ascii")
    assert "request_id" not in text and "req_" not in text
    records = demo.queue(REPLAY)
    assert records, "the committed replay is empty"
    queues = [r["disposition"]["queue"] for r in records]
    assert queues == sorted(queues, key={"priority": 0, "standard": 1}.__getitem__)
    assert all(r["disposition"]["final"] != "auto_cleared" for r in records)
    raw = [json.loads(x)["alert"]["case"]["alert_id"] for x in text.splitlines()]
    assert raw == [r["alert"]["case"]["alert_id"] for r in records]  # written in queue order


# ---------- DEM-03 review writes the session chain ----------


def test_review_appends_a_valid_chained_event(tmp_path):
    log = demo.session_log(tmp_path)
    first = demo.review(_id(1), "confirm_fraud", "", log, at=AT)
    second = demo.review(_id(2), "override", " customer called ", log, at=AT)
    events = read_log(log.path)  # verifies the whole chain
    assert [e.seq for e in events] == [0, 1]
    assert events[1].prev_hash == events[0].hash == first.hash
    assert event_hash(second) == second.hash
    assert isinstance(second.payload, AnalystAction)
    assert second.payload.comment == "customer called"
    assert first.payload.comment is None  # type: ignore[union-attr]
    assert demo.session_bytes(log) == log.path.read_bytes()


def test_override_without_comment_is_refused(tmp_path):
    log = demo.session_log(tmp_path)
    with pytest.raises(ValueError, match="comment"):
        demo.review(_id(1), "override", "   ", log, at=AT)
    assert demo.session_events(log) == []


def test_no_block_action():
    assert set(demo.ACTIONS) == {"confirm_fraud", "clear", "override"}
    assert "Block" not in APP.read_text(encoding="utf-8")


def test_session_logs_are_separate():
    a, b = demo.session_log(), demo.session_log()
    assert a.path != b.path


def test_policy_text():
    assert demo.policy_text("P3.9").startswith("P3.9 Never auto-clear extreme spend: A purchase")
    assert "not a clause" in demo.policy_text("P99.9")
    assert demo.policy_title("P99.9") is None


# ---------- DEM-04 no model or cloud client in the demo ----------


def _module_file(name: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(name)
    except ModuleNotFoundError:  # a name imported from a module, not a module
        return None
    return Path(spec.origin) if spec and spec.origin and spec.origin.endswith(".py") else None


def _imports(path: Path, package: str) -> set[str]:
    """Every module a file imports, at any depth (lazy imports included)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parts, base] if base else parts)
            found.add(base)
            found |= {f"{base}.{a.name}" for a in node.names}
    return found


@pytest.mark.safety
def test_demo_import_closure_has_no_model_or_cloud_client():
    """Walk every fraudreview module the demo package can reach (statically, lazy imports
    included) and check that none of them is or imports a banned module."""
    seen: set[str] = set()
    todo = ["fraudreview.demo"]
    reached: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_file(name)
        if path is None:
            continue
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for imported in _imports(path, package):
            reached.add(imported)
            if imported.split(".")[0] == "fraudreview" and _module_file(imported):
                todo.append(imported)
    bad = sorted(m for m in reached if any(m == b or m.startswith(b + ".") for b in BANNED))
    assert not bad, f"the demo package reaches {bad}"
    assert "fraudreview.demo" in seen and "fraudreview.audit" in seen


@pytest.mark.safety
def test_importing_demo_loads_no_banned_module():
    code = "import sys, json; import fraudreview.demo; print(json.dumps(sorted(sys.modules)))"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    loaded = json.loads(out)
    bad = [m for m in loaded if any(m == b or m.startswith(b + ".") for b in BANNED)]
    assert not bad, f"importing fraudreview.demo loaded {bad}"


@pytest.mark.safety
def test_demo_files_name_no_credential():
    for path in [*(ROOT / "src" / "fraudreview" / "demo").rglob("*.py"), APP]:
        text = path.read_text(encoding="utf-8")
        for word in ("ANTHROPIC_API_KEY", "AWS_ACCESS_KEY", "AWS_SECRET", "st.secrets", ".env"):
            assert word not in text, f"{path} mentions {word}"


# ---------- DEM-02 the page ----------


def test_app_imports_without_starting_a_server():
    pytest.importorskip("streamlit")
    spec = importlib.util.spec_from_file_location("demo_app_under_test", APP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # __name__ is not "__main__", so main() does not run
    assert callable(module.main)
    assert module.num(0.5, "{:.2f}") == "0.50 (simulated)"
    record = demo.queue(REPLAY)[0]
    assert "(simulated)" in module.case_label(record)


def _run_app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_app_walks_review_to_decision_to_audit_entry_headless():
    """DEM-02/DEM-03: the real page over the real replay, headless: pick a case, see the reasons
    and score drivers, record a decision, and find the audit entry, with the chain verifying."""
    pytest.importorskip("streamlit")
    at = _run_app()
    cases = demo.queue(REPLAY)
    first = cases[0]
    alert_id = first["alert"]["case"]["alert_id"]
    shown = " ".join(m.value for m in at.markdown)
    assert first["alert"]["merchant"] in shown and "(simulated)" in shown
    assert any("Score drivers" in s.value for s in at.subheader)
    assert [b.label for b in at.button] == ["Confirm fraud", "Clear", "Override with comment"]
    assert "No actions yet in this session." in [m.value for m in at.markdown]

    # an override with no comment is refused and writes nothing
    at.button(key=f"override-{alert_id}").click().run()
    assert any("needs a comment" in e.value for e in at.error)
    log = at.session_state["audit_log"]
    assert demo.session_events(log) == []

    # confirm: one chained entry, shown in the trail with a verified chain
    at.button(key=f"confirm-{alert_id}").click().run()
    assert not at.exception
    assert any("audit event 0" in s.value for s in at.success)
    events = demo.session_events(log)  # verifies the chain
    assert len(events) == 1 and events[0].alert_id == alert_id
    assert isinstance(events[0].payload, AnalystAction)
    assert events[0].payload.action == "confirm_fraud"
    assert any("Chain verified: 1 events" in s.value for s in at.success)

    # a second call on the next case, with a comment, extends the chain
    at.selectbox[0].set_value(1).run()
    second_id = cases[1]["alert"]["case"]["alert_id"]
    at.text_input(key=f"comment-{second_id}").set_value("customer called").run()
    at.button(key=f"override-{second_id}").click().run()
    events = read_log(log.path)
    assert [e.seq for e in events] == [0, 1]
    assert events[1].prev_hash == events[0].hash
    assert events[1].payload.comment == "customer called"  # type: ignore[union-attr]
    assert any("Chain verified: 2 events" in s.value for s in at.success)
    assert demo.session_bytes(log) == log.path.read_bytes()


@pytest.mark.safety
def test_demo_requirements_are_pinned_to_the_lock_and_hold_no_cloud_client():
    """DEM-04/DEM-05: the deployed page installs only pinned packages from uv.lock."""
    import tomllib

    lock = tomllib.load((ROOT / "uv.lock").open("rb"))
    locked = {p["name"]: p["version"] for p in lock["package"]}
    pins = {}
    for line in (ROOT / "demo" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split(" ;")[0].strip()
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            assert version, f"{line!r} is not pinned"
            pins[name.lower().replace("_", "-")] = version
    assert {"streamlit", "pydantic", "pyyaml", "markdown-it-py"} <= set(pins)
    for name, version in pins.items():
        assert locked[name] == version, name
    banned = {"anthropic", "boto3", "botocore", "openai", "evalkit-harness", "awscrt"}
    assert not banned & set(pins)
    assert "replay-only" in (ROOT / "demo" / "README.md").read_text(encoding="utf-8").lower()


# ---------- the content guard in demo-cache ----------


def _guard_file(tmp: Path, term: str) -> Path:
    path = tmp / "guard.local"
    path.write_text(f"# test terms\n{term}\n", encoding="utf-8")
    return path


@pytest.mark.contract
def test_demo_cache_skips_guarded_cases_and_counts_them(tmp_path):
    """A guarded term in a merchant name (case 4) or a reason (case 5) never reaches the file."""
    run, alerts = _write_run(tmp_path)
    term = "zzgrdterm"
    rows = [json.loads(x) for x in alerts.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["case"]["alert_id"] == _id(4):
            row["merchant"] = f"The {term.upper()} Shop"
    alerts.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    decisions = run / "decisions.jsonl"
    text = decisions.read_text(encoding="utf-8").replace(
        '"reasons": ["Night-time purchase."]', f'"reasons": ["Night-time {term} purchase."]'
    )  # every decision, so every case; keep only case 5's by restoring the others
    lines = [json.loads(x) for x in text.splitlines()]
    for d in lines:
        if d["alert_id"] != _id(5):
            d["output"]["reasons"] = ["Night-time purchase."]
    decisions.write_text("".join(json.dumps(d) + "\n" for d in lines), encoding="utf-8")
    guard = _guard_file(tmp_path, term)
    out = tmp_path / "replay.jsonl"
    n, _, skipped = write_replay(run, alerts, out, guard_file=guard)
    assert skipped == 2 and n == 2  # cases 4 and 5 out; 1 and 3 kept (case 2 is auto-cleared)
    written = out.read_text(encoding="utf-8")
    assert term not in written.lower()
    assert [json.loads(x)["alert"]["case"]["alert_id"] for x in written.splitlines()] == [
        _id(3),
        _id(1),
    ]


@pytest.mark.contract
def test_demo_cache_guard_defaults_to_the_shared_term_list(tmp_path):
    """The default guard is fraudreview.content_guard's list, not a copy: a generic pattern in
    it (a home-directory path) is skipped with no guard file passed."""
    from fraudreview.content_guard import GENERIC_PRIVATE, private_patterns

    assert private_patterns(tmp_path / "missing") == GENERIC_PRIVATE
    run, alerts = _write_run(tmp_path)
    rows = [json.loads(x) for x in alerts.read_text(encoding="utf-8").splitlines()]
    rows[0]["merchant"] = "Shop at " + "/" + "Users" + "/x"
    alerts.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    _, skipped = build_replay_guarded(run, alerts)
    assert skipped == 1
