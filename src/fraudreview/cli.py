"""The `fraud` command. Later nodes add `doctor`, `baseline`, `triage`, `evaluate`, `report`
and `demo-cache` (Tech Spec §4.3)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

from fraudreview import __version__
from fraudreview.defaults import packaged

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW = Path.home() / ".cache" / "fraud-review" / "raw"
REFERENCE_MANIFEST = ROOT / "docs" / "data" / "splits.json"  # present in a source checkout


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fraud", description=__doc__)
    ap.add_argument("--version", action="version", version=f"fraud {__version__}")
    sub = ap.add_subparsers(dest="command")
    load = sub.add_parser(
        "load", help="download the source data, minimise it, split it and record the hashes"
    )
    load.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW, help="downloaded source files")
    load.add_argument("--data-dir", type=Path, default=Path("data"), help="where splits go")
    load.add_argument(
        "--offline", action="store_true", help="use files already in --raw-dir; never download"
    )
    base = sub.add_parser(
        "baseline", help="features, LightGBM baseline, D-9 threshold, alerts, samples and SHAP"
    )
    base.add_argument("--data-dir", type=Path, default=Path("data"), help="where splits are")
    base.add_argument("--out", type=Path, default=Path("results/baseline"), help="output folder")
    base.add_argument("--config", type=Path, default=Path("config.yaml"), help="run config (seed)")
    doc = sub.add_parser(
        "doctor", help="check this machine and the model provider are ready to run"
    )
    doc.add_argument("--config", type=Path, default=Path("config.yaml"))
    doc.add_argument("--prices", type=Path, default=Path("prices.yaml"))
    doc.add_argument("--offline", action="store_true", help="local checks only; no network calls")
    doc.add_argument(
        "--smoke", action="store_true", help="also one tiny call per model (a fraction of a cent)"
    )
    doc.add_argument(
        "--record",
        type=Path,
        help="also write the live preflight proof (structured call, cache pair, rate limits) "
        "to this JSON file; Claude API provider only, about a cent",
    )
    tri = sub.add_parser(
        "triage", help="run both stages and the router; --dry-run prints calls and cost only"
    )
    tri.add_argument("--split", choices=("dev", "eval", "eval2"), required=True)
    tri.add_argument("--alerts", type=int, help="how many alerts (default: the whole sample)")
    tri.add_argument("--dry-run", action="store_true", help="estimate only; no model calls")
    tri.add_argument(
        "--plan-from",
        type=Path,
        help="a finished run folder whose live calls set the planning tokens (§9)",
    )
    tri.add_argument("--config", type=Path, default=Path("config.yaml"))
    tri.add_argument("--prices", type=Path, default=Path("prices.yaml"))
    tri.add_argument("--cases", type=Path, default=Path("results/baseline"), help="alert samples")
    tri.add_argument("--out", type=Path, default=Path("results"), help="where run folders go")
    tri.add_argument("--store", type=Path, default=Path(".fraud/store"), help="response cache")
    tri.add_argument(
        "--record-golden",
        type=Path,
        help="after a finished run, write it as the golden replay fixture (RUN-05)",
    )
    mon = sub.add_parser(
        "monitor", help="score a finished run, append it to the history and check the alarm bands"
    )
    mon.add_argument("--run", type=Path, required=True, help="a finished run folder")
    mon.add_argument("--truth", type=Path, required=True, help="the labels JSONL for its split")
    mon.add_argument("--cases", type=Path, help="the run's alert cases JSONL (for the drift check)")
    mon.add_argument("--reference", type=Path, help="reference cases JSONL for the drift check")
    mon.add_argument("--config", type=Path, default=Path("config.yaml"))
    mon.add_argument("--no-append", action="store_true", help="check only; leave the history alone")
    ev = sub.add_parser(
        "evaluate",
        help="write summary.json for a finished run (offline), append the history, check the bars",
    )
    ev.add_argument("--run", type=Path, help="a finished run folder")
    ev.add_argument("--cases", type=Path, help="the run's alert cases JSONL")
    ev.add_argument("--truth", type=Path, help="the labels JSONL for its split")
    ev.add_argument(
        "--out", type=Path, help="where to write the summary (default: <run>/summary.json)"
    )
    ev.add_argument(
        "--faithfulness-summary",
        type=Path,
        help="the judge cache summary (scripts/judge_cache_summary.py); carried through as partial",
    )
    ev.add_argument(
        "--baseline",
        type=Path,
        help="baseline.json or its summary block (default: next to --cases, else committed copy)",
    )
    ev.add_argument("--reference", type=Path, help="reference cases JSONL for the drift check")
    ev.add_argument("--config", type=Path, default=Path("config.yaml"))
    ev.add_argument("--no-append", action="store_true", help="leave the run history alone")
    ev.add_argument(
        "--example",
        action="store_true",
        help="evaluate the committed golden run (tests/fixtures/golden); no files are required",
    )
    rp = sub.add_parser(
        "report", help="render summary.json as one self-contained HTML dashboard (offline)"
    )
    rp.add_argument("--summary", type=Path, help="a summary.json written by `fraud evaluate`")
    rp.add_argument("--history", type=Path, help="the run history JSONL (default: the repo's log)")
    rp.add_argument("--out", type=Path, default=Path("report.html"), help="the HTML file to write")
    rp.add_argument("--example", action="store_true", help="render from the committed golden run")
    ex = sub.add_parser(
        "experiment", help="log a dev-set change: two dev runs, the rule's verdict, a ledger line"
    )
    ex.add_argument("--before", type=Path, required=True, help="the dev run before the change")
    ex.add_argument("--after", type=Path, required=True, help="the dev run after the change")
    ex.add_argument("--truth", type=Path, required=True, help="dev labels JSONL")
    ex.add_argument("--hypothesis", required=True)
    ex.add_argument("--change", required=True)
    ex.add_argument("--verdict", choices=("keep", "revert"), help="default: the rule's verdict")
    ex.add_argument("--note")
    ex.add_argument("--config", type=Path, default=Path("config.yaml"))
    ex.add_argument("--dry-run", action="store_true", help="print the line; do not append it")
    fr = sub.add_parser("freeze", help="record the settings the eval run must match (C2)")
    fr.add_argument("--config", type=Path, default=Path("config.yaml"))
    dc = sub.add_parser(
        "demo-cache", help="write the review screen's replay file from a finished run (D-15)"
    )
    dc.add_argument("--run", type=Path, required=True, help="a finished run folder")
    dc.add_argument(
        "--alerts", type=Path, required=True, help="Alert views (results/baseline/alerts_<split>)"
    )
    dc.add_argument("--out", type=Path, default=Path("demo/replay.jsonl"))
    dc.add_argument(
        "--max-cases", type=int, help="keep the first n cases in queue order (holds first)"
    )
    jd = sub.add_parser(
        "judge",
        help="score stage-2 explanations with EvalKit faithfulness (two judges); "
        "--dry-run prints the estimate only",
    )
    jd.add_argument("--run", type=Path, required=True, help="a finished run folder")
    jd.add_argument("--cases", type=Path, required=True, help="the alert cases JSONL")
    jd.add_argument("--max-items", type=int, help="judge only the first n stage-2 decisions")
    jd.add_argument("--cap-usd", type=float, default=10.0, help="refuse and stop above this spend")
    jd.add_argument("--dry-run", action="store_true", help="estimate only; no model calls")
    jd.add_argument("--store", type=Path, default=Path(".fraud/judge-store"), help="judge cache")
    jd.add_argument(
        "--claims-per-item",
        type=float,
        help="claims per explanation to plan with, measured on a small sample; without it "
        "EvalKit counts the claims from the explanations themselves",
    )
    jd.add_argument(
        "--out", type=Path, help="where to write results (default: <run>/faithfulness.json)"
    )
    return ap


def _judge(args: argparse.Namespace) -> int:
    from fraudreview import judging
    from fraudreview.ledger import LEDGER

    if args.max_items is not None and args.max_items < 1:
        print("fraud judge: --max-items must be at least 1", file=sys.stderr)
        return 2
    if not args.cap_usd > 0:
        print("fraud judge: --cap-usd must be above 0", file=sys.stderr)
        return 2
    try:
        loaded = judging.load_run(args.run, args.cases)
        items = judging.build_items(loaded.decisions, loaded.cases)
    except (OSError, ValueError, KeyError) as exc:
        print(f"fraud judge: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.max_items is not None:
        items = items[: args.max_items]
    if not items:
        print("fraud judge: no stage-2 decisions with an output to judge", file=sys.stderr)
        return 2
    est = judging.estimate(items, claims_per_item=args.claims_per_item)
    print(judging.format_estimate({**est, "cap_usd": args.cap_usd}))
    if est["total_usd"] > args.cap_usd:  # printed first, so the number is visible
        print(
            f"fraud judge: the estimate ${est['total_usd']:.2f} is over the "
            f"${args.cap_usd:g} cap; lower --max-items or raise --cap-usd",
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        return 0
    from evalkit.runtime import BudgetExceeded

    load_env_file(Path(".env"))
    out = args.out or args.run / "faithfulness.json"
    run_id = f"faithfulness-{args.run.name}"
    try:
        results = judging.judge(
            items,
            cap_usd=args.cap_usd,
            store_dir=args.store,
            ledger_path=ROOT / LEDGER,
            run_id=run_id,
        )
    except judging.JudgingRefused as exc:
        print(f"fraud judge: {exc}", file=sys.stderr)
        return 2
    except BudgetExceeded as exc:
        print(
            f"fraud judge: stopped at the spend cap: {exc}. The cap is a total for this run: "
            "do not rerun without the owner approving a new total",
            file=sys.stderr,
        )
        return 3
    summ = judging.summary(results, loaded.decisions)
    body = {"summary": summ, "results": [r.model_dump(mode="json") for r in results]}
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summ, indent=2, sort_keys=True))
    print(f"-> {out}")
    return 0


def _demo_cache(args: argparse.Namespace) -> int:
    from fraudreview.demo_cache import DemoCacheError, write_replay

    if args.max_cases is not None and args.max_cases < 1:
        print("fraud demo-cache: --max-cases must be at least 1", file=sys.stderr)
        return 2
    try:
        n, size, skipped = write_replay(args.run, args.alerts, args.out, args.max_cases)
    except DemoCacheError as exc:
        print(f"fraud demo-cache: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.out}: {n} held or escalated cases, {size} bytes (simulated data)")
    print(f"skipped {skipped} cases whose text the public-content guard flags")
    from fraudreview import content_guard

    if not content_guard.GUARD_FILE.is_file():
        print(
            "warning: no private guard file (.public-guard.local); only the generic patterns "
            "were applied, so run this where the full guard is present before publishing",
            file=sys.stderr,
        )
    return 0


def _triage(args: argparse.Namespace) -> int:
    from fraudreview.config import ConfigError, load_config
    from fraudreview.cost import dry_run_lines, estimate_run
    from fraudreview.doctor import price_table

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"fraud triage: {exc}", file=sys.stderr)
        return 2
    n = args.alerts
    if n is None:
        cases = args.cases / f"cases_{args.split}.jsonl"
        if not cases.is_file():
            print(
                f"fraud triage: no {cases}; run `fraud baseline` or pass --alerts", file=sys.stderr
            )
            return 2
        n = sum(1 for line in cases.read_text(encoding="utf-8").splitlines() if line.strip())
    if n < 1:
        print("fraud triage: --alerts must be at least 1", file=sys.stderr)
        return 2
    try:
        prices = price_table(args.prices, config.provider)
    except (OSError, AttributeError, TypeError, ValueError) as exc:  # missing or malformed file
        print(f"fraud triage: cannot read {args.prices} ({type(exc).__name__})", file=sys.stderr)
        return 2
    missing = [m for m in (config.stage1.model, config.stage2.model) if m not in prices]
    if missing:
        print(
            f"fraud triage: no {config.provider} prices for {', '.join(missing)}", file=sys.stderr
        )
        return 2
    from fraudreview.llm import check_price

    for m in (config.stage1.model, config.stage2.model):
        check_price(prices[m])  # a bad price is a config error (exit 2), not "$nan"
    assumptions = None
    if args.plan_from:
        problem = _plan_problem(args.plan_from, config)
        if isinstance(problem, str):
            print(f"fraud triage: --plan-from {args.plan_from}: {problem}", file=sys.stderr)
            return 2
        assumptions = problem
    est = estimate_run(n, prices, config, assumptions)
    mode = "dry run" if args.dry_run else "plan"
    print(f"{mode}: {args.split}, {n} alerts, provider {config.provider}")
    for line in dry_run_lines(est, config):
        print(line)
    if est.total_usd > config.max_cost_usd:
        print(
            f"fraud triage: the estimate ${est.total_usd:.2f} is over max_cost_usd "
            f"${config.max_cost_usd:g}; lower --alerts or raise the cap",
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        return 0
    return _triage_live(args, config, prices, n, est.total_usd)


def _plan_problem(folder: Path, config: Any) -> Any:
    """Planning tokens from a finished run made under the current settings, or why not."""
    from fraudreview import run
    from fraudreview.cost import assumptions_from
    from fraudreview.policy import load_policy
    from fraudreview.schemas import TriageDecision

    try:
        facts = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        decs = [
            TriageDecision.model_validate_json(x)
            for x in (folder / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
    except (OSError, ValueError) as exc:
        return f"not a finished run ({type(exc).__name__})"
    current = run.freeze_record(config, load_policy(packaged("policy.md")))
    freeze = facts.get("freeze") if isinstance(facts, dict) else None
    if not isinstance(freeze, dict) or freeze.get("digest") != current["digest"]:
        return "that run used other settings; plan from a run made under the current ones"
    live = [d for d in decs if not d.cache_hit]
    n_alerts = facts.get("n_done", 0) if isinstance(facts.get("n_done"), int) else 0
    if not any(d.stage == 1 for d in live) or n_alerts < 1:
        return "that run has no live stage-1 calls to plan from"
    return assumptions_from(
        [d.tokens for d in live],
        [d.stage for d in live],
        n_alerts,
        stage2_reached=sum(1 for d in decs if d.stage == 2),
    )


def _triage_live(
    args: argparse.Namespace, config: Any, prices: dict[str, Any], n: int, estimate: float
) -> int:
    import asyncio

    from fraudreview import run, stages
    from fraudreview.cache import Store
    from fraudreview.cost import SpendCap
    from fraudreview.ledger import LEDGER, append_spend
    from fraudreview.llm import make_client
    from fraudreview.policy import load_policy

    policy = load_policy(packaged("policy.md"))
    current = run.freeze_record(config, policy)
    if args.split.startswith("eval"):
        problems = run.freeze_problems(ROOT / run.FREEZE, current)
        if problems:
            print("fraud triage: the eval run must match the freeze (C2):", file=sys.stderr)
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            return 2
    load_env_file(Path(".env"))
    cases = run.load_cases(args.cases, args.split, n)
    ctx = stages.Context(
        config=config,
        policy=policy,
        client=make_client(config),
        store=Store(args.store),
        cap=SpendCap(config.max_cost_usd),
        prices=prices,
    )
    run_id = f"{args.split}-{len(cases)}-{current['digest'][:8]}"

    def progress(done: int, total: int, spent: float) -> None:
        print(f"  {done}/{total} alerts, ${spent:.4f} billed", file=sys.stderr, flush=True)

    result = None
    try:
        result = asyncio.run(
            run.run_triage(cases, ctx, args.out / run_id, run_id, progress=progress)
        )
    finally:  # one ledger line for whatever this run billed, even if it raised
        append_spend(
            ROOT / LEDGER,
            run_id=run_id,
            provider=config.provider,
            split=args.split,
            calls=result.billed_calls if result else 0,
            estimate_usd=estimate,
            actual_usd=ctx.cap.actual,
            note=None if result else "the run raised before finishing; calls not counted",
        )
    assert result is not None  # run_triage returned (an exception would have propagated)
    finals = [t.disposition.final for t in result.triaged]
    print(
        f"{run_id}: {len(result.triaged)}/{len(cases)} alerts, "
        f"{finals.count('auto_cleared')} auto-cleared, {finals.count('hold')} held, "
        f"{finals.count('escalate')} escalated; ${result.billed_usd:.4f} billed "
        f"({result.billed_calls} calls) -> {args.out / run_id}"
    )
    if result.stopped == "cap":
        print("fraud triage: stopped at the spend cap; rerun to resume", file=sys.stderr)
        return 3
    labels = args.cases / f"truth_{args.split}.jsonl"
    if args.split == "dev" and labels.is_file():  # RUN-02: log every dev run with its result
        line = run.dev_log_line(result, labels, config)
        log = ROOT / "docs" / "measurements" / "dev-runs.jsonl"
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
        print(
            f"dev result: leakage {line['leakage']['k']}/{line['leakage']['n']} frauds, "
            f"auto-clear {line['auto_clear_rate']['k']}/{line['n']} -> {log}"
        )
    if args.record_golden:
        run.record_golden(result, cases, ctx, labels, args.record_golden)
        print(f"golden fixture -> {args.record_golden}")
    return 0


def _monitor(args: argparse.Namespace) -> int:
    from fraudreview import metrics, monitor
    from fraudreview.config import ConfigError, load_config
    from fraudreview.schemas import AlertCase, Disposition, TriageDecision

    try:
        config = load_config(args.config)
        run = args.run
        disp = [
            Disposition.model_validate_json(x)
            for x in (run / "dispositions.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        decs = [
            TriageDecision.model_validate_json(x)
            for x in (run / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        facts = json.loads((run / "run.json").read_text(encoding="utf-8"))
        truth = [
            json.loads(x) for x in args.truth.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
        labels = {r["alert_id"]: bool(r["label"]) for r in truth}
        fields = metrics.compute(disp, decs, labels, minutes_per_alert=4.0, seed=config.seed)
    except (ConfigError, OSError, ValueError, KeyError, metrics.MetricsError) as exc:
        print(f"fraud monitor: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    line = monitor.run_history_line(run.name, fields, facts)
    line["source"] = "fraud monitor"
    alarms = monitor.check_bands(line, config.alarms or ())
    print(
        f"{line['run_id']}: n={line['n']}, frauds={line['frauds']}, leakage "
        f"{line['leakage']['k']}/{line['leakage']['n']}, auto-clear "
        f"{line['auto_clear_rate']['k']}/{line['auto_clear_rate']['n']} (simulated data)"
    )
    for a in alarms:
        print(f"  {a.level.upper():<5} {a.metric} = {a.value:g} ({a.band})")
    if not alarms:
        print("  no alarms")
    if args.cases and args.reference:
        read = lambda p: [  # noqa: E731
            AlertCase.model_validate_json(x)
            for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        for name, (value, level) in monitor.drift(read(args.reference), read(args.cases)).items():
            print(f"  drift {name}: PSI {value:.3f} ({level})")
    if not args.no_append and not monitor.append_history_once(ROOT / monitor.RUNS, line):
        print(f"  already in the history ({line['run_id']}, {line['digest']}); not added twice")
    return 1 if any(a.level == "fail" for a in alarms) else 0


def _evaluate(args: argparse.Namespace) -> int:
    from fraudreview import metrics, monitor, summary
    from fraudreview.config import ConfigError, load_config
    from fraudreview.policy import PolicyError, load_policy

    if not args.example and not (args.run and args.cases and args.truth):
        print("fraud evaluate: give --run, --cases and --truth (or --example)", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
        policy = load_policy(packaged("policy.md"))
        if args.example:
            inputs = summary.load_golden(ROOT / "tests" / "fixtures" / "golden")
        else:
            inputs = summary.load_run(args.run, args.cases, args.truth)
        baseline_path = args.baseline
        if baseline_path is None and args.cases is not None:
            beside = args.cases.parent / "baseline.json"
            baseline_path = beside if beside.is_file() else None
        baseline = summary.read_baseline(baseline_path or ROOT / summary.BASELINE_SUMMARY)
        reference = summary.read_cases(args.reference) if args.reference else ()
        built = summary.build(
            inputs,
            config,
            policy,
            baseline,
            faithfulness=args.faithfulness_summary,
            reference=reference,
        )
    except (ConfigError, PolicyError, summary.SummaryError, OSError, ValueError, KeyError) as exc:
        print(f"fraud evaluate: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    s = built.summary
    out = args.out or (args.run / "summary.json" if args.run else Path("summary.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(summary.to_json(s), encoding="utf-8")
    print(
        f"{s.run_id}: n={s.n_alerts}, frauds={s.n_frauds}, leakage {s.leakage.k}/{s.leakage.n} "
        f"(upper bound {s.leakage.hi:.1%}), auto-clear {s.auto_clear_rate.k}/{s.auto_clear_rate.n} "
        f"(simulated data) -> {out}"
    )
    for t in s.thresholds:
        status = "n/a" if t.passed is None else ("pass" if t.passed else "FAIL")
        print(f"  threshold {t.metric} {t.op} {t.value:g}: {status}")
    for a in built.alarms:
        print(f"  {a.level.upper():<5} {a.metric} = {a.value:g}")
    if not args.no_append and not args.example:
        added = summary.append_history_once(ROOT / monitor.RUNS, built)
        print("  history line appended" if added else "  history already has this run")
    return metrics.exit_code(s.thresholds)


def _report(args: argparse.Namespace) -> int:
    from fraudreview import monitor, report, summary

    if not args.example and not args.summary:
        print("fraud report: give --summary (or --example)", file=sys.stderr)
        return 2
    try:
        if args.example:
            html = report.render_example(ROOT)
        else:
            history_path = args.history or ROOT / monitor.RUNS
            html = report.render(
                summary.read_summary(args.summary), monitor.read_jsonl(history_path)
            )
    except (summary.SummaryError, report.ReportError, OSError, ValueError) as exc:
        print(f"fraud report: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"report -> {args.out} (one file, no external requests; simulated data)")
    return 0


def _experiment(args: argparse.Namespace) -> int:
    from fraudreview import monitor, run
    from fraudreview.config import ConfigError, load_config

    try:
        config = load_config(args.config)
        before = run.dev_log_line(_finished(args.before), args.truth, config)
        after = run.dev_log_line(_finished(args.after), args.truth, config)
        facts_b = json.loads((args.before / "run.json").read_text(encoding="utf-8"))
        facts_a = json.loads((args.after / "run.json").read_text(encoding="utf-8"))
        if args.truth.name.startswith("truth_") and args.truth.name != "truth_dev.jsonl":
            raise ValueError(f"--truth is {args.truth.name}; an experiment needs truth_dev.jsonl")
        for side, facts in (("before", facts_b), ("after", facts_a)):
            if not str(facts["run_id"]).startswith("dev-"):  # run ids start with their split
                raise ValueError(
                    f"--{side} is run {facts['run_id']!r}, not a dev run; an experiment is "
                    "judged on the dev split only (the eval splits stay frozen)"
                )
        from fraudreview import compare

        labels = compare.load_labels(args.truth)
        paired_result = compare.compare_runs(
            [x.disposition for x in _finished(args.before).triaged],
            [x.disposition for x in _finished(args.after).triaged],
            labels,
            ("before", "after"),
        )
        gain = paired_result.gap("auto_clear")
        paired = {
            "survives_holm": bool(gain.claimed and gain.diff > 0),
            "auto_clear_diff": round(gain.diff, 4),
            "auto_clear_holm_interval": [round(gain.holm_lo, 4), round(gain.holm_hi, 4)],
            "leakage_verdict": compare.verdict(paired_result),
        }
        rule_verdict, reason = monitor.accept_change(before, after, paired=paired)
        verdict = args.verdict or rule_verdict
        line = monitor.experiment_line(
            hypothesis=args.hypothesis,
            change=args.change,
            digest_before=facts_b["freeze"]["digest"],
            digest_after=facts_a["freeze"]["digest"],
            dev_before=before,
            dev_after=after,
            paired=paired,
            verdict=verdict,
            note=args.note,
        )
    except (ConfigError, OSError, ValueError, KeyError) as exc:
        print(f"fraud experiment: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"rule: {rule_verdict} ({reason}); recorded verdict: {verdict}")
    if args.dry_run:
        print(json.dumps(line, indent=2, sort_keys=True))
        return 0
    monitor.append_jsonl(ROOT / "docs" / "measurements" / "experiments.jsonl", line)
    return 0


def _finished(folder: Path) -> Any:
    """A `RunResult`-like view of a finished run folder, for `run.dev_log_line`."""
    from fraudreview import run
    from fraudreview.schemas import Disposition, TriageDecision

    facts = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    disp = [
        Disposition.model_validate_json(x)
        for x in (folder / "dispositions.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    decs = {
        d.alert_id: [] for d in disp
    }  # decisions are grouped per alert, as run.RunResult holds them
    for x in (folder / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
        if x.strip():
            d = TriageDecision.model_validate_json(x)
            decs.setdefault(d.alert_id, []).append(d)
    from fraudreview import stages

    result = run.RunResult(run_id=folder.name, out_dir=folder, n_total=facts["n_total"])
    result.triaged = [stages.Triaged(tuple(decs[d.alert_id]), d) for d in disp]
    result.billed_usd = facts["billed_usd"]
    return result


def _freeze(args: argparse.Namespace) -> int:
    from fraudreview import run
    from fraudreview.config import ConfigError, load_config
    from fraudreview.policy import load_policy

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"fraud freeze: {exc}", file=sys.stderr)
        return 2
    record = run.freeze_record(config, load_policy(packaged("policy.md")))
    path = ROOT / run.FREEZE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"frozen {record['digest']} -> {path}")
    return 0


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Read `KEY=value` lines from a git-ignored `.env` into the environment. A variable that is
    already set (and not empty) wins; an empty value is skipped; a ` # comment` tail is dropped.
    Returns the names set (values are never printed)."""
    import os
    import re

    env: MutableMapping[str, str] = os.environ if environ is None else environ
    if not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():  # -sig drops a BOM
        m = re.fullmatch(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*", line)
        if not m or env.get(m.group(1)):  # set and non-empty wins; empty counts as unset
            continue
        raw = m.group(2)
        quoted = re.fullmatch(r"(['\"])(.*?)\1(?:\s+#.*)?", raw)
        # quoted: kept as written, '#' included; unquoted: an inline comment is dropped
        value = quoted.group(2) if quoted else re.sub(r"(^|\s+)#.*$", "", raw)
        if not value:
            continue  # an empty value counts as unset, so other credential sources still apply
        env[m.group(1)] = value
        names.append(m.group(1))
    return names


def _doctor(args: argparse.Namespace) -> int:
    from fraudreview.doctor import exit_code, run_checks

    load_env_file(Path(".env"))
    checks = run_checks(args.config, args.prices, offline=args.offline, smoke=args.smoke)
    for c in checks:
        print(f"{c.status.upper():<5} {c.name:<36} {c.detail}")
    code = exit_code(checks)
    if args.record:
        if args.offline:
            print("--record needs the online checks; drop --offline", file=sys.stderr)
            return 2
        if code != 0:
            print("not recording: a check failed", file=sys.stderr)
            return code
        return _record(args)
    return code


def _record(args: argparse.Namespace, client_factory: Callable[[], Any] | None = None) -> int:
    from fraudreview.config import load_config
    from fraudreview.doctor import _anthropic_client, price_table, record_preflight

    config = load_config(args.config)
    if config.provider != "anthropic":
        print("--record supports provider anthropic only", file=sys.stderr)
        return 1
    system = packaged("policy.md").read_text(encoding="utf-8")
    try:
        report = record_preflight(
            (client_factory or _anthropic_client)(),
            config,
            price_table(args.prices, "anthropic"),
            system,
        )
    except Exception as exc:  # name the failure; show no secrets
        print(f"preflight failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"recorded {args.record} (about ${report['total_cost_usd']:.4f})")
    return 0


def _baseline(args: argparse.Namespace) -> int:
    from fraudreview.baseline import BaselineError, run_baseline
    from fraudreview.config import ConfigError, load_config
    from fraudreview.data import DataError

    try:
        seed = load_config(args.config).seed
        report = run_baseline(args.data_dir, args.out, seed)
    except ConfigError as exc:
        print(f"fraud baseline: {exc}", file=sys.stderr)
        return 2
    except (BaselineError, DataError, OSError) as exc:
        print(f"fraud baseline: {exc}", file=sys.stderr)
        return 1
    s = report["summary"]
    print(f"threshold {s['threshold']:.6f} ({report['threshold']['reason']}; simulated data)")
    print(
        f"validation: recall {s['recall_val']:.1%}, precision {s['precision_val']:.1%} (simulated)"
    )
    rt, pt = s["recall_test"], s["precision_test"]
    print(
        f"test: recall {rt['point']:.1%} [{rt['lo']:.1%}, {rt['hi']:.1%}], precision "
        f"{pt['point']:.1%} [{pt['lo']:.1%}, {pt['hi']:.1%}] (simulated)"
    )
    for key, mix in report["alert_mix"].items():
        print(
            f"{key}: {mix['alerts']} alerts, {mix['fraud']} fraud "
            f"({mix['fraud_share']:.1%}, simulated)"
        )
    print(f"wrote {args.out}")
    return 0


def _load(args: argparse.Namespace) -> int:
    from fraudreview.data import DataError, download_source, kaggle_auth, load

    try:
        if not args.offline:
            download_source(args.raw_dir, auth=kaggle_auth)  # read only if needed
        reference = None
        if REFERENCE_MANIFEST.is_file():
            try:
                reference = json.loads(REFERENCE_MANIFEST.read_text(encoding="ascii"))
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                raise DataError(f"{REFERENCE_MANIFEST}: not valid ASCII JSON") from exc
        if reference is None:
            print(
                "fraud load: no docs/data/splits.json here (not a source checkout); "
                "the result is not compared with a reference",
                file=sys.stderr,
            )
        result = load(args.raw_dir, args.data_dir, reference=reference)
    except (DataError, OSError) as exc:
        print(f"fraud load: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # anything else: name it, no traceback (download errors are
        # already DataErrors without their text, so no credential can reach this message)
        print(f"fraud load: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for name, facts in result.manifest["splits"].items():
        print(
            f"{name:5}  {facts['rows']:>7} rows  {facts['fraud']:>5} fraud "
            f"({facts['fraud_rate']:.3%})  {facts['first_ts'][:10]} to {facts['last_ts'][:10]}"
            "  (simulated)"
        )
    if result.written:
        print(f"wrote {args.data_dir / 'splits.json'}")
    if result.mismatches:
        print(
            "fraud load: the splits differ from docs/data/splits.json; nothing was written:",
            file=sys.stderr,
        )
        for line in result.mismatches:
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Exit codes (§4.3): 0 ok, 1 a threshold failed, 2 configuration or runtime error, 3 the
    spend cap stopped the run (completed work is cached; rerun to resume)."""
    from fraudreview.cost import CapReached
    from fraudreview.llm import ProviderConfigError

    try:
        return _dispatch(argv)
    except ProviderConfigError as exc:
        print(f"fraud: {exc}", file=sys.stderr)
        return 2
    except CapReached as exc:
        print(f"fraud: stopped at the spend cap: {exc}; completed work is cached", file=sys.stderr)
        return 3


def _use_packaged_defaults(args: argparse.Namespace) -> None:
    """An untouched default path that is not in the current folder falls back to the copy that
    ships with the package, so a pip install runs outside a clone."""
    for attr, name in (("config", "config.yaml"), ("prices", "prices.yaml")):
        given = getattr(args, attr, None)
        if given is not None and given == Path(name) and not given.exists():
            setattr(args, attr, packaged(name))


def _dispatch(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _use_packaged_defaults(args)
    if args.command == "load":
        return _load(args)
    if args.command == "baseline":
        return _baseline(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "triage":
        return _triage(args)
    if args.command == "freeze":
        return _freeze(args)
    if args.command == "monitor":
        return _monitor(args)
    if args.command == "evaluate":
        return _evaluate(args)
    if args.command == "report":
        return _report(args)
    if args.command == "experiment":
        return _experiment(args)
    if args.command == "demo-cache":
        return _demo_cache(args)
    if args.command == "judge":
        return _judge(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
