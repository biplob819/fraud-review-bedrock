"""Spend ledger (N1.3, BRK-06, Tech Spec §9): one JSON line per real run, appended in one write.

The ledger is public (`docs/measurements/spend-ledger.jsonl`), so a line holds only the run id,
provider, split, call count, the estimate and the actual cost. It never holds a key, account or
request id.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

LEDGER = Path("docs") / "measurements" / "spend-ledger.jsonl"


def append_spend(
    path: Path,
    *,
    run_id: str,
    provider: str,
    split: str,
    calls: int,
    estimate_usd: float,
    actual_usd: float,
    at: datetime | None = None,
    note: str | None = None,
) -> dict[str, object]:
    if calls < 0 or not (estimate_usd >= 0 and actual_usd >= 0):
        raise ValueError("calls and costs must be non-negative numbers")
    line = {
        "at": (at or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        "run_id": run_id,
        "provider": provider,
        "split": split,
        "calls": calls,
        "estimate_usd": round(estimate_usd, 6),
        "actual_usd": round(actual_usd, 6),
    }
    if note:
        line["note"] = note  # e.g. a line written after the run, or with no reservation
    data = (json.dumps(line, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)  # one write with O_APPEND: lines never interleave
    finally:
        os.close(fd)
    return line


class LedgerBusy(RuntimeError):
    """Another process holds the exclusive lock on this ledger."""


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for a ledger (a sibling `.lock` file) or raise `LedgerBusy`
    at once. A run that reads the spent total, spends, then appends must hold it throughout, or two
    runs can each see the same remaining cap and together pass it. The OS drops the lock if the
    process dies."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LedgerBusy(f"{path} is locked by another run") from exc
        yield
    finally:
        os.close(fd)  # closing releases the lock


def total_spend(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(
        json.loads(x)["actual_usd"] for x in path.read_text(encoding="utf-8").splitlines() if x
    )
