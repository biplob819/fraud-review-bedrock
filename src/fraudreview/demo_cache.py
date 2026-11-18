"""`fraud demo-cache`: the replay file for the analyst review screen (D4.1, DEM-01, D-15).

Reads a finished run folder (`decisions.jsonl`, `dispositions.jsonl`) and the baseline's Alert
views (`results/baseline/alerts_<split>.jsonl`: case, truth with SHAP, merchant, timestamp) and
writes one JSON line per alert that reached a person: held alerts (priority queue) first, then
escalated ones (standard queue), each by risk score, highest first. Auto-cleared alerts are left
out. Each line carries the Alert view, the agent's decisions (action, reasons, clauses, fields,
confidence) and the disposition with its router refusals. Request ids, tokens, costs and hashes
of the provider call are not copied: the demo shows the decision, not the call.

A case whose merchant name or any other text matches the public-content guard
(`fraudreview.content_guard`) is skipped and counted, so a guarded term never reaches the file.

Every input line is validated against its schema, so the replay file only holds records the
pipeline itself could have written.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from fraudreview.content_guard import find_private
from fraudreview.schemas import SCHEMA_VERSION, Disposition, TriageDecision
from fraudreview.truth import Alert

DEFAULT_OUT = Path("demo") / "replay.jsonl"
MAX_BYTES = 2_000_000  # the committed replay file stays small (D4.1 brief)
QUEUE_ORDER = {"priority": 0, "standard": 1}


class DemoCacheError(Exception):
    """An input is missing, malformed or inconsistent; the message names the file and line."""


def _lines(path: Path) -> Iterable[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DemoCacheError(f"{path}: cannot read ({exc.strerror})") from exc
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            yield n, line


def _read[T](path: Path, parse: Callable[[str], T]) -> list[T]:
    out: list[T] = []
    for n, line in _lines(path):
        try:
            out.append(parse(line))
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            raise DemoCacheError(f"{path}: line {n}: not a valid record ({exc})") from exc
    return out


def decision_view(d: TriageDecision) -> dict[str, Any]:
    """What the screen shows of one stage's decision; no request id, tokens or cost."""
    out = d.output
    return {
        "decision_id": d.decision_id,
        "stage": d.stage,
        "model": d.model,
        "effort": d.effort,
        "policy_version": d.policy_version,
        "action": out.action if out else None,
        "reasons": list(out.reasons) if out else [],
        "policy_clauses": list(out.policy_clauses) if out else [],
        "fields_used": list(out.fields_used) if out else [],
        "confidence": out.confidence if out else None,
        "error": d.error,
    }


def _strings(value: Any) -> Iterable[str]:
    """Every string in a JSON-like value: keys and leaves."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def build_replay(run_dir: Path, alerts_path: Path) -> list[dict[str, Any]]:
    """The replay records for one run, in queue order (priority, then standard, then risk)."""
    return build_replay_guarded(run_dir, alerts_path)[0]


def build_replay_guarded(
    run_dir: Path, alerts_path: Path, guard_file: Path | None = None
) -> tuple[list[dict[str, Any]], int]:
    """(records in queue order, number of cases skipped because their text is guarded)."""
    decisions = _read(run_dir / "decisions.jsonl", TriageDecision.model_validate_json)
    dispositions = _read(run_dir / "dispositions.jsonl", Disposition.model_validate_json)
    alerts = _read(alerts_path, Alert.model_validate_json)
    by_decision = {d.decision_id: d for d in decisions}
    by_alert = {a.case.alert_id: a for a in alerts}
    if len(by_decision) != len(decisions):
        raise DemoCacheError(f"{run_dir / 'decisions.jsonl'}: a decision id repeats")
    if len({d.alert_id for d in dispositions}) != len(dispositions):
        raise DemoCacheError(f"{run_dir / 'dispositions.jsonl'}: an alert has two dispositions")
    records: list[dict[str, Any]] = []
    skipped = 0
    for disp in dispositions:
        if disp.final == "auto_cleared" or disp.queue is None:
            continue  # never reached a person; not part of the review queue
        alert = by_alert.get(disp.alert_id)
        if alert is None:
            raise DemoCacheError(f"{alerts_path}: no Alert view for queued alert {disp.alert_id}")
        views = []
        for decision_id in disp.decision_ids:
            d = by_decision.get(decision_id)
            if d is None or d.alert_id != disp.alert_id:
                raise DemoCacheError(
                    f"{run_dir}: disposition of {disp.alert_id} names decision {decision_id}, "
                    "which is not in decisions.jsonl for that alert"
                )
            views.append(decision_view(d))
        record = {
            "schema_version": SCHEMA_VERSION,
            "data_label": "simulated",
            "alert": alert.model_dump(mode="json"),
            "decisions": sorted(views, key=lambda v: v["stage"]),
            "disposition": {
                "final": disp.final,
                "queue": disp.queue,
                "decided_at_stage": disp.decided_at_stage,
                "router_refusals": list(disp.router_refusals),
            },
        }
        if find_private(_strings(record), guard_file) is not None:
            skipped += 1  # the matched text is not echoed: it is private
            continue
        records.append(record)
    records.sort(key=order_key)
    return records, skipped


def order_key(record: dict[str, Any]) -> tuple[int, float, str]:
    """Priority queue first, then standard; highest risk first; alert id breaks ties."""
    case = record["alert"]["case"]
    return (QUEUE_ORDER[record["disposition"]["queue"]], -case["risk_score"], case["alert_id"])


def to_line(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def write_replay(
    run_dir: Path,
    alerts_path: Path,
    out: Path = DEFAULT_OUT,
    max_cases: int | None = None,
    guard_file: Path | None = None,
) -> tuple[int, int, int]:
    """Write the replay file; returns (cases written, bytes, cases skipped by the content
    guard). `max_cases` keeps the first n in queue order, so every held case is kept before
    any escalated one."""
    records, skipped = build_replay_guarded(run_dir, alerts_path, guard_file)
    if max_cases is not None:
        records = records[:max_cases]
    data = "".join(to_line(r) + "\n" for r in records).encode("ascii")
    if len(data) > MAX_BYTES:
        raise DemoCacheError(
            f"{out}: {len(data)} bytes is over the {MAX_BYTES} limit; pass a smaller --max-cases"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return len(records), len(data), skipped
