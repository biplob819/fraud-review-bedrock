import hashlib
import itertools
import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fraudreview import schemas as s
from fraudreview.audit import AuditError, AuditLog, audit_view, canonical, read_log
from fraudreview.policy import load_policy
from fraudreview.router import RouterSettings, dispose
from tests.test_router import A1, A2, alert, decision

ROOT = Path(__file__).resolve().parent.parent
POLICY = load_policy(ROOT / "policy.md")
SETTINGS = RouterSettings(0.9, 0.7)
AT = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def filled_log(path: Path) -> AuditLog:
    log = AuditLog(path)
    d1, d2 = decision(action="pass"), decision(stage=2, action="hold")
    log.record_decision(d1, AT)
    log.record_decision(d2, AT)
    log.append(dispose(alert(), d1, d2, POLICY, SETTINGS), AT)
    log.record_decision(decision(alert_id=A2), AT)
    log.append(dispose(alert(A2), decision(alert_id=A2), None, POLICY, SETTINGS), AT)
    log.append(
        s.AnalystAction(alert_id=A1, action="confirm_fraud", comment=None, analyst="demo"), AT
    )
    return log


# ---------- RTE-04 audit log ----------


def test_chain_links_every_event(tmp_path):
    filled_log(tmp_path / "audit.jsonl")
    events = read_log(tmp_path / "audit.jsonl")
    assert [e.seq for e in events] == list(range(6))
    assert events[0].prev_hash == ""
    for prev, cur in itertools.pairwise(events):
        assert cur.prev_hash == prev.hash
    assert [e.kind for e in events] == [
        "decision",
        "decision",
        "disposition",
        "decision",
        "disposition",
        "analyst_action",
    ]


def test_hash_is_sha256_of_the_canonical_event_without_hash(tmp_path):
    filled_log(tmp_path / "audit.jsonl")
    for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        claimed = raw.pop("hash")
        body = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert hashlib.sha256(body.encode()).hexdigest() == claimed
        assert canonical({**raw, "hash": claimed}).decode() == line


def test_timestamps_are_stored_in_utc(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    ist = AT.astimezone(timezone(timedelta(hours=5, minutes=30)))
    event = log.record_decision(decision(), ist)
    assert event.at == AT and '"at":"2026-01-05T14:30:00Z"' in (tmp_path / "a.jsonl").read_text(
        encoding="utf-8"
    )


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


@pytest.mark.safety
@pytest.mark.parametrize(
    "tamper, needle",
    [
        (lambda ls: [ls[0], ls[1].replace('"hold"', '"escalate"'), *ls[2:]], "line 2: hash"),
        (lambda ls: [ls[0], *ls[2:]], "line 2: seq 2, expected 1"),
        (lambda ls: [ls[1], ls[0], *ls[2:]], "line 1: seq 1, expected 0"),
        (lambda ls: [*ls[:3], ls[2], *ls[3:]], "line 4: seq 2, expected 3"),
        (lambda ls: [*ls[:-1], ls[-1].rstrip("\n")], "incomplete"),
        (lambda ls: [ls[0], "not json\n", *ls[1:]], "line 2: not a valid audit event"),
        (
            lambda ls: [ls[0], ls[1].replace(",", ", ", 1), *ls[2:]],
            "line 2: the line is not in canonical form",
        ),
    ],
)
def test_any_edit_breaks_the_chain_and_names_the_line(tmp_path, tamper, needle):
    path = tmp_path / "audit.jsonl"
    filled_log(path)
    path.write_text("".join(tamper(_lines(path))), encoding="utf-8")
    with pytest.raises(AuditError, match=needle):
        read_log(path)


@pytest.mark.safety
def test_a_rehashed_edit_still_breaks_the_next_link(tmp_path):
    """Recomputing the edited event's own hash does not help: the next prev_hash no longer
    matches."""
    path = tmp_path / "audit.jsonl"
    filled_log(path)
    ls = _lines(path)
    raw = json.loads(ls[1])
    raw["payload"]["decision"]["output"]["action"] = "escalate"
    raw.pop("hash")
    raw["hash"] = hashlib.sha256(canonical(raw)).hexdigest()
    ls[1] = canonical(raw).decode() + "\n"
    path.write_text("".join(ls), encoding="utf-8")
    with pytest.raises(AuditError, match="line 3: prev_hash"):
        read_log(path)


def test_missing_or_empty_log_is_empty(tmp_path):
    assert read_log(tmp_path / "none.jsonl") == []
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert read_log(tmp_path / "empty.jsonl") == []


def test_reopening_verifies_and_continues_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    filled_log(path)
    log = AuditLog(path)
    event = log.record_decision(decision(decision_id="d-late"), AT)
    events = read_log(path)
    assert event.seq == 6 and event.prev_hash == events[5].hash and len(events) == 7
    path.write_text("".join(_lines(path)[1:]), encoding="utf-8")
    with pytest.raises(AuditError):
        AuditLog(path)


def test_concurrent_appends_keep_one_valid_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    def work(t: int) -> None:
        for i in range(25):
            log.record_decision(decision(decision_id=f"d-{t}-{i}"), AT)

    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    events = read_log(path)
    ids = {e.payload.decision.decision_id for e in events if isinstance(e.payload, s.DecisionEvent)}
    assert len(events) == 200 and len(ids) == 200


def test_audit_view_returns_one_alerts_events_in_order(tmp_path):
    filled_log(tmp_path / "audit.jsonl")
    events = read_log(tmp_path / "audit.jsonl")
    view = audit_view(events, A1)
    assert [e.seq for e in view] == [0, 1, 2, 5]
    assert all(e.alert_id == A1 for e in view)
    assert audit_view(events, "a-ffffffffffffff09") == ()


def test_the_log_is_only_ever_appended(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = filled_log(path)
    before = path.read_bytes()
    log.record_decision(decision(decision_id="d-more"), AT)
    assert path.read_bytes().startswith(before)


# ---------- verifier pass 1 regressions ----------


@pytest.mark.safety
def test_line_and_paragraph_separators_in_text_keep_the_log_readable(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    out = s.Stage1Output(
        action="pass",
        reasons=("ok\u2028fine", "next\u2029para"),
        policy_clauses=("P7.1",),
        fields_used=("amount",),
        confidence=0.5,
    )
    log.record_decision(
        s.TriageDecision.model_validate({**decision().model_dump(), "output": out}), AT
    )
    log.append(
        s.AnalystAction(
            alert_id=A1, action="override", comment="line one\u2028line two", analyst="demo"
        ),
        AT,
    )
    events = read_log(path)
    assert len(events) == 2 and path.read_bytes().isascii()
    AuditLog(path).record_decision(decision(decision_id="d-after"), AT)
    assert len(read_log(path)) == 3


@pytest.mark.safety
def test_a_non_ascii_byte_is_refused_naming_the_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    filled_log(path)
    data = bytearray(path.read_bytes())
    data[data.index(b"\n") + 10] = 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(AuditError, match="line 2: not ASCII"):
        read_log(path)


@pytest.mark.safety
def test_a_recorded_head_catches_deleted_trailing_lines_and_rewrites(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = filled_log(path)
    head = log.head
    assert head[0] == 5 and read_log(path, expect_head=head)
    path.write_text("".join(_lines(path)[:-2]), encoding="utf-8")
    assert len(read_log(path)) == 4  # a shortened chain is still self-consistent
    with pytest.raises(AuditError, match="ends at seq 3, expected seq 5"):
        read_log(path, expect_head=head)


def test_two_writers_on_one_file_keep_one_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    first, second = AuditLog(path), AuditLog(path)
    for i in range(5):
        first.record_decision(decision(decision_id=f"d-a{i}"), AT)
        second.record_decision(decision(decision_id=f"d-b{i}"), AT)
    assert [e.seq for e in read_log(path)] == list(range(10))


def test_a_failed_write_stops_further_appends(tmp_path, monkeypatch):
    import os

    log = AuditLog(tmp_path / "audit.jsonl")

    def fail(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        log.record_decision(decision(), AT)
    monkeypatch.undo()
    with pytest.raises(AuditError, match="earlier write or check failed"):
        log.record_decision(decision(decision_id="d-2"), AT)


# ---------- verifier pass 2 regressions: a writer never continues a damaged file ----------


def _three(path: Path) -> AuditLog:
    log = AuditLog(path)
    for i in range(3):
        log.record_decision(decision(decision_id=f"d-{i}"), AT)
    return log


@pytest.mark.safety
@pytest.mark.parametrize(
    "damage",
    [
        lambda p: p.write_text("".join(_lines(p)[:1]), encoding="utf-8"),  # cut short
        lambda p: p.unlink(),  # deleted
    ],
)
def test_a_writer_refuses_a_file_cut_short_or_deleted(tmp_path, damage):
    path = tmp_path / "audit.jsonl"
    log = _three(path)
    damage(path)
    with pytest.raises(AuditError, match="no longer holds"):
        log.record_decision(decision(decision_id="d-3"), AT)
    with pytest.raises(AuditError):
        _ = log.head


@pytest.mark.safety
def test_a_writer_refuses_a_same_size_replacement(tmp_path):
    path, other = tmp_path / "audit.jsonl", tmp_path / "other.jsonl"
    log = _three(path)
    alt = AuditLog(other)
    for i in range(3):
        alt.record_decision(decision(decision_id=f"d-{i}", alert_id=A2), AT)
    assert other.stat().st_size == path.stat().st_size
    path.write_bytes(other.read_bytes())
    with pytest.raises(AuditError, match="no longer holds"):
        log.record_decision(decision(decision_id="d-3"), AT)


def test_head_is_the_writers_own_and_survives_json(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _three(path)
    head = log.head
    assert head == (2, read_log(path)[-1].hash)
    assert read_log(path, expect_head=json.loads(json.dumps(head)))


def test_non_ascii_text_is_escaped_and_hashed_as_escaped(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).append(
        s.AnalystAction(
            alert_id=A1, action="override", comment="Caf\u00e9 \u20b9 50", analyst="demo"
        ),
        AT,
    )
    line = path.read_text(encoding="ascii").splitlines()[0]
    assert "\\u00e9" in line
    raw = json.loads(line)
    claimed = raw.pop("hash")
    body = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(body.encode()).hexdigest() == claimed


# ---------- verifier pass 3 regressions ----------


@pytest.mark.safety
@pytest.mark.parametrize("swap", ["replace", "in_place"])
def test_a_writer_refuses_an_edited_earlier_line_of_the_same_size(tmp_path, swap):
    import os

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(3):
        log.append(
            s.AnalystAction(alert_id=A1, action="override", comment=f"note {i}", analyst="demo"), AT
        )
    edited = path.read_bytes().replace(b"note 0", b"note X", 1)
    if swap == "replace":
        tmp = tmp_path / "edited.jsonl"
        tmp.write_bytes(edited)
        os.replace(tmp, path)
    else:
        path.write_bytes(edited)
    with pytest.raises(AuditError, match="line 1"):
        log.record_decision(decision(decision_id="d-new"), AT)
    with pytest.raises(AuditError, match="earlier write or check failed"):
        log.record_decision(decision(decision_id="d-new2"), AT)


def test_head_and_appends_from_two_writers_interleave_safely(tmp_path):
    path = tmp_path / "audit.jsonl"
    a, b = AuditLog(path), AuditLog(path)
    for i in range(10):
        a.record_decision(decision(decision_id=f"d-a{i}"), AT)
        assert b.head == (2 * i, read_log(path)[-1].hash)
        b.record_decision(decision(decision_id=f"d-b{i}"), AT)
        assert a.head[0] == 2 * i + 1
    assert len(read_log(path, expect_head=a.head)) == 20


def test_a_failed_check_stops_the_writer(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _three(path)
    good = path.read_bytes()
    with path.open(mode="ab") as f:
        f.write(b'{"partial":')
    with pytest.raises(AuditError, match="incomplete"):
        log.record_decision(decision(decision_id="d-3"), AT)
    path.write_bytes(good)
    with pytest.raises(AuditError, match="earlier write or check failed"):
        log.record_decision(decision(decision_id="d-4"), AT)


# ---------- verifier pass 4 regressions ----------


@pytest.mark.safety
def test_a_recorded_head_refuses_a_deleted_log(tmp_path):
    path = tmp_path / "audit.jsonl"
    head = _three(path).head
    path.unlink()
    with pytest.raises(AuditError, match="missing, but a head at seq 2"):
        read_log(path, expect_head=head)
    assert read_log(path, expect_head=(-1, "")) == []
