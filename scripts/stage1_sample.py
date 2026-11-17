"""A2.3 live proof: stage 1 on the first N dev alerts through `stages.run_stage`, then the same
alerts again to show cache hits. Records actions, router acceptance, tokens and cost (no key,
request id or alert id), and appends one spend-ledger line. Under $0.02 for 10 alerts.

    uv run python scripts/stage1_sample.py 10 docs/measurements/stage1-sample.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from fraudreview import stages
from fraudreview.cache import Store
from fraudreview.cli import ROOT, load_env_file
from fraudreview.config import load_config
from fraudreview.cost import SpendCap, estimate_run
from fraudreview.doctor import price_table
from fraudreview.ledger import append_spend
from fraudreview.llm import make_client
from fraudreview.policy import load_policy
from fraudreview.schemas import AlertCase


async def main(n: int, out: Path) -> int:
    load_env_file(ROOT / ".env")
    config = load_config(ROOT / "config.yaml")
    lines = (ROOT / "results" / "baseline" / "cases_dev.jsonl").read_text("utf-8").splitlines()
    cases = [AlertCase.model_validate_json(x) for x in lines if x.strip()][:n]
    cap = SpendCap(config.max_cost_usd)
    ctx = stages.Context(
        config=config,
        policy=load_policy(),
        client=make_client(config),
        store=Store(ROOT / ".fraud" / "store"),
        cap=cap,
        prices=price_table(ROOT / "prices.yaml", config.provider),
    )
    prices = price_table(ROOT / "prices.yaml", config.provider)
    planned = estimate_run(len(cases), prices, config).stage1_usd  # before any call (§9)
    first, again, calls = [], [], 0
    try:
        for c in cases:
            d = await stages.run_stage(1, c, ctx)
            calls += 0 if d.cache_hit else d.attempts
            first.append((d, stages.needs_stage2(d, c, ctx)))
        for c in cases:  # the rerun: every call should be a cache hit
            d = await stages.run_stage(1, c, ctx)
            calls += 0 if d.cache_hit else d.attempts
            again.append(d)
    finally:  # one line for everything the cap recorded, rerun included
        append_spend(
            ROOT / "docs" / "measurements" / "spend-ledger.jsonl",
            run_id="a2.3-stage1-sample",
            provider=config.provider,
            split="dev",
            calls=calls,
            estimate_usd=planned,
            actual_usd=cap.actual,
        )
    rows = [
        {
            "action": d.output.action if d.output else None,
            "error": d.error,
            "clauses": list(d.output.policy_clauses) if d.output else [],
            "to_stage2": to2,
            "tokens": d.tokens.model_dump(),
            "cost_usd": round(d.cost_usd, 6),
            "cache_hit_first": d.cache_hit,
        }
        for d, to2 in first
    ]
    record = {
        "provider": config.provider,
        "model": config.stage1.model,
        "n": len(cases),
        "actions": dict(Counter(r["action"] or r["error"] for r in rows)),
        "accepted_clears": sum(not r["to_stage2"] for r in rows),
        "to_stage2": sum(r["to_stage2"] for r in rows),
        "rerun_cache_hits": sum(d.cache_hit for d in again),
        "cost_usd": round(cap.actual, 6),
        "rows": rows,
    }
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "rows"}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]), Path(sys.argv[2]))))
