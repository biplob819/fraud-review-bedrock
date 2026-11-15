#!/usr/bin/env python3
"""Fail if anything that looks like an API key is in the repo.

Scans tracked and untracked (not ignored) files. Lines containing the marker
`secret-scan: allow` are skipped (for documented fake examples only).

Known limit: a key split across string literals ("sk-ant-" "abc...") is not detected.
Provider-side secret scanning on GitHub is the backstop for that case.

Usage:
    python scripts/secret_scan.py [paths...]
    python scripts/secret_scan.py --self-test
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from harness_lib import ROOT

PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai key": re.compile(
        r"(?<![A-Za-z0-9])sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}"
    ),
    "aws access key": re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    "aws secret key": re.compile(
        r"(?i)aws_?secret_?(?:access_?)?key\W{0,4}[=:]\W{0,4}[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+])"
    ),
    "github token": re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,}(?![A-Za-z0-9])"),
    "github fine-grained token": re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{50,}"),
    "aws secret in json/yaml/repr": re.compile(
        r"""(?i)["']?(?:SecretAccessKey|aws[_-]secret[_-](?:access[_-])?key|secret_key)["']?\s*[:=]\s*["']?[A-Za-z0-9/+=]{40}"""
    ),
    "aws secret in xml": re.compile(
        r"<(?:SecretAccessKey|SessionToken)>[A-Za-z0-9/+=]{40,}</(?:SecretAccessKey|SessionToken)>"
    ),
    "aws session token": re.compile(
        r"""(?i)["']?(?:aws[_-]session[_-]token|SessionToken|Token)["']?\s*[:=]\s*["']?[A-Za-z0-9/+=]{100,}"""
    ),
    "aws sts text credentials": re.compile(
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\s+\S+\s+[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+])"
    ),
    "bedrock api key": re.compile(r"(?<![A-Za-z0-9])ABSK[A-Za-z0-9+/=]{40,}"),
    "bedrock short-term api key": re.compile(r"bedrock-api-key-[A-Za-z0-9+/=%&_-]{40,}"),
    "bedrock bearer token": re.compile(
        r"(?i)AWS_BEARER_TOKEN_BEDROCK\W{0,4}[=:]\W{0,4}[A-Za-z0-9+/=%&_-]{20,}"
    ),
    "kaggle key": re.compile(
        r"""["']key["']\s*:\s*["'][0-9a-f]{32}["']|KAGGLE_KEY\W{0,4}[=:]\W{0,4}[0-9a-f]{32}"""
    ),
    "slack token": re.compile(r"(?<![A-Za-z0-9])xox[abprse]-[A-Za-z0-9-]{10,}"),
    "slack app token": re.compile(r"(?<![A-Za-z0-9])xapp-[0-9]-[A-Za-z0-9-]{10,}"),
    "slack webhook": re.compile(
        r"hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/]{16,}"
    ),
    "pypi token": re.compile(r"(?<![A-Za-z0-9])pypi-[A-Za-z0-9_\-]{50,}"),
    "private key": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----"
    ),
}
ALLOW = "secret-scan: allow"
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".whl", ".gz", ".zip", ".ico"}


def candidate_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [root / p for p in out.stdout.splitlines() if p]
    except (OSError, subprocess.CalledProcessError) as exc:
        if (root / ".git").exists():  # a repository git cannot list is a tool error, not a pass
            raise RuntimeError(f"git could not list files to scan: {exc}") from exc
        skip = {".git", ".venv", "node_modules", "__pycache__", "dist"}
        return [p for p in root.rglob("*") if p.is_file() and not (skip & set(p.parts))]


def scan(files: list[Path]) -> list[str]:
    hits: list[str] = []
    for f in files:
        if f.suffix.lower() in SKIP_SUFFIXES or not f.is_file():
            continue
        try:
            # never skip a file for its encoding: undecodable bytes become U+FFFD
            text = f.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if ALLOW in line:
                continue
            for name, pat in PATTERNS.items():
                if pat.search(line):
                    hits.append(f"{f}:{lineno}: possible {name}")
    return hits


def self_test() -> int:
    fake_ant = "sk-ant-" + "a1B2" * 10
    fake_oai = "sk-proj-" + "Z9y8" * 10
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "leak.py"
        bad.write_text(f'KEY = "{fake_ant}"\nOTHER = "{fake_oai}"\n', encoding="utf-8")
        allowed = Path(tmp) / "doc.md"
        allowed.write_text(f"example: {fake_ant}  # {ALLOW}\n", encoding="utf-8")
        clean = Path(tmp) / "clean.py"
        clean.write_text('KEY = os.environ["ANTHROPIC_API_KEY"]\n', encoding="utf-8")
        checks = [
            ("detects leaked keys", len(scan([bad])) == 2),
            ("honours allow marker", scan([allowed]) == []),
            ("clean file passes", scan([clean]) == []),
        ]
    for name, ok in checks:
        failures += 0 if ok else 1
        print(f"  [{'ok' if ok else 'WRONG'}] {name}")
    print("self-test", "PASSED" if failures == 0 else f"FAILED ({failures})")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    files = args.paths or candidate_files(ROOT)
    hits = scan(files)
    if hits:
        print("secret scan: FAIL")
        for h in hits:
            print(f"  - {h}")
        return 1
    print(f"secret scan: OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
