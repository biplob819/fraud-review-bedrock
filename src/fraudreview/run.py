"""Batch triage (A2.5, Tech Spec §2.1, §5, C2, E2, NFR3, US1).

`run_triage` triages a list of alerts with at most `concurrency` in flight. It writes
`results/<run_id>/` from one place after the workers finish (workers write only cache entries,
never the run folder):
`decisions.jsonl`, `dispositions.jsonl`, a hash-chained `audit.jsonl` and `run.json`. A rerun is
the resume: finished calls come back from the cache at no cost. The spend cap stops scheduling
(exit 3) and the work already done stays cached.

`freeze_record` names everything that decides a run's behaviour: prompt hashes, policy version,
models, efforts, output limits, router settings, provider, code versions, and source hashes of
the router, the clause evaluator and the record validators. `fraud freeze`
writes it to `docs/measurements/freeze.json`; an eval run refuses if anything has changed since
(C2, Tech Spec §5 step 1).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fraudreview import llm, prompts, router, schemas, stages
from fraudreview import policy as policy_module
from fraudreview.audit import AuditLog
from fraudreview.cache import source_hash
from fraudreview.config import RunConfig
from fraudreview.cost import CapReached
from fraudreview.policy import Policy
from fraudreview.schemas import AlertCase

FREEZE = Path("docs") / "measurements" / "freeze.json"


class FreezeError(Exception):
    """The eval run's settings differ from the frozen ones (or nothing is frozen)."""


def freeze_record(config: RunConfig, policy: Policy) -> dict[str, Any]:
    record = {
        "provider": config.provider,
        "policy_version": policy.version,
        "prompt_hashes": {
            "stage1": prompts.prompt_hash(1, policy),
            "stage2": prompts.prompt_hash(2, policy),
        },
        "models": {"stage1": config.stage1.model, "stage2": config.stage2.model},
        "efforts": {"stage1": config.stage1.effort, "stage2": config.stage2.effort},
        "max_output_tokens": {
            "stage1": config.stage1.max_output_tokens,
            "stage2": config.stage2.max_output_tokens,
        },
        "router": {
            "clear_ceiling": config.clear_ceiling,
            "clear_min_confidence": config.clear_min_confidence,
        },
        "seed": config.seed,
        "code_versions": {
            "model_call": llm.CODE_VERSION,
            "stages": stages.STAGE_CODE_VERSION,
            "prompts": prompts.PROMPTS_CODE_VERSION,
        },
        # the code that turns replies into dispositions: router rules, the clause evaluator
        # and the record validators (their sources, since they carry no version of their own)
        "logic_sources": {
            "router": source_hash(router)[:16],
            "policy": source_hash(policy_module)[:16],
            "schemas": source_hash(schemas)[:16],
        },
    }
    record["digest"] = digest(record)
    return record


def digest(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "digest"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def freeze_problems(frozen_path: Path, current: dict[str, Any]) -> list[str]:
    """Fields that differ from the frozen record; a missing freeze is one problem."""
    if not frozen_path.is_file():
        return [f"nothing is frozen ({frozen_path}); run `fraud freeze` after the dev runs"]
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    keys = sorted(set(frozen) | set(current))
    return [
        f"{k}: frozen {frozen.get(k)!r}, now {current.get(k)!r}"
        for k in keys
        if k != "digest" and frozen.get(k) != current.get(k)
    ]


def load_cases(cases_dir: Path, split: str, n: int | None = None) -> list[AlertCase]:
    path = cases_dir / f"cases_{split}.jsonl"
    lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    cases = [AlertCase.model_validate_json(x) for x in lines]
    if len({c.alert_id for c in cases}) != len(cases):
        raise ValueError(f"{path} repeats an alert id")
    return cases if n is None else cases[:n]


@dataclass
class RunResult:
    run_id: str
    out_dir: Path
    n_total: int
    triaged: list[stages.Triaged] = field(default_factory=list)
    stopped: str | None = None  # "cap" when the spend cap stopped scheduling
    billed_usd: float = 0.0
    billed_calls: int = 0
    cases_sha256: str = ""  # identity of the alert sample this run triaged


async def run_triage(
    cases: list[AlertCase],
    ctx: stages.Context,
    out_dir: Path,
    run_id: str,
    *,
    progress: Callable[[int, int, float], None] | None = None,
    every: int = 10,
) -> RunResult:
    """Triage every case (order kept), then write the run's files in one place."""
    stop = asyncio.Event()
    done: list[stages.Triaged | None] = [None] * len(cases)
    finished = 0
    gate = asyncio.Semaphore(ctx.config.concurrency)

    async def one(i: int, case: AlertCase) -> None:
        nonlocal finished
        async with gate:
            if stop.is_set():
                return
            try:
                done[i] = await stages.triage_alert(case, ctx)
            except CapReached:
                stop.set()  # stop scheduling; finished work is cached
                return
            finished += 1
            if progress and (finished % every == 0 or finished == len(cases)):
                progress(finished, len(cases), ctx.cap.actual)

    await asyncio.gather(*(one(i, c) for i, c in enumerate(cases)))
    result = RunResult(run_id=run_id, out_dir=out_dir, n_total=len(cases))
    result.cases_sha256 = hashlib.sha256(
        "".join(c.model_dump_json() + "\n" for c in cases).encode("utf-8")
    ).hexdigest()
    result.triaged = [t for t in done if t is not None]
    result.stopped = "cap" if stop.is_set() else None
    result.billed_usd = ctx.cap.actual
    result.billed_calls = sum(
        0 if d.cache_hit else d.attempts for t in result.triaged for d in t.decisions
    )
    write_run(result, ctx)
    return result


def write_run(result: RunResult, ctx: stages.Context) -> None:
    """Rewrite the run folder from this run's results (a rerun replaces it; the cache makes the
    content identical for finished work)."""
    out = result.out_dir
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    now = datetime.now(UTC)
    decisions = [d for t in result.triaged for d in t.decisions]
    with (out / "decisions.jsonl").open("w", encoding="utf-8") as f:
        for d in decisions:
            f.write(d.model_dump_json() + "\n")
    with (out / "dispositions.jsonl").open("w", encoding="utf-8") as f:
        for t in result.triaged:
            f.write(t.disposition.model_dump_json() + "\n")
    log = AuditLog(out / "audit.jsonl")
    for t in result.triaged:
        for d in t.decisions:
            log.record_decision(d, now)
        log.append(t.disposition, now)
    seq, head = log.head
    record = {
        "run_id": result.run_id,
        "n_total": result.n_total,
        "n_done": len(result.triaged),
        "stopped": result.stopped,
        "billed_usd": round(result.billed_usd, 6),
        "billed_calls": result.billed_calls,
        "decision_cost_usd": round(sum(d.cost_usd for d in decisions), 6),
        "audit_head": {"seq": seq, "hash": head},
        "freeze": freeze_record(ctx.config, ctx.policy),
        "errors": stages.error_counts(decisions),
        "cases_sha256": result.cases_sha256,
        "finished_at": now.isoformat(timespec="seconds"),
    }
    (out / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")


GOLDEN = Path("tests") / "fixtures" / "golden"


def record_golden(
    result: RunResult, cases: list[AlertCase], ctx: stages.Context, labels: Path, out: Path
) -> dict[str, Any]:
    """RUN-05: freeze a small finished run as the golden replay fixture. The recorded replies
    go into a cache store under their real keys (request ids dropped), with the cases, their
    labels and the expected dispositions. Replaying it needs no model and no network."""
    from fraudreview.cache import Store

    by_id = {t.disposition.alert_id: t for t in result.triaged}
    if result.stopped or len(by_id) != len(cases):
        raise ValueError("record golden only from a run that finished every alert")
    if out.exists():
        shutil.rmtree(out)
    store = Store(out / "store")
    for c in cases:
        for d in by_id[c.alert_id].decisions:
            clean = d.model_copy(update={"request_id": None, "cache_hit": False})
            stage = 1 if d.stage == 1 else 2
            store.put(
                stages.stage_key(stage, c, ctx),
                stages.STAGE_CODE_VERSION,
                clean.model_dump(mode="json"),
            )
    wanted = {c.alert_id for c in cases}
    truth_lines = [
        x
        for x in labels.read_text(encoding="utf-8").splitlines()
        if x.strip() and json.loads(x)["alert_id"] in wanted
    ]
    if len(truth_lines) != len(cases):
        raise ValueError(f"{labels} does not label every golden alert")
    (out / "cases.jsonl").write_text(
        "".join(c.model_dump_json() + "\n" for c in cases), encoding="utf-8"
    )
    (out / "truth.jsonl").write_text("".join(x + "\n" for x in truth_lines), encoding="utf-8")
    expected = {
        "freeze": freeze_record(ctx.config, ctx.policy),
        "dispositions": [by_id[c.alert_id].disposition.model_dump(mode="json") for c in cases],
    }
    (out / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return expected


def dev_log_line(
    result: RunResult, labels: Path, config: RunConfig, minutes_per_alert: float = 4.0
) -> dict[str, Any]:
    """RUN-02: one line per dev run with the settings digest and the dev result, so every
    policy or prompt change is logged next to what it did on the dev set."""
    from fraudreview import metrics
    from fraudreview.truth import AlertTruth

    truth = [
        AlertTruth.model_validate_json(x)
        for x in labels.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    done = {t.disposition.alert_id for t in result.triaged}
    label_map = {t.alert_id: t.label for t in truth if t.alert_id in done}
    fields = metrics.compute(
        [t.disposition for t in result.triaged],
        [d for t in result.triaged for d in t.decisions],
        label_map,
        minutes_per_alert=minutes_per_alert,
        seed=config.seed,
    )

    def rate(r: Any) -> dict[str, Any]:
        return {"k": r.k, "n": r.n, "point": round(r.point, 4), "hi": round(r.hi, 4)}

    return {
        "run_id": result.run_id,
        "digest": result.run_id.rsplit("-", 1)[-1],
        "n": fields["n_alerts"],
        "frauds": fields["n_frauds"],
        "leakage": rate(fields["leakage"]),
        "auto_clear_rate": rate(fields["auto_clear_rate"]),
        "auto_clear_precision": rate(fields["auto_clear_precision"]),
        "queue_mix": fields["queue_mix"],
        "billed_usd": round(result.billed_usd, 6),
        "decision_cost_usd": round(fields["cost_total_usd"], 6),
    }
