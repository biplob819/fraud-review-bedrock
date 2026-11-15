"""Append-only, hash-chained audit log (Tech Spec §3, D-12, H3).

One JSON line per `AuditEvent`. Each event's `hash` is the sha256 of the canonical JSON of the
event without its `hash` field (keys sorted, no spaces, ASCII with escapes, timestamps in UTC),
and `prev_hash` is the previous event's hash. `read_log` checks the whole chain and names the
first bad line, so an edited, removed, reordered or inserted line is caught, as is a cut-off
last line. A chain is only self-consistent: deleting whole lines from the end, or rewriting a
suffix with fresh hashes, is caught only against a head (seq and hash) recorded elsewhere,
which `read_log(path, expect_head=...)` checks; the run summary records it.

`AuditLog` only ever opens the file to append, takes an exclusive file lock for each append
(so two instances or processes cannot fork the chain), flushes and fsyncs each line, and
refuses further appends after a failed write.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading

try:  # POSIX advisory lock; the project runs on Linux and macOS
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from pydantic import TypeAdapter, ValidationError

from fraudreview.schemas import (
    AnalystAction,
    AuditEvent,
    DecisionEvent,
    Disposition,
    Timestamp,
    TriageDecision,
)

Payload = DecisionEvent | Disposition | AnalystAction
_TIMESTAMP: TypeAdapter[datetime] = TypeAdapter(Timestamp)


class AuditError(Exception):
    """The audit log is malformed or has been altered; the message names the line."""


def canonical(fields: dict[str, Any]) -> bytes:
    """Canonical JSON bytes: sorted keys, no spaces, ASCII with escapes."""
    # ASCII with escapes: U+2028/U+2029 and other non-ASCII text cannot split a line
    return json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def event_hash(event: AuditEvent) -> str:
    """The hash an event must carry: sha256 over its canonical JSON without `hash`."""
    return hashlib.sha256(canonical(event.model_dump(mode="json", exclude={"hash"}))).hexdigest()


def _alert_of(payload: Payload) -> str:
    return payload.decision.alert_id if isinstance(payload, DecisionEvent) else payload.alert_id


def build_event(seq: int, at: datetime, payload: Payload, prev_hash: str) -> AuditEvent:
    """Make the next event of a chain, with its hash computed over the validated content."""
    at = _TIMESTAMP.validate_python(at)  # zone required, normalised to UTC
    fields = {
        "schema_version": "1.0",
        "seq": seq,
        "at": _TIMESTAMP.dump_python(at, mode="json"),
        "alert_id": _alert_of(payload),
        "payload": payload.model_dump(mode="json"),
        "prev_hash": prev_hash,
    }
    digest = hashlib.sha256(canonical(fields)).hexdigest()
    event = AuditEvent.model_validate_json(json.dumps({**fields, "hash": digest}))
    if event_hash(event) != event.hash:  # the validated event must serialise as it was hashed
        raise AuditError("event hash is not stable")
    return event


def read_log(
    path: Path | str, expect_head: tuple[int, str] | list[Any] | None = None
) -> list[AuditEvent]:
    """Read and verify a whole log; an empty or missing file is an empty log. With
    `expect_head` (last seq and hash, recorded elsewhere), a log that ends anywhere else is
    refused, which catches deleted trailing lines and a rewritten suffix."""
    path = Path(path)
    if not path.exists():
        want_seq = int(expect_head[0]) if expect_head is not None else -1
        if want_seq >= 0:
            raise AuditError(f"{path}: missing, but a head at seq {want_seq} was recorded")
        return []
    events = _parse(path.read_bytes(), path)
    if expect_head is not None:
        head = (events[-1].seq, events[-1].hash) if events else (-1, "")
        if head != tuple(expect_head):
            raise AuditError(
                f"{path}: ends at seq {head[0]}, expected seq {expect_head[0]} with the recorded "
                "hash (lines were removed or rewritten)"
            )
    return events


def _parse(data: bytes, path: Path) -> list[AuditEvent]:
    """Verify a log's bytes and return its events (see `read_log`)."""
    if data and not data.endswith(b"\n"):
        last = data.count(b"\n") + 1
        raise AuditError(f"{path}: line {last}: the last line is incomplete (a write was cut off)")
    events: list[AuditEvent] = []
    prev = ""
    for n, raw_line in enumerate(data[:-1].split(b"\n") if data else [], 1):
        where = f"{path}: line {n}"
        try:
            line = raw_line.decode("ascii")
            event = AuditEvent.model_validate_json(line)
        except UnicodeDecodeError as exc:
            raise AuditError(f"{where}: not ASCII (the log is written with escapes)") from exc
        except ValidationError as exc:
            raise AuditError(
                f"{where}: not a valid audit event ({exc.errors()[0]['msg']})"
            ) from exc
        if event.seq != len(events):
            raise AuditError(f"{where}: seq {event.seq}, expected {len(events)}")
        if event.prev_hash != prev:
            raise AuditError(f"{where}: prev_hash does not match the previous event")
        if event_hash(event) != event.hash:
            raise AuditError(f"{where}: hash does not match the event's content")
        if raw_line != canonical(event.model_dump(mode="json")):
            raise AuditError(f"{where}: the line is not in canonical form")
        events.append(event)
        prev = event.hash
    return events


class AuditLog:
    """Append events to a log file. Opening an existing log verifies it and continues it.

    Every operation holds the exclusive file lock and works from one read of the file. After
    each of its own writes the writer records the file's fingerprint (inode, size, modification
    time); if the fingerprint is unchanged nothing has touched the file, otherwise the whole
    file is verified and must still hold this writer's chain (it may only have grown by another
    writer's valid events). Any failed write or check stops the writer."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._broken = False
        self._seq = 0
        self._prev = ""
        self._fingerprint: tuple[int, int, int] | None = None
        with self._locked() as f:
            self._load(f)  # refuse to continue a bad log

    @contextmanager
    def _locked(self) -> Iterator[BinaryIO]:
        with self.path.open(mode="a+b") as f:  # appends only; reads for verification
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # one writer across processes
            try:
                try:  # the path must still name the file we locked (not deleted or replaced)
                    same = os.stat(self.path).st_ino == os.fstat(f.fileno()).st_ino
                except FileNotFoundError:
                    same = False
                if not same:
                    self._broken = True
                    raise AuditError(f"{self.path}: the log was deleted or replaced while open")
                yield f
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _stat(f: BinaryIO) -> tuple[int, int, int]:
        st = os.fstat(f.fileno())
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def _load(self, f: BinaryIO) -> None:
        f.seek(0)
        events = _parse(f.read(), self.path)
        if self._seq and (len(events) < self._seq or events[self._seq - 1].hash != self._prev):
            raise AuditError(
                f"{self.path}: line {min(self._seq, len(events) + 1)}: the log no longer holds "
                "this writer's chain (cut short, deleted, replaced or edited)"
            )
        self._seq = len(events)
        self._prev = events[-1].hash if events else ""
        self._fingerprint = self._stat(f)

    def _check(self, f: BinaryIO) -> None:
        if self._broken:
            raise AuditError(f"{self.path}: an earlier write or check failed; check the log")
        try:
            if self._stat(f) != self._fingerprint:  # someone else touched the file
                self._load(f)
        except AuditError:
            self._broken = True
            raise

    @property
    def head(self) -> tuple[int, str]:
        """Last seq and hash of this writer's verified chain, to record outside the log and
        check later with `read_log(expect_head=...)`."""
        with self._lock, self._locked() as f:
            self._check(f)
            return (self._seq - 1, self._prev)

    def append(self, payload: Payload, at: datetime) -> AuditEvent:
        with self._lock, self._locked() as f:
            self._check(f)
            event = build_event(self._seq, at, payload, self._prev)
            line = canonical(event.model_dump(mode="json")) + b"\n"
            try:
                f.seek(0, os.SEEK_END)
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            except OSError:
                self._broken = True
                raise
            before = self._fingerprint[1] if self._fingerprint else 0
            after = self._stat(f)
            if after[1] != before + len(line):  # the file changed under the lock
                self._broken = True
                raise AuditError(f"{self.path}: the log changed during the write; check it")
            self._seq, self._prev = self._seq + 1, event.hash
            self._fingerprint = after
            return event

    def record_decision(self, decision: TriageDecision, at: datetime) -> AuditEvent:
        return self.append(DecisionEvent(decision=decision), at)


def audit_view(events: Iterable[AuditEvent], alert_id: str) -> tuple[AuditEvent, ...]:
    """One alert's events (decisions, its disposition, analyst actions) in log order."""
    return tuple(e for e in events if e.alert_id == alert_id)
