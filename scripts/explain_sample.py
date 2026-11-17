"""E2.2 sample: `explain.summarise` over one run folder's decisions with the matching baseline
cases and truth. Pure and read-only: no model calls, nothing written. Prints the summary as JSON;
alert ids are left out unless `--ids` is given.

    uv run python scripts/explain_sample.py results/eval2-500-7fec65d1 eval2
    uv run python scripts/explain_sample.py RUN_DIR SPLIT [--results RESULTS_DIR] [--ids]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fraudreview import explain
from fraudreview.policy import load_policy
from fraudreview.schemas import AlertCase, TriageDecision
from fraudreview.truth import AlertTruth


def _lines(path: Path) -> list[str]:
    return [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("split", help="cases_<split>.jsonl and truth_<split>.jsonl, e.g. eval2")
    ap.add_argument("--results", type=Path, default=None, help="folder holding baseline/")
    ap.add_argument("--ids", action="store_true", help="print the mismatch alert ids")
    args = ap.parse_args(argv)
    run_dir: Path = args.run_dir
    baseline = (args.results or run_dir.parent) / "baseline"
    decisions = [TriageDecision.model_validate_json(x) for x in _lines(run_dir / "decisions.jsonl")]
    cases = [
        AlertCase.model_validate_json(x) for x in _lines(baseline / f"cases_{args.split}.jsonl")
    ]
    truths = [
        AlertTruth.model_validate_json(x) for x in _lines(baseline / f"truth_{args.split}.jsonl")
    ]
    wanted = {d.alert_id for d in decisions}
    s = explain.summarise(
        decisions,
        [c for c in cases if c.alert_id in wanted],
        [t for t in truths if t.alert_id in wanted],
        load_policy(),
    )
    out = {k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in s.items()}
    picks = out.pop("mismatch_examples")
    out["mismatch_examples"] = list(picks) if args.ids else f"{len(picks)} picked"
    out["n_decisions"] = len(decisions)
    out["n_alerts"] = len(wanted)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
