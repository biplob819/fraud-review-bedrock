"""Spend ledger (N1.3, BRK-06, Tech Spec §9): one JSON line per real run, appended in one write.

The ledger is public (`docs/measurements/spend-ledger.jsonl`), so a line holds only the run id,
provider, split, call count, the estimate and the actual cost. It never holds a key, account or
request id.
"""

from __future__ import annotations

import json
import os
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


def total_spend(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(
        json.loads(x)["actual_usd"] for x in path.read_text(encoding="utf-8").splitlines() if x
    )
