"""The `fraud` command. Later nodes add `doctor`, `load`, `baseline`, `triage`, `evaluate`,
`report` and `demo-cache` (Tech Spec §4.3)."""

from __future__ import annotations

import argparse
import sys

from fraudreview import __version__


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fraud", description=__doc__)
    ap.add_argument("--version", action="version", version=f"fraud {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    del args
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
