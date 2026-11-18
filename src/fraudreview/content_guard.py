"""The public-content guard's term list, in one place (D4.1).

The repo is public, so nothing private may reach a tracked file. The guard has two parts:
generic patterns written here (built from pieces so this file never matches its own scan) and
private terms, one regular expression per line, in the git-ignored `.public-guard.local` at the
repository root. `tests/harness/test_records.py` scans tracked files with `private_patterns()`;
`fraud demo-cache` uses `find_private()` to leave out any case whose text matches, because
merchant names in the simulated data have matched in the past.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD_FILE = ROOT / ".public-guard.local"
_HOME = "~" + "/"
_USERS = "/" + "Users" + "/"
GENERIC_PRIVATE = [
    re.escape(_HOME) + r"(?!\.(?:kaggle|aws|config)/)",  # standard tool config paths are fine
    re.escape(_USERS),
]


def private_patterns(guard_file: Path | None = None) -> list[str]:
    """Generic patterns plus every non-comment line of the private guard file, if present."""
    path = GUARD_FILE if guard_file is None else guard_file
    pats = list(GENERIC_PRIVATE)
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def guard_regex(guard_file: Path | None = None) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in private_patterns(guard_file)), re.I)


def find_private(texts: Iterable[str], guard_file: Path | None = None) -> str | None:
    """The first guarded match in any text (the matched text), or None when all are clean."""
    banned = guard_regex(guard_file)
    for text in texts:
        m = banned.search(text)
        if m:
            return m.group(0)
    return None
