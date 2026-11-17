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
    tri.add_argument("--split", choices=("dev", "eval"), required=True)
    tri.add_argument("--alerts", type=int, help="how many alerts (default: the whole sample)")
    tri.add_argument("--dry-run", action="store_true", help="estimate only; no model calls")
    tri.add_argument("--config", type=Path, default=Path("config.yaml"))
    tri.add_argument("--prices", type=Path, default=Path("prices.yaml"))
    tri.add_argument("--cases", type=Path, default=Path("results/baseline"), help="alert samples")
    return ap


def _triage(args: argparse.Namespace) -> int:
    from fraudreview.config import ConfigError, load_config
    from fraudreview.cost import dry_run_lines, estimate_run
    from fraudreview.doctor import price_table

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"fraud triage: {exc}", file=sys.stderr)
        return 2
    if not args.dry_run:
        print("fraud triage: live runs arrive in A2.5; use --dry-run", file=sys.stderr)
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
    prices = price_table(args.prices, config.provider)
    missing = [m for m in (config.stage1.model, config.stage2.model) if m not in prices]
    if missing:
        print(
            f"fraud triage: no {config.provider} prices for {', '.join(missing)}", file=sys.stderr
        )
        return 2
    from fraudreview.llm import check_price

    for m in (config.stage1.model, config.stage2.model):
        check_price(prices[m])  # a bad price is a config error (exit 2), not "$nan"
    est = estimate_run(n, prices, config)
    print(f"dry run: {args.split}, {n} alerts, provider {config.provider}")
    for line in dry_run_lines(est, config):
        print(line)
    if est.total_usd > config.max_cost_usd:
        print(
            f"fraud triage: the estimate ${est.total_usd:.2f} is over max_cost_usd "
            f"${config.max_cost_usd:g}; lower --alerts or raise the cap",
            file=sys.stderr,
        )
        return 2
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
    system = (ROOT / "policy.md").read_text(encoding="utf-8")
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


def _dispatch(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "load":
        return _load(args)
    if args.command == "baseline":
        return _baseline(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "triage":
        return _triage(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
