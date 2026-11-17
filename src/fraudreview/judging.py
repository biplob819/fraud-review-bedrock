"""Two-judge faithfulness of the stage-2 explanations (E2.2, EXP-05, D-11, D-36, D-41).

Each stage-2 decision with an output becomes an EvalKit `FaithfulnessItem`: the question is one
fixed sentence, the evidence is the alert as one plain sentence per glossary field
(`evalkit_bridge.evidence`), and the explanation is the decision's reasons joined. EvalKit's
`faithfulness` extracts the explanation's claims with its extractor model and judges every claim
against all the evidence with two judges from two model families.

Models are EvalKit's own reference configuration (`configs/pubmedqa.yaml` in the EvalKit repo,
which is not packaged): judges `claude-opus-5-5` (Anthropic) and `gpt-6.1-sol` (OpenAI), extractor
`claude-haiku-5-5`. Prices come from EvalKit's packaged `prices.yaml`, not this repo's.
Keys are read from the environment only (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) and never printed.

- `build_items`: decisions + cases -> items. Ids are hashes of the decision id (which holds the
  alert id), so nothing in a claim id or a cache key names an alert.
- `estimate`: a dry-run cost from EvalKit's own estimator. No model is called.
- `judge`: refuses over the cap, runs live through EvalKit's executor (its spend guard, its
  content-addressed store, so a rerun is free) and writes one spend-ledger line in a `finally`.
- `summary`: per judge, the mean score and the share fully faithful with Wilson intervals,
  `judge_mode`, judge-error counts and the three lowest-scoring decisions.

Nothing at import time or in `build_items`, `estimate` or `summary` touches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from evalkit.adapters import ModelAdapter
from evalkit.adapters.base import BatchRequest, BatchResult, CompletionRequest, RawCompletion
from evalkit.cost import Price, TokenAssumptions, estimate_run_cost, estimate_tokens, load_prices
from evalkit.env import KEYS, missing_keys
from evalkit.pipeline.extract import FAITHFULNESS_EXTRACTOR_PROMPT, FAITHFULNESS_QUESTION_RULE
from evalkit.pipeline.generate import provider_for
from evalkit.pipeline.judge import JUDGE_RUBRIC
from evalkit.run import make_adapters
from evalkit.runtime import Executor
from evalkit.runtime.store import Store
from evalkit.schemas import RunConfig as EvalKitConfig
from evalkit.scorers.faithfulness import FaithfulnessItem, FaithfulnessResult, faithfulness

from fraudreview import evalkit_bridge
from fraudreview.ledger import LEDGER, append_spend
from fraudreview.schemas import AlertCase, TriageDecision
from fraudreview.stats import bootstrap_mean, wilson

QUESTION = "Should this card transaction alert be cleared, held or escalated, given the evidence?"
JUDGES = ("claude-opus-5-5", "gpt-6.1-sol")  # two families: Anthropic and OpenAI
EXTRACTOR = "claude-haiku-5-5"
DEFAULT_CAP_USD = 10.0  # D-41: up to about $10 of judging is pre-approved
N_WORST = 3
SEED = 20261117
DEFAULT_STORE = Path(".fraud") / "judge-store"


class JudgingRefused(Exception):
    """The run was not started: the estimate is over the cap, or a key is missing."""


def eval_config(max_cost_usd: float | None = None) -> EvalKitConfig:
    """EvalKit's reference run configuration, for faithfulness only. `models` is required by the
    schema and unused here (no generation happens); effort is explicit, as EvalKit requires."""
    return EvalKitConfig(
        models=("claude-sonnet-5-5",),
        judges=JUDGES,
        extractor=EXTRACTOR,
        structured_output=True,
        max_output_tokens=2048,
        concurrency={"anthropic": 8, "openai": 8},
        seed=7,
        max_cost_usd=max_cost_usd,
        default_effort="medium",
        judge_effort="medium",
        extractor_effort="medium",
    )


# ------------------------------------------------------------------ items


def item_id(decision: TriageDecision) -> str:
    """No ":" (EvalKit's claim ids use it) and no alert id: a hash of the decision id."""
    return "d" + hashlib.sha256(decision.decision_id.encode("utf-8")).hexdigest()[:16]


def explanation_of(decision: TriageDecision) -> str | None:
    out = decision.output
    if decision.stage != 2 or out is None:
        return None
    return " ".join(r.strip() for r in out.reasons)


def build_items(
    decisions: Iterable[TriageDecision], cases: Iterable[AlertCase]
) -> list[FaithfulnessItem]:
    """One item per stage-2 decision that has an output, in decision order."""
    by_alert = {c.alert_id: c for c in cases}
    items: list[FaithfulnessItem] = []
    for d in decisions:
        text = explanation_of(d)
        if text is None:
            continue
        case = by_alert.get(d.alert_id)
        if case is None:
            raise KeyError(f"decision {d.decision_id} has no case in the cases file")
        items.append(
            FaithfulnessItem(
                id=item_id(d),
                question=QUESTION,
                explanation=text,
                evidence=tuple(evalkit_bridge.evidence(case)),
            )
        )
    return items


# ------------------------------------------------------------------ estimate


def _assumptions(items: Sequence[FaithfulnessItem], claims_per_item: float | None) -> Any:
    base = TokenAssumptions()
    n = max(1, len(items))
    mean_explanation = sum(estimate_tokens(i.question, i.explanation) for i in items) / n
    mean_evidence = sum(estimate_tokens(*i.evidence) for i in items) / n
    extractor_prompt = estimate_tokens(FAITHFULNESS_EXTRACTOR_PROMPT, FAITHFULNESS_QUESTION_RULE)
    judge_prompt = estimate_tokens(JUDGE_RUBRIC)
    return base.model_copy(
        update={
            "t_ext_in": math.ceil(extractor_prompt + mean_explanation),
            "t_j_in": math.ceil(judge_prompt + mean_evidence + 40),
            "claims_per_answer": claims_per_item or base.claims_per_answer,
        }
    )


def estimate(
    items: Sequence[FaithfulnessItem],
    config: EvalKitConfig | None = None,
    prices: dict[str, Price] | None = None,
    *,
    claims_per_item: float | None = None,
    cap_usd: float | None = None,
) -> dict[str, Any]:
    """EvalKit's own estimate for faithfulness on these items (no API call): one extractor call
    per item, then one live judge call per claim per judge. Token counts are EvalKit's planning
    averages, with the prompt sizes taken from these items. Raises `JudgingRefused` if the total
    is over `cap_usd`."""
    config = config or eval_config()
    prices = prices if prices is not None else load_prices()
    a = _assumptions(items, claims_per_item)
    est = estimate_run_cost(
        prices,
        models=config.models,
        extractor=config.extractor,
        judges=config.judges,
        n_cases=len(items),
        assumptions=a,
        batch_judges=False,  # live judging: full price, no batch discount
    )
    lines = [ln for ln in est.lines if ln.node != "generate"]  # nothing is generated here
    total = sum(ln.usd for ln in lines)
    out: dict[str, Any] = {
        "items": len(items),
        "claims_per_item": a.claims_per_answer,
        "claims": math.ceil(len(items) * a.claims_per_answer),
        "extractor": config.extractor,
        "judges": list(config.judges),
        "lines": [
            {"node": ln.node, "model": ln.model, "calls": ln.calls, "usd": round(ln.usd, 6)}
            for ln in lines
        ],
        "total_usd": round(total, 6),
        "cap_usd": cap_usd,
    }
    out["calls"] = sum(ln["calls"] for ln in out["lines"])
    if cap_usd is not None and out["total_usd"] > cap_usd:
        raise JudgingRefused(
            f"the judging estimate ${total:.2f} is over the ${cap_usd:g} cap; "
            "lower --max-items or raise --cap-usd"
        )
    return out


PLANNING_CLAIMS_PER_ITEM = 4.5  # EvalKit's default, measured on short PubMedQA answers
MEASURED_CLAIMS_PER_ITEM = 12.1  # measured on this project's explanations (OWNER D-43)


def format_estimate(est: Mapping[str, Any]) -> str:
    rows = [("step", "model", "calls", "usd")]
    rows += [(ln["node"], ln["model"], str(ln["calls"]), f"{ln['usd']:.4f}") for ln in est["lines"]]
    rows.append(("total", "", str(est["calls"]), f"{est['total_usd']:.4f}"))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    table = [
        f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:>{widths[2]}}  {r[3]:>{widths[3]}}"
        for r in rows
    ]
    head = (
        f"{est['items']} items, about {est['claims']} claims "
        f"({est['claims_per_item']:g} per item), priced for two judges; no model calls made"
    )
    cap = "" if est.get("cap_usd") is None else f" (cap ${est['cap_usd']:g})"
    lines = [head, *table, f"estimated total ${est['total_usd']:.2f}{cap}"]
    if est["claims_per_item"] <= PLANNING_CLAIMS_PER_ITEM + 1e-9:
        lines.append(
            f"WARNING: {est['claims_per_item']:g} claims per item is a planning default; this "
            f"project's explanations measured {MEASURED_CLAIMS_PER_ITEM:g}, so a real run costs "
            f"at least {MEASURED_CLAIMS_PER_ITEM / est['claims_per_item']:.1f}x this estimate "
            "(the Opus judge also cost about 1.5x its per-claim estimate). "
            "Pass --claims-per-item to plan with a measured figure."
        )
    return "\n".join(lines)


# ------------------------------------------------------------------ live judging


class _Metered(ModelAdapter):
    """Counts the calls an adapter makes (the ledger's `calls`) and delegates everything."""

    provider: ClassVar[str] = "metered"

    def __init__(self, inner: ModelAdapter, counter: list[int]) -> None:
        self.inner = inner
        self.counter = counter

    async def complete(self, req: CompletionRequest) -> RawCompletion:
        self.counter[0] += 1
        return await self.inner.complete(req)

    async def submit_batch(self, reqs: list[BatchRequest]) -> str:
        return await self.inner.submit_batch(reqs)

    async def collect_batch(
        self, batch_id: str, reqs: Mapping[str, CompletionRequest]
    ) -> list[BatchResult] | None:
        return await self.inner.collect_batch(batch_id, reqs)


def _check_keys(config: EvalKitConfig) -> None:
    providers = {provider_for(m, config.endpoints) for m in (config.extractor, *config.judges)}
    missing = missing_keys(providers)
    if missing:
        raise JudgingRefused(
            f"missing environment variables: {', '.join(missing)} "
            f"(expected {', '.join(sorted(KEYS.values()))})"
        )


def spent_for(ledger: Path, run_id: str | None = None) -> float:
    """Everything the ledger records for judging: every line of the `faithfulness` split, so a
    renamed run folder cannot start a fresh cap. Pass a run id to count just that run."""
    if not ledger.exists():
        return 0.0
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    return sum(
        float(r["actual_usd"])
        for r in rows
        if r.get("split") == "faithfulness" and (run_id is None or r.get("run_id") == run_id)
    )


async def judge_async(
    items: Sequence[FaithfulnessItem],
    *,
    cap_usd: float = DEFAULT_CAP_USD,
    store_dir: Path = DEFAULT_STORE,
    ledger_path: Path | None = None,
    run_id: str = "faithfulness",
    config: EvalKitConfig | None = None,
    prices: dict[str, Price] | None = None,
    adapters: Mapping[str, ModelAdapter] | None = None,
) -> list[FaithfulnessResult]:
    """Run `faithfulness` live. `adapters` is for tests (fakes); live runs build EvalKit's."""
    config = config or eval_config()
    prices = prices if prices is not None else load_prices()
    ledger = ledger_path or Path(LEDGER)
    # The cap is a TOTAL for judging across every invocation (a rerun that restarted the cap
    # caused OWNER D-43): everything the ledger holds for judging counts against the cap.
    spent_before = spent_for(ledger)
    remaining = cap_usd - spent_before
    if remaining <= 0:
        raise JudgingRefused(
            f"${spent_before:.2f} is already spent on judging, which reaches the ${cap_usd:g} "
            "cap; raise the cap only after the owner approves a new total"
        )
    est = estimate(items, config, prices, cap_usd=remaining)  # refuses before anything is spent
    if adapters is None:
        _check_keys(config)
        adapters = make_adapters(config, prices)
    counter = [0]
    metered = {k: _Metered(a, counter) for k, a in adapters.items()}
    executor = Executor(
        Store(store_dir),
        concurrency=dict(config.concurrency),
        max_cost_usd=remaining,  # EvalKit's spend guard: a call that could pass the cap is refused
    )
    note: str | None = "the run raised before finishing"
    try:
        results = await faithfulness(
            items,
            executor=executor,
            config=config,
            adapters=metered,
            prices=prices,
            run_id=run_id,
        )
        note = None
        return results
    finally:  # one ledger line for whatever this run billed, even if it raised
        append_spend(
            ledger,
            run_id=run_id,
            provider="anthropic+openai",
            split="faithfulness",
            calls=counter[0],
            estimate_usd=est["total_usd"],
            actual_usd=executor.spent_usd,
            note=note,
        )


def judge(items: Sequence[FaithfulnessItem], **kwargs: Any) -> list[FaithfulnessResult]:
    """`judge_async` for a caller with no event loop."""
    return asyncio.run(judge_async(items, **kwargs))


# ------------------------------------------------------------------ summary


def _scored(r: FaithfulnessResult, judge_name: str) -> bool:
    """The judge gave a verdict on at least one of this item's claims."""
    judged = r.n_claims - r.judge_errors.get(judge_name, 0) - r.pending.get(judge_name, 0)
    return r.extraction_error is None and judged > 0 and judge_name in r.score


def summary(
    results: Sequence[FaithfulnessResult], decisions: Iterable[TriageDecision]
) -> dict[str, Any]:
    """Per judge: the mean item score (seeded bootstrap interval), the supported share of judged
    claims and the share of explanations fully faithful (both Wilson 95%). A claim a judge failed
    on is excluded from its score but counts against `fully_faithful` (EvalKit's rule). Claims
    within one explanation are not independent, so the claim-level Wilson interval is narrower
    than the item-level truth; the bootstrap and the fully-faithful share are over items.

    `judge_mode` is "dual" if both judges scored, "single" if one did, else "none". The lowest
    three explanations (by mean of the judges' scores, then the fewest supported claims, then
    decision id) come back as decision ids only."""
    by_item = {item_id(d): d.decision_id for d in decisions}
    judges: list[str] = []
    for r in results:
        for j in r.score:
            if j not in judges:
                judges.append(j)
    per_judge: dict[str, Any] = {}
    scored_judges: list[str] = []
    for j in judges:
        rs = [r for r in results if _scored(r, j)]
        if not rs:
            per_judge[j] = None
            continue
        scored_judges.append(j)
        mean, lo, hi = bootstrap_mean([r.score[j] for r in rs], SEED)
        judged = sum(r.n_claims - r.judge_errors.get(j, 0) - r.pending.get(j, 0) for r in rs)
        supported = sum(sum(1 for c in r.claims if c.verdicts.get(j) == "supported") for r in rs)
        per_judge[j] = {
            "items": len(rs),
            "mean_score": {"point": mean, "lo": lo, "hi": hi},
            "supported_claims": wilson(supported, judged).model_dump(),
            "fully_faithful": wilson(
                sum(r.fully_faithful.get(j, False) for r in rs), len(rs)
            ).model_dump(),
        }
    mode = {0: "none", 1: "single"}.get(len(scored_judges), "dual")
    errors = {j: sum(r.judge_errors.get(j, 0) for r in results) for j in judges}
    pending = {j: sum(r.pending.get(j, 0) for r in results) for j in judges}

    def worst_key(r: FaithfulnessResult) -> tuple[float, int, str]:
        scores = [r.score[j] for j in scored_judges if _scored(r, j)]
        supported = sum(
            1 for c in r.claims for j in scored_judges if c.verdicts.get(j) == "supported"
        )
        return (sum(scores) / len(scores), supported, by_item.get(r.id, r.id))

    candidates = [r for r in results if any(_scored(r, j) for j in scored_judges)]
    worst = [by_item.get(r.id, r.id) for r in sorted(candidates, key=worst_key)[:N_WORST]]
    return {
        "judge_mode": mode,
        "n_items": len(results),
        "judges": per_judge,
        "judge_errors": errors,
        "pending_claims": pending,
        "extraction_errors": sum(1 for r in results if r.extraction_error),
        "no_claims": sum(1 for r in results if r.extraction_error is None and r.n_claims == 0),
        "lowest": worst,
    }


# ------------------------------------------------------------------ files and CLI


@dataclass(frozen=True)
class Loaded:
    decisions: list[TriageDecision]
    cases: list[AlertCase]


def load_run(run: Path, cases_file: Path) -> Loaded:
    """Read a run folder's decisions and a cases file (both read only)."""
    decisions = [
        TriageDecision.model_validate_json(x)
        for x in (run / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    cases = [
        AlertCase.model_validate_json(x)
        for x in cases_file.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    return Loaded(decisions, cases)


def keys_present() -> dict[str, bool]:
    """Which key variables are set (names only, never values)."""
    return {name: bool(os.environ.get(name)) for name in sorted(KEYS.values())}
