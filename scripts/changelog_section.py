#!/usr/bin/env python3
"""Print the changelog section for a release tag (OSS-04).

A section starts at a `## ` heading. The section for `v0.1.0` is the one whose heading holds
`v0.1.0` or `0.1.0`. When no heading names the version, the newest section (the first one) is used
and a note says so, so a release never goes out with empty notes.

Usage:
    python scripts/changelog_section.py v0.1.0 [changelog.md]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) for every `## ` section, in file order."""
    found: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            found.append((line[3:].strip(), []))
        elif found:
            found[-1][1].append(line)
    return [(h, "\n".join(body).strip()) for h, body in found]


def section_for(tag: str, text: str) -> str:
    """The notes for `tag`: the named section, else the newest one with a note saying so."""
    version = tag.removeprefix("v")
    found = sections(text)
    if not found:
        raise ValueError("the changelog has no `## ` section")
    for heading, body in found:
        if re.search(rf"(?<![\d.]){re.escape(version)}(?![\d.])", heading):
            return body
    heading, body = found[0]
    note = f"_The changelog has no section for {tag}; these are the newest entries ({heading})._"
    return f"{note}\n\n{body}"


def main(argv: list[str]) -> int:
    if not argv or len(argv) > 2:
        print("usage: changelog_section.py <tag> [changelog.md]", file=sys.stderr)
        return 2
    path = Path(argv[1]) if len(argv) == 2 else ROOT / "changelog.md"
    try:
        print(section_for(argv[0], path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        print(f"changelog_section: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
