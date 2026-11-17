"""E2.5 sample: EvalKit rule compliance and the strict value check over a run (no model call).

Reads `<run>/decisions.jsonl` and the matching cases file; prints counts and rates as JSON (no
alert or request id). Writes nothing unless an output path is given.

    uv run python scripts/evalkit_sample.py results/<run> results/baseline/cases_eval2.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from fraudreview import evalkit_bridge as b
from fraudreview.policy import load_policy
from fraudreview.schemas import AlertCase, Rate, TriageDecision


def _rate(r: Rate) -> dict[str, Any]:
    return {
        "k": r.k,
        "n": r.n,
        "point": round(r.point, 4),
        "lo": round(r.lo, 4),
        "hi": round(r.hi, 4),
    }


def _lines(path: Path) -> list[str]:
    return [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarise(run: Path, cases_path: Path) -> dict[str, Any]:
    decisions = [TriageDecision.model_validate_json(x) for x in _lines(run / "decisions.jsonl")]
    wanted = {d.alert_id for d in decisions}
    cases = [
        c for c in map(AlertCase.model_validate_json, _lines(cases_path)) if c.alert_id in wanted
    ]
    by_id = {c.alert_id: c for c in cases}
    missing = wanted - set(by_id)
    if missing:
        raise SystemExit(f"{len(missing)} decisions have no case in {cases_path.name}")
    rules = b.ruleset(load_policy())
    per_stage: dict[str, Counter[str]] = {}
    reasons: Counter[str] = Counter()
    for d in decisions:
        key = f"stage{d.stage}"
        tally = per_stage.setdefault(key, Counter())
        if d.output is None:
            tally["error"] += 1
            continue
        result = b.compliance(d, by_id[d.alert_id], rules)
        kind = "clear" if d.output.action == "clear" else "not_clear"
        tally[f"{kind}_n"] += 1
        tally[f"{kind}_compliant"] += result.value == 1.0
        for r in result.details["reasons"]:
            reasons[f"{kind}:{r}"] += 1
    values = b.value_shares(decisions, cases)
    return {
        "decisions": len(decisions),
        "alerts": len(wanted),
        "compliance": {k: dict(v) for k, v in sorted(per_stage.items())},
        "compliance_reasons": dict(reasons.most_common()),
        "reasons_total": values["n"],
        "values_verified": values["verified"],
        "values_contradicted": values["contradicted"],
        "values_unverifiable": values["unverifiable"],
        "values_verified_share": _rate(values["verified_share"]),
        "values_contradicted_share": _rate(values["contradicted_share"]),
    }


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    out = summarise(Path(argv[0]), Path(argv[1]))
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if len(argv) == 3:
        Path(argv[2]).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
