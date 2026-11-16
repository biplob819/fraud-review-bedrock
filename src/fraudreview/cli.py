"""The `fraud` command. Later nodes add `doctor`, `baseline`, `triage`, `evaluate`, `report`
and `demo-cache` (Tech Spec §4.3)."""

from __future__ import annotations

import argparse
import json
import sys
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
    return ap


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
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
