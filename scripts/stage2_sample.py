"""A2.4 live proof: `stages.triage_alert` on the first N dev alerts (stage 1 replays from the
A2.3 cache; stage 2 is billed). Records dispositions, refusal reasons, error counts, latency per
stage over live calls, tokens and cost; no alert or request id. One spend-ledger line.

    uv run python scripts/stage2_sample.py 10 docs/measurements/stage2-sample.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from fraudreview import metrics, stages
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
    prices = price_table(ROOT / "prices.yaml", config.provider)
    planned = estimate_run(len(cases), prices, config).total_usd  # before any call (§9)
    cap = SpendCap(config.max_cost_usd)
    ctx = stages.Context(
        config=config,
        policy=load_policy(),
        client=make_client(config),
        store=Store(ROOT / ".fraud" / "store"),
        cap=cap,
        prices=prices,
    )
    results, calls = [], 0
    try:
        for c in cases:
            t = await stages.triage_alert(c, ctx)
            calls += sum(0 if d.cache_hit else d.attempts for d in t.decisions)
            results.append(t)
    finally:
        append_spend(
            ROOT / "docs" / "measurements" / "spend-ledger.jsonl",
            run_id="a2.4-stage2-sample",
            provider=config.provider,
            split="dev",
            calls=calls,
            estimate_usd=planned,
            actual_usd=cap.actual,
            note="stage 1 replays from the cache when unchanged; the estimate plans both stages",
        )
    decisions = [d for t in results for d in t.decisions]
    rows = []
    for t in results:
        s2 = t.decisions[1] if len(t.decisions) > 1 else None
        rows.append(
            {
                "stage1_action": t.decisions[0].output.action if t.decisions[0].output else None,
                "stage2_action": (s2.output.action if s2 and s2.output else None),
                "stage2_error": s2.error if s2 else None,
                "stage2_confidence": (s2.output.confidence if s2 and s2.output else None),
                "final": t.disposition.final,
                "decided_at_stage": t.disposition.decided_at_stage,
                "refusals": list(t.disposition.router_refusals),
                "stage2_cost_usd": round(s2.cost_usd, 6) if s2 and not s2.cache_hit else 0.0,
            }
        )
    lat = metrics.latency(decisions)
    record = {
        "provider": config.provider,
        "models": {"stage1": config.stage1.model, "stage2": config.stage2.model},
        "n": len(cases),
        "dispositions": dict(Counter(r["final"] for r in rows)),
        "errors": stages.error_counts(decisions),
        "latency_ms": {k: v.model_dump() for k, v in lat.items()},
        "stage1_cache_hits": sum(t.decisions[0].cache_hit for t in results),
        "stage2_cost_usd": round(sum(r["stage2_cost_usd"] for r in rows), 6),
        "total_billed_usd": round(cap.actual, 6),
        "rows": rows,
    }
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "rows"}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]), Path(sys.argv[2]))))
