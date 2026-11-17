"""N1.3 live proof (BRK-08): one call per stage through `llm.call_model` with the real stage
schemas, on one fixed synthetic alert. Records whether the API accepted each schema and the reply
validated, with tokens and cost; never a key or request id. About a cent.

    uv run python scripts/live_schema_check.py docs/measurements/structured-schemas.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fraudreview.cli import ROOT, load_env_file
from fraudreview.config import load_config
from fraudreview.cost import SpendCap
from fraudreview.doctor import price_table
from fraudreview.ledger import append_spend
from fraudreview.llm import billed_cost, build_body, call_model, make_client, worst_case_cost
from fraudreview.schemas import Stage1Output, Stage2Output

ALERT = {
    "alert_id": "a-0123456789abcdef",
    "ts_hour": 23,
    "ts_dow": 3,
    "category": "shopping_net",
    "amount": 912.4,
    "features": {
        "amt_vs_usual": 11.2,
        "hour": 23,
        "category_freq": 0.01,
        "merchant_first_seen": True,
        "card_history_n": 40,
    },
    "risk_score": 0.93,
}
SYSTEM = (
    "You triage card-fraud alerts for a review team. You recommend; people decide. Cite only "
    "policy clause ids from the policy below and only fields present in the alert. "
    "Return the decision record and nothing else."
)


async def main(out: Path) -> int:
    load_env_file(ROOT / ".env")
    config = load_config(ROOT / "config.yaml")
    prices = price_table(ROOT / "prices.yaml", config.provider)
    policy = (ROOT / "policy.md").read_text(encoding="utf-8")
    client = make_client(config)
    record: dict[str, object] = {"provider": config.provider, "stages": {}}
    total = reserved_total = 0.0
    cap = SpendCap(config.max_cost_usd)
    attempts = 0
    try:
        for stage, cfg, model in (
            (1, config.stage1, Stage1Output),
            (2, config.stage2, Stage2Output),
        ):
            body = build_body(cfg, [SYSTEM, policy], json.dumps(ALERT), model)
            reserve = worst_case_cost(body, prices[cfg.model])
            reserved_total += reserve
            token = await cap.reserve(reserve)
            result = await call_model(client, body, model)
            attempts += result.attempts
            usd = billed_cost(result.tokens, result.unmetered, body, prices[cfg.model])
            await cap.commit(token, usd)
            total += usd
            record["stages"][f"stage{stage}"] = {  # type: ignore[index]
                "model": cfg.model,
                "effort": cfg.effort,
                "schema": model.__name__,
                "accepted_and_valid": result.error is None and isinstance(result.output, model),
                "error": result.error,
                "attempts": result.attempts,
                "action": getattr(result.output, "action", None),
                "tokens": result.tokens.model_dump(),
                "cost_usd": round(usd, 6),
            }
    finally:  # a line for whatever was billed, even if a later stage failed
        append_spend(
            ROOT / "docs" / "measurements" / "spend-ledger.jsonl",
            run_id="n1.3-schema-check",
            provider=config.provider,
            split="smoke",
            calls=attempts,
            estimate_usd=reserved_total,
            actual_usd=total,
        )
    record["total_cost_usd"] = round(total, 6)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ok = all(s["accepted_and_valid"] for s in record["stages"].values())  # type: ignore[union-attr]
    print(f"{'ok' if ok else 'FAILED'}: wrote {out} (about ${total:.4f})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
