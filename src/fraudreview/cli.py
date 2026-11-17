"""The `fraud` command. Later nodes add `doctor`, `baseline`, `triage`, `evaluate`, `report`
and `demo-cache` (Tech Spec §4.3)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import MutableMapping
from pathlib import Path

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
    return ap


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Read `KEY=value` lines from a git-ignored `.env` into the environment. A variable that is
    already set wins; an empty value is skipped; an unquoted ` # comment` tail is dropped.
    Returns the names set (values are never printed)."""
    import os
    import re

    env: MutableMapping[str, str] = os.environ if environ is None else environ
    if not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*", line)
        if not m or m.group(1) in env:
            continue
        value = m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]  # quoted: kept as written, '#' included
        else:
            value = re.sub(r"(^|\s+)#.*$", "", value)  # an unquoted inline comment
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
    if args.record and code == 0 and not args.offline:
        return _record(args)
    return code


def _record(args: argparse.Namespace) -> int:
    from fraudreview.config import load_config
    from fraudreview.doctor import _anthropic_client, price_table, record_preflight

    config = load_config(args.config)
    if config.provider != "anthropic":
        print("--record supports provider anthropic only", file=sys.stderr)
        return 1
    system = (ROOT / "policy.md").read_text(encoding="utf-8")
    try:
        report = record_preflight(
            _anthropic_client(), config, price_table(args.prices, "anthropic"), system
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "load":
        return _load(args)
    if args.command == "baseline":
        return _baseline(args)
    if args.command == "doctor":
        return _doctor(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
