"""E2.4 sample: the MON-03 drift check between the eval and eval2 alert samples, and the MON-02
alarm check for each committed eval run. Pure and read-only: no model calls, nothing written.

    uv run python scripts/monitor_sample.py
    uv run python scripts/monitor_sample.py --cases MAIN_CHECKOUT/results/baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fraudreview import monitor
from fraudreview.config import load_config
from fraudreview.run import FREEZE, load_cases

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "results" / "baseline"  # from a worktree, pass the main checkout's folder


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, default=CASES, help="folder with cases_*.jsonl")
    args = ap.parse_args(argv)

    reference = load_cases(args.cases, "eval")
    new = load_cases(args.cases, "eval2")
    print(f"drift: eval ({len(reference)} alerts) -> eval2 ({len(new)} alerts)")
    for name, (value, level) in monitor.drift(reference, new).items():
        print(f"  {name:<16} psi {value:.4f}  {level}")

    bands = load_config(ROOT / "config.yaml").alarms
    print("alarms:")
    for row in monitor.read_jsonl(ROOT / "docs" / "measurements" / "eval-runs.jsonl"):
        line = monitor.history_from_eval_row(row)
        alarms = monitor.check_bands(line, bands)
        text = ", ".join(f"{a.metric} {a.level} ({a.value:g})" for a in alarms) or "none"
        print(f"  {line['run_id']:<20} {text}")

    current = json.loads((ROOT / FREEZE).read_text(encoding="utf-8"))["digest"]
    reason = monitor.freeze_guard(ROOT / "docs" / "measurements" / "eval-runs.jsonl", current)
    print(f"freeze guard: {reason or 'ok'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
