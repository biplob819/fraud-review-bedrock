"""The analyst review screen's logic (Tech Spec §4.4, §7, D4.1). Replay only (D-15).

`queue` reads held and escalated cases from the committed `demo/replay.jsonl`, priority first.
`review` appends an `AnalystAction` to a session's own hash-chained audit log, which lives in a
temporary file and goes away with the session; the visitor can download it. `policy_text` gives
a clause's title and plain-language rule for the screen.

There is no live mode here: this package imports no model client, no cloud SDK and no pipeline
stage (`fraudreview.llm`, `fraudreview.stages`, `anthropic`, `boto3`, `botocore`), and reads no
credential. A test walks the import closure to keep it that way (DEM-04).
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fraudreview.audit import AuditLog, read_log
from fraudreview.policy import DEFAULT_POLICY, HEADING_RE, load_policy
from fraudreview.schemas import AnalystAction, AuditEvent
from fraudreview.truth import Alert

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPLAY = ROOT / "demo" / "replay.jsonl"
QUEUE_ORDER = {"priority": 0, "standard": 1}
Action = Literal["confirm_fraud", "clear", "override"]
ACTIONS: tuple[Action, ...] = ("confirm_fraud", "clear", "override")  # no block action (§7)
DEMO_ANALYST = "demo-analyst"


def queue(path: Path | str = DEFAULT_REPLAY) -> list[dict[str, Any]]:
    """Held and escalated cases from the replay file: priority first, then standard, each by
    risk score (highest first). Each record's Alert view is validated; auto-cleared or
    unqueued records are refused, since they never reach a person."""
    path = Path(path)
    records: list[dict[str, Any]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        Alert.model_validate_json(json.dumps(record["alert"]))
        if record["disposition"]["queue"] not in QUEUE_ORDER:
            raise ValueError(f"{path}: line {n}: not a held or escalated case")
        records.append(record)
    records.sort(
        key=lambda r: (
            QUEUE_ORDER[r["disposition"]["queue"]],
            -r["alert"]["case"]["risk_score"],
            r["alert"]["case"]["alert_id"],
        )
    )
    return records


def session_log(directory: Path | str | None = None) -> AuditLog:
    """A new, empty audit log for one visitor session, in a temporary folder."""
    folder = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="fraud-demo-"))
    return AuditLog(folder / "session-audit.jsonl")


def review(
    alert_id: str,
    action: Action,
    comment: str | None,
    log: AuditLog,
    analyst: str = DEMO_ANALYST,
    at: datetime | None = None,
) -> AuditEvent:
    """Record the analyst's call on one case in the session's audit chain. An override needs a
    comment; a blank comment counts as none."""
    comment = comment.strip() if comment and comment.strip() else None
    payload = AnalystAction(alert_id=alert_id, action=action, comment=comment, analyst=analyst)
    return log.append(payload, at or datetime.now(UTC))


def session_events(log: AuditLog) -> list[AuditEvent]:
    """The session's events, with the whole chain verified."""
    return read_log(log.path)


def session_bytes(log: AuditLog) -> bytes:
    """The session log as written (canonical JSON lines), for the download button."""
    read_log(log.path)  # verify before handing it out
    return log.path.read_bytes() if log.path.exists() else b""


@lru_cache(maxsize=4)
def _clause_texts(policy_path: Path) -> dict[str, tuple[str, str]]:
    """clause id -> (title, prose) from a validated policy file."""
    policy = load_policy(policy_path)  # refuses a malformed policy
    texts: dict[str, tuple[str, str]] = {}
    current: str | None = None
    prose: list[str] = []
    in_code = False
    for line in [*policy.text.split("\n"), "## end"]:
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if line.startswith("## "):
            if current is not None:
                texts[current] = (policy.clauses[current].title, " ".join(prose).strip())
            m = HEADING_RE.match(line[3:])
            current = m.group(1) if m and m.group(1) in policy.clauses else None
            prose = []
        elif current is not None and line.strip():
            prose.append(line.strip())
    return texts


def policy_title(clause_id: str, policy_path: Path | str = DEFAULT_POLICY) -> str | None:
    """A clause's title, or None when the current policy has no such clause."""
    found = _clause_texts(Path(policy_path).resolve()).get(clause_id)
    return found[0] if found else None


def policy_text(clause_id: str, policy_path: Path | str = DEFAULT_POLICY) -> str:
    """'P3.9 Never auto-clear extreme spend: A purchase at ten or more ...', or a note that the
    clause is not in the policy (the screen still shows what the agent cited)."""
    texts = _clause_texts(Path(policy_path).resolve())
    if clause_id not in texts:
        return f"{clause_id}: not a clause of the current policy"
    title, prose = texts[clause_id]
    return f"{clause_id} {title}: {prose}"
