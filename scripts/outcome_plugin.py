"""pytest plugin used by the build harness: record each test's real outcome as JSON.

Loaded with `-p outcome_plugin` (scripts/ on PYTHONPATH); writes to the path in the
FRAUD_OUTCOMES environment variable. Outcomes per node id: passed, failed, skipped, xfail,
xpass, error. A test proves something only when its outcomes are exactly {"passed"}: an
expected failure that passes (XPASS, strict or not) is never a pass (D-29, L-24). Nothing a
test prints can change this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_OUTCOMES: dict[str, set[str]] = {}


def pytest_runtest_logreport(report) -> None:  # type: ignore[no-untyped-def]
    if report.when != "call" and report.outcome == "passed":
        return  # setup and teardown that pass say nothing about the test
    outcome = report.outcome
    if hasattr(report, "wasxfail"):
        outcome = "xpass" if report.outcome == "passed" else "xfail"
    elif report.when != "call" and report.outcome == "failed":
        outcome = "error"
    _OUTCOMES.setdefault(report.nodeid, set()).add(outcome)


def pytest_collectreport(report) -> None:  # type: ignore[no-untyped-def]
    if report.failed:
        _OUTCOMES.setdefault(f"<collect>{report.nodeid}", set()).add("error")


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    path = os.environ.get("FRAUD_OUTCOMES")
    if path:
        data = {nid: sorted(o) for nid, o in sorted(_OUTCOMES.items())}
        data["<plugin>"] = [str(Path(__file__).resolve())]  # the harness checks which plugin ran
        Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
