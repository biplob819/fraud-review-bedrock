"""B3.2: the PR/FAQ has its three parts, stays short, and its numbers match the evaluation."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "docs" / "pr-faq.md").read_text(encoding="utf-8")
SUMMARY = json.loads(
    (ROOT / "docs" / "measurements" / "summary-eval2.json").read_text(encoding="utf-8")
)


def test_has_press_release_customer_faq_and_internal_faq_in_two_pages() -> None:
    for part in ("## Press release", "## Customer FAQ", "## Internal FAQ"):
        assert part in TEXT
    assert len(TEXT.split()) <= 1000  # about two pages


def test_internal_faq_names_model_risk_review_the_leakage_bar_and_real_data() -> None:
    internal = TEXT.split("## Internal FAQ")[1].lower()
    assert "model risk review" in internal
    assert "leakage bar" in internal
    assert "real data" in internal


def test_every_number_matches_the_summary() -> None:
    s = SUMMARY
    leak, clear = s["leakage"], s["auto_clear_rate"]
    assert (leak["k"], leak["n"]) == (0, 55) and "0 of 55" in TEXT
    assert f"{leak['hi'] * 100:.1f}%" in TEXT
    assert (clear["k"], clear["n"]) == (44, 500)
    assert f"({clear['k']} of {clear['n']}" in TEXT and f"{clear['point'] * 100:.1f}%" in TEXT
    cost = s["cost_per_1000_usd"]["point"] if "cost_per_1000_usd" in s else None
    assert cost is not None and f"${cost:.2f} per 1,000" in TEXT
    values = s["explanation"]["value_shares"]
    assert "1,098 of 1,098" in TEXT and values["verified"] == 1098 and values["contradicted"] == 0
    assert "2 of 79" in TEXT and "36.8%" in TEXT  # the first run, from eval-runs.jsonl
    first = json.loads(
        (ROOT / "docs" / "measurements" / "eval-runs.jsonl").read_text("utf-8").splitlines()[0]
    )
    assert first["leakage"]["k"] == 2 and first["leakage"]["n"] == 79
    assert abs(first["auto_clear_rate"]["point"] - 0.368) < 0.001


def test_it_is_published_and_says_simulated_up_front() -> None:
    assert "docs/pr-faq.md" in (ROOT / "docs" / "published.txt").read_text(encoding="utf-8")
    assert "simulated" in TEXT.split("\n")[2].lower()


def test_each_headline_number_sits_in_a_sentence_labelled_simulated() -> None:
    for number in ("0 of 55", "44 of 500", "2 of 79", "$6.24 per 1,000"):
        sentence = next(s for s in TEXT.replace("\n", " ").split(". ") if number in s)
        assert "simulated" in sentence.lower(), number


def test_the_late_night_rule_is_described_with_the_policy_hours() -> None:
    policy = (ROOT / "policy.md").read_text(encoding="utf-8")
    assert "hour >= 22 or hour < 6" in policy
    assert "10 p.m. to 6 a.m." in TEXT
    assert "invented" not in TEXT and 'says. "' not in TEXT  # no made-up quote
