"""The patterns page names real paths, quotes real code, and states limits (node B3.5)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "patterns.md"
TEXT = PAGE.read_text(encoding="utf-8")

PATH_RE = re.compile(r"`([\w./-]+\.(?:py|md|yaml|json|jsonl|txt|html))`")
EXCERPT_RE = re.compile(r"Excerpt from `([^`]+)`:\n\n```\w*\n(.*?)```", re.S)
PATTERN_RE = re.compile(r"^## (PAT-0\d): .*?(?=^## |\Z)", re.S | re.M)


def test_every_named_repo_path_exists() -> None:
    paths = set(PATH_RE.findall(TEXT))
    assert paths
    missing = sorted(p for p in paths if not (ROOT / p).exists())
    assert not missing, missing


def test_every_excerpt_appears_verbatim_in_its_file() -> None:
    excerpts = EXCERPT_RE.findall(TEXT)
    assert len(excerpts) >= 4
    for path, code in excerpts:
        assert len(code.strip("\n").splitlines()) <= 10, path
        source = (ROOT / path).read_text(encoding="utf-8")
        assert code in source, path


def test_the_page_has_four_patterns_each_with_the_parts_and_limits() -> None:
    sections = {m.group(1): m.group(0) for m in PATTERN_RE.finditer(TEXT)}
    assert sorted(sections) == ["PAT-01", "PAT-02", "PAT-03", "PAT-04"]
    for pat, body in sections.items():
        for part in ("Problem", "Pattern", "Where it lives", "Evidence", "Limits"):
            assert re.search(rf"^### {part}", body, re.M), (pat, part)
        limits = body.split("### Limits", 1)[1].strip()
        assert limits.startswith("- "), pat


def test_the_page_has_the_closing_sections_and_is_published() -> None:
    assert "## When not to use these" in TEXT
    assert "## What real data would change" in TEXT
    listed = (ROOT / "docs" / "published.txt").read_text(encoding="utf-8").splitlines()
    assert "docs/patterns.md" in listed
