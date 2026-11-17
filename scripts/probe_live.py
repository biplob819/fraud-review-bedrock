"""EXP-08 live proof: the prompt-injection probe through the real `stages.triage_alert`.

NOT RUN BY THE BUILD. The coordinator runs it once, after the owner agrees to the spend.

    uv run python scripts/probe_live.py docs/measurements/probe-live.json
    uv run python scripts/probe_live.py docs/measurements/probe-live.json --hypothetical

Without `--hypothetical` it makes no model call: no free-text field reaches a prompt (`category`
is a closed list of 14 values), so the result is "no injection surface" and the script says so.

With `--hypothetical` it takes 20 dev alerts on which a no-clear clause holds (so a clear can
never be right), puts one of the 10 attack strings into each one's `category` (a validation-free
copy; the real schema would refuse it, so this stands in for a future free-text field), and runs
stage 1, the hand-off, stage 2 and the router on each. The pass criterion is that none is
auto-cleared. Scoring is EvalKit's `attack_outcome`.

Estimated cost: under 1 US dollar (20 alerts, two stages each at about 0.01 US dollar an alert on
the configured models; the run is stopped at the spend cap in `config.yaml`). One spend-ledger
line is appended. The record holds counts and outcome per attack name, no alert id.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from fraudreview import probe, stages
from fraudreview.cache import Store
from fraudreview.cli import ROOT, load_env_file
from fraudreview.config import load_config
from fraudreview.cost import SpendCap, estimate_run
from fraudreview.doctor import price_table
from fraudreview.ledger import append_spend
from fraudreview.llm import make_client
from fraudreview.policy import load_policy
from fraudreview.schemas import AlertCase, Disposition

N = 20


def main(out: Path, hypothetical: bool) -> int:
    policy = load_policy()
    surface = probe.surface()
    record: dict[str, object] = {
        "surface": [{"field": s.field, "free_text": s.free_text} for s in surface],
        "hypothetical": hypothetical,
    }
    if not hypothetical and not probe.free_text_fields():
        record |= {"verdict": "no_injection_surface", "planted": 0, "billed_usd": 0.0}
        out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(record))
        return 0

    load_env_file(ROOT / ".env")
    config = load_config(ROOT / "config.yaml")
    lines = (ROOT / "results" / "baseline" / "cases_dev.jsonl").read_text("utf-8").splitlines()
    cases = [AlertCase.model_validate_json(x) for x in lines if x.strip()]
    chosen = sorted(probe.no_clear_cases(cases, policy), key=lambda c: c.alert_id)[:N]
    if len(chosen) < N:
        print(f"only {len(chosen)} no-clear dev alerts; need {N}", file=sys.stderr)
        return 2
    prices = price_table(ROOT / "prices.yaml", config.provider)
    planned = estimate_run(len(chosen), prices, config).total_usd  # before any call (§9)
    cap = SpendCap(config.max_cost_usd)
    ctx = stages.Context(
        config=config,
        policy=policy,
        client=make_client(config),
        store=Store(ROOT / ".fraud" / "store"),
        cap=cap,
        prices=prices,
    )
    loop = asyncio.new_event_loop()
    calls = 0

    def run_fn(case: AlertCase) -> Disposition:
        nonlocal calls
        t = loop.run_until_complete(stages.triage_alert(case, ctx))
        calls += sum(0 if d.cache_hit else d.attempts for d in t.decisions)
        return t.disposition

    try:
        result = probe.scenario(chosen, run_fn, hypothetical=True)
    finally:
        append_spend(
            ROOT / "docs" / "measurements" / "spend-ledger.jsonl",
            run_id="e2.2-probe-live",
            provider=config.provider,
            split="dev",
            calls=calls,
            estimate_usd=planned,
            actual_usd=cap.actual,
            note="hypothetical planting through category; 20 no-clear dev alerts",
        )
        loop.close()
    record |= {
        "verdict": result.verdict,
        "planted": result.planted,
        "auto_cleared": result.auto_cleared,
        "passed": result.passed,
        "outcomes": dict(Counter(a.outcome for a in result.attempts)),
        "finals": dict(Counter(a.final for a in result.attempts)),
        "by_attack": {
            a.injection: {"final": a.final, "outcome": a.outcome} for a in result.attempts
        },
        "billed_usd": round(cap.actual, 6),
    }
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "by_attack"}))
    return 0 if result.passed else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EXP-08 live prompt-injection probe")
    ap.add_argument("out", type=Path)
    ap.add_argument("--hypothetical", action="store_true")
    args = ap.parse_args()
    sys.exit(main(args.out, args.hypothetical))
