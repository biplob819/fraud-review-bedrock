"""D4.2: the project page links what it should, tells the truth, and its numbers match."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "project.md"
TEXT = PAGE.read_text(encoding="utf-8")
SUMMARY = json.loads(
    (ROOT / "docs" / "measurements" / "summary-eval2.json").read_text(encoding="utf-8")
)


def _links() -> list[str]:
    found = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", TEXT)
    return [x for x in found if not x.startswith("http")]


def test_every_link_and_image_exists() -> None:
    links = _links()
    assert len(links) >= 12
    for rel in links:
        assert (PAGE.parent / rel).resolve().exists(), rel


def test_it_links_the_prd_decision_log_data_card_and_build_notes() -> None:
    for name in ("PRD.md", "decisions.md", "DATA_CARD.md", "lessons.md"):
        assert any(link.endswith(name) for link in _links()), name


def test_numbers_match_the_summary_and_the_first_run() -> None:
    leak, clear = SUMMARY["leakage"], SUMMARY["auto_clear_rate"]
    assert (leak["k"], leak["n"]) == (0, 55) and "0 of 55" in TEXT
    assert f"{leak['hi'] * 100:.1f}%" in TEXT
    assert f"({clear['k']} of {clear['n']})" in TEXT and f"{clear['point'] * 100:.1f}%" in TEXT
    assert f"${SUMMARY['cost_per_1000_usd']['point']:.2f} per 1,000" in TEXT
    first = json.loads(
        (ROOT / "docs" / "measurements" / "eval-runs.jsonl").read_text("utf-8").splitlines()[0]
    )
    assert (first["leakage"]["k"], first["leakage"]["n"]) == (2, 79) and "2 of 79" in TEXT


def test_it_is_honest_about_the_video_the_demo_and_the_faithfulness_result() -> None:
    assert "video is not part of this release" in TEXT
    assert "hosted demo is planned for v0.2" in TEXT
    assert "single-judge partial" in TEXT and "not calibrated" in TEXT


def test_it_is_published_and_names_no_private_file() -> None:
    assert "docs/project.md" in (ROOT / "docs" / "published.txt").read_text(encoding="utf-8")
    assert "NOTES.local" not in TEXT


def test_the_one_page_prd_is_one_page_and_says_what_changed() -> None:
    page = (ROOT / "docs" / "prd-onepager.md").read_text(encoding="utf-8")
    assert len(page.split()) <= 450
    for word in ("D-35", "D-44", "D-45", "D-43", "0 of 55"):
        assert word in page
    assert "docs/prd-onepager.md" in (ROOT / "docs" / "published.txt").read_text(encoding="utf-8")
    assert any(x.endswith("prd-onepager.md") for x in _links())
