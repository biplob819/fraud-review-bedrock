"""Per-call cost, the pre-run estimate, the running spend cap and bounded concurrency
(N1.3, Tech Spec §2.1 and §9, H8, H9, EvalKit L-12 and L-14).

Prices are USD per million tokens from the provider's table in `prices.yaml`. Output tokens
include thinking. Cache writes are priced at the 5-minute rate, the default TTL.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass

from fraudreview.config import RunConfig
from fraudreview.schemas import Tokens


class CapReached(Exception):
    """The next call would take spend past `max_cost_usd`. The CLI stops scheduling and exits 3;
    completed work stays cached, so rerunning resumes."""


def call_cost(tokens: Tokens, price: Mapping[str, float]) -> float:
    return (
        tokens.input * price["input"]
        + tokens.output * price["output"]
        + tokens.cache_read * price["cache_read"]
        + tokens.cache_write * price["cache_write_5m"]
    ) / 1e6


@dataclass(frozen=True)
class Assumptions:
    """Planning token counts per call (§9); replaced by dev-run averages after A2.5."""

    cached_prefix: int = 1500
    alert_fresh: int = 700
    stage1_out: int = 450  # output + thinking
    stage2_out: int = 1150
    stage2_share: float = 1.0  # s: plan as if every alert reaches stage 2


def assumptions_from(
    decisions: list[Tokens | None], stages_of: list[int], n_alerts: int
) -> Assumptions:
    """Planning tokens from a finished run's live calls (§9: replace planning figures with dev
    averages). Each stage's mean input (fresh plus cache writes), cache reads and output, and
    the share of alerts that reached stage 2."""

    def mean(xs: list[int]) -> int:
        return round(sum(xs) / len(xs)) if xs else 0

    by: dict[int, list[Tokens]] = {1: [], 2: []}
    for tok, stage in zip(decisions, stages_of, strict=True):
        if tok is not None:
            by[stage].append(tok)
    if not by[1] or n_alerts < 1:
        raise ValueError("a run with live stage-1 calls is needed to plan from")
    every = by[1] + by[2]
    return Assumptions(
        cached_prefix=mean([t.cache_read for t in every]),
        alert_fresh=mean([t.input + t.cache_write for t in every]),
        stage1_out=mean([t.output for t in by[1]]),
        stage2_out=mean([t.output for t in by[2]]) if by[2] else Assumptions().stage2_out,
        stage2_share=min(1.0, len(by[2]) / n_alerts),
    )


@dataclass(frozen=True)
class Estimate:
    n_alerts: int
    stage1_calls: int
    stage2_calls: int
    stage1_usd: float
    stage2_usd: float

    @property
    def total_usd(self) -> float:
        return self.stage1_usd + self.stage2_usd


def per_call_estimate(price: Mapping[str, float], a: Assumptions, out_tokens: int) -> float:
    """One call with a warm cache: the prefix is a cache read, the alert is fresh input."""
    return call_cost(
        Tokens(input=a.alert_fresh, output=out_tokens, cache_read=a.cached_prefix), price
    )


def estimate_run(
    n: int,
    prices: Mapping[str, Mapping[str, float]],
    config: RunConfig,
    a: Assumptions | None = None,
) -> Estimate:
    """§9: stage1 = n × per-call; stage2 = n × s × per-call (judging is EvalKit's own estimate)."""
    a = a or Assumptions()
    if n < 0 or not 0.0 <= a.stage2_share <= 1.0:
        raise ValueError("n must be at least 0 and the stage-2 share in [0, 1]")
    p1, p2 = prices[config.stage1.model], prices[config.stage2.model]
    s2_calls = round(n * a.stage2_share)
    return Estimate(
        n_alerts=n,
        stage1_calls=n,
        stage2_calls=s2_calls,
        stage1_usd=n * per_call_estimate(p1, a, a.stage1_out),
        stage2_usd=s2_calls * per_call_estimate(p2, a, a.stage2_out),
    )


class SpendCap:
    """Before each call: actual + reserved + this call's reservation must stay at or under the
    cap. Callers reserve `llm.worst_case_cost`, the most a call can bill, so actual spend cannot
    pass the cap; `commit` stops the run if it ever does. Reserve the slot before spending
    (L-12); settle every reservation exactly once (L-14)."""

    def __init__(self, max_usd: float) -> None:
        if not max_usd > 0:
            raise ValueError("the cap must be above 0")
        self.max_usd = max_usd
        self.actual = 0.0
        self.reserved: dict[int, float] = {}
        self._next = 0
        self._lock = asyncio.Lock()

    async def reserve(self, estimate_usd: float) -> int:
        if not estimate_usd >= 0:
            raise ValueError("an estimate is a non-negative number")
        async with self._lock:
            if self.actual + sum(self.reserved.values()) + estimate_usd > self.max_usd:
                raise CapReached(
                    f"spend would pass the ${self.max_usd:g} cap "
                    f"(spent ${self.actual:.4f}, in flight ${sum(self.reserved.values()):.4f})"
                )
            self._next += 1
            self.reserved[self._next] = estimate_usd
            return self._next

    async def commit(self, token: int, actual_usd: float) -> None:
        """Settle a reservation with what the call billed. If spend has passed the cap anyway
        (a reservation smaller than the bill), record it and stop the run (CapReached)."""
        if (
            isinstance(actual_usd, bool)
            or not isinstance(actual_usd, int | float)
            or not math.isfinite(actual_usd)
            or actual_usd < 0
        ):
            raise ValueError("an actual cost is a finite non-negative number")
        async with self._lock:
            self.reserved.pop(token)  # KeyError on a double or unknown settle
            self.actual += actual_usd
            if self.actual > self.max_usd:
                raise CapReached(f"spend ${self.actual:.4f} passed the ${self.max_usd:g} cap")

    async def release(self, token: int) -> None:
        async with self._lock:
            self.reserved.pop(token)


async def bounded[T, R](
    items: Iterable[T], worker: Callable[[T], Awaitable[R]], concurrency: int
) -> list[R]:
    """Run `worker` over `items` with at most `concurrency` in flight; results keep input order."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    gate = asyncio.Semaphore(concurrency)

    async def one(item: T) -> R:
        async with gate:
            return await worker(item)

    return list(await asyncio.gather(*(one(i) for i in items)))


def dry_run_lines(est: Estimate, config: RunConfig) -> list[str]:
    return [
        f"stage 1  {config.stage1.model:<24} {est.stage1_calls:>5} calls  ${est.stage1_usd:.4f}",
        f"stage 2  {config.stage2.model:<24} {est.stage2_calls:>5} calls  ${est.stage2_usd:.4f}",
        f"estimated total ${est.total_usd:.2f} (cap ${config.max_cost_usd:g}; no model calls made)",
    ]
