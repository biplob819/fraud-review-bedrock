"""The strict value check: a false claim is never `verified` (E2.5)."""

import random
from typing import Any

import pytest

from fraudreview import valuecheck as vc
from fraudreview.valuecheck import check_reason, shares

REC: dict[str, Any] = {
    "ts_hour": 22,
    "ts_dow": 3,
    "category": "travel",
    "amount": 11.01,
    "risk_score": 0.0386,
    "amt_vs_usual": 0.2134,
    "hour": 22,
    "category_freq": 0.038,
    "merchant_first_seen": False,
    "card_history_n": 1545,
}

# ---------------------------------------------------------------- the mutation generator

# claim templates per field: {v} is the number; a template with "%" shows v * 100
EQ: dict[str, list[str]] = {
    "amt_vs_usual": [
        "Amount is {v} times the card's usual",
        "{v}x the usual amount",
        "{v}× the card's usual",
        "amt_vs_usual is {v}",
        "about {v} times its typical spend",
        "The purchase is {v} times the card's median",
        "{v} times usual",
    ],
    "amount": [
        "Amount is {v}",
        "The purchase amount of ${v}",
        "A ${v} purchase",
        "amount {v}",
        "Purchase amount is {v} dollars",
    ],
    "risk_score": [
        "Risk score is {v}",
        "risk_score {v}",
        "The screening model's fraud score is {v}",
        "Risk score is low at {v}",
        "Risk score is {p}%",
    ],
    "category_freq": [
        "category_freq {v}",
        "Category travel is {p}% of the card's earlier purchases",
        "category share is only {v}",
        "The category makes up about {p}% of the card's purchases",
        "category_freq is {p}%",
    ],
    "card_history_n": [
        "Card has {v} earlier purchases",
        "{v} prior transactions",
        "card_history_n is {v}",
        "The card history of {v} earlier purchases",
    ],
    "hour": [
        "hour {v}",
        "Purchase hour is {v}",
        "at hour {v}",
        "The hour is {v}:00",
        "Purchase hour is {c12}",
    ],
}
# comparison templates: {c} the comparison words, {v} the bound
CMP: dict[str, list[str]] = {
    "amount": ["Amount is {c} {v}", "The purchase is {c} ${v}", "purchase amount {c} {v}"],
    "amt_vs_usual": ["{c} {v} times the card's usual", "Amount is {c} {v}x the usual"],
    "risk_score": ["risk score {c} {v}", "Risk score is {c} {v}"],
    "category_freq": ["category_freq {c} {v}", "category_freq is {c} {p}%"],
    "card_history_n": ["{c} {v} earlier purchases", "Card has {c} {v} prior transactions"],
    "hour": ["hour is {c} {v}", "Purchase hour {c} {v}"],
}
COMPARATORS: dict[str, str] = {
    "under": "<",
    "below": "<",
    "less than": "<",
    "over": ">",
    "above": ">",
    "more than": ">",
    "at least": ">=",
    "not above": "<=",
    "not over": "<=",
    "not more than": "<=",
    "not below": ">=",
    "not under": ">=",
    "not less than": ">=",
}
MERCHANT_TRUE = [
    "First purchase at this merchant",
    "This is a new merchant for the card",
    "merchant_first_seen is true",
    "The card has never been used at this merchant",
]
MERCHANT_FALSE = [
    "The merchant is known to the card",
    "A known merchant",
    "The merchant is not new",
    "merchant_first_seen is false",
]
JOINERS = [", ", " and ", "; ", ". ", " but ", " (", " so ", " which "]

NUMERIC = tuple(vc.KINDS.keys() - {"merchant_first_seen"})


def sample_record(rng: random.Random) -> dict[str, Any]:
    hour = rng.randrange(24)
    return {
        "ts_hour": hour,
        "ts_dow": rng.randrange(7),
        "category": "travel",
        "amount": round(rng.choice([rng.uniform(1, 60), rng.uniform(1, 1600)]), 2),
        "risk_score": rng.choice([rng.random() * 0.2, rng.random()]),
        "amt_vs_usual": rng.choice([rng.uniform(0.05, 2), rng.uniform(0, 15)]),
        "hour": hour,
        "category_freq": rng.choice([rng.random() * 0.12, rng.random()]),
        "merchant_first_seen": rng.random() < 0.5,
        "card_history_n": rng.choice([rng.randrange(6), rng.randrange(3000)]),
    }


def _num(x: float) -> str:
    text = f"{x:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _clock(h: int) -> str:
    return f"{h % 12 or 12} {'p' if h >= 12 else 'a'}.m."


def render(template: str, field: str, v: float, comparator: str = "") -> str:
    """Fill a template; `v` is in the field's own units (a share, not a percent)."""
    if field in ("card_history_n", "hour"):
        shown = str(int(v)) if v == int(v) else _num(v)
    elif field in ("category_freq", "risk_score"):
        shown = _num(v)
    else:
        shown = _num(v)
    return template.format(
        v=shown,
        p=_num(v * 100),
        c=comparator,
        c12=_clock(int(v)) if v == int(v) and 0 <= v <= 23 else shown,
    )


def parsed(template: str, field: str, v: float) -> float:
    """The value a reader gets from what was printed (the rounding of `_num`)."""
    pct = "{p}%" in template
    return float(_num(v * 100 if pct else v)) / (100 if pct else 1)


def tol(t: float) -> float:
    return max(vc.ABS_TOL, vc.REL_TOL * abs(t))


def false_eq(field: str, template: str, v: float, rec: dict[str, Any]) -> bool:
    """The oracle: is the printed equality claim false for this record?"""
    t = float(rec[field])
    if "{c12}" in template:
        if v != int(v) or not 0 <= v <= 23:
            return False  # printed as a bare number that the template does not bind: skip it
        return int(v) != int(t)
    pv = parsed(template, field, v)
    if field in ("card_history_n", "hour"):
        return pv != t
    return abs(pv - t) > tol(t) + 1e-9


def holds(op: str, t: float, bound: float) -> bool:
    return {"<": t < bound, ">": t > bound, "<=": t <= bound, ">=": t >= bound}[op]


def wrong_values(field: str, t: float, rec: dict[str, Any], rng: random.Random) -> list[float]:
    out = [
        t * 1.5 + 1,
        t * 0.5,
        t + 7,
        abs(t - 3),
        t * 10,
        t / 10,
        t + 1.01 * tol(t),
        t - 1.01 * tol(t),
    ]
    if field in ("card_history_n", "hour"):
        out += [t + 1, t - 1, t + 2, t * 2, 0, 23 - t if field == "hour" else t + 5]
    out += [
        float(rec[o])
        for o in NUMERIC
        if o != field and isinstance(rec.get(o), int | float)  # a swapped field's value
    ]
    out += [rng.uniform(0, 2000) for _ in range(2)]
    return [x for x in out if x >= 0]


def false_variants(rng: random.Random, rec: dict[str, Any]) -> list[tuple[str, str]]:
    """(text, why) for claims that are false against `rec`, over every pattern."""
    out: list[tuple[str, str]] = []
    for field in NUMERIC:
        t = float(rec[field])
        for tpl in EQ[field]:
            for v in wrong_values(field, t, rec, rng):
                if false_eq(field, tpl, v, rec):
                    out.append((render(tpl, field, v), f"wrong number/{field}"))
            # a swapped field name: the true value of this field, claimed under another name
            for other in NUMERIC:
                if other == field:
                    continue
                for otpl in EQ[other]:
                    if false_eq(other, otpl, t, rec):
                        out.append((render(otpl, other, t), f"swapped {field}->{other}"))
        for tpl in CMP[field]:
            for word, op in COMPARATORS.items():
                for bound in (t * 0.5, t * 2 + 1, t - 1, t + 1, t / 10, t * 10, 5.0, 50.0, 0.05):
                    if bound < 0:
                        continue
                    pv = parsed(tpl, field, bound)
                    if not holds(op, t, pv):
                        out.append((render(tpl, field, bound, word), f"flipped/{field}"))
    for text in MERCHANT_TRUE if not rec["merchant_first_seen"] else MERCHANT_FALSE:
        out.append((text, "merchant polarity"))
    return out


def true_variants(rng: random.Random, rec: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for field in NUMERIC:
        t = float(rec[field])
        for tpl in EQ[field]:
            if "{c12}" in tpl:
                out.append(render(tpl, field, t))
                continue
            out.append(render(tpl, field, t))
        for tpl in CMP[field]:
            for word, op in COMPARATORS.items():
                for bound in (t * 0.5, t * 2 + 1, t - 1, t + 1, 5.0, 50.0):
                    if field in ("risk_score", "category_freq") and bound > 1:
                        continue  # a bound above 1 is neither a share nor a percent
                    if bound >= 0 and holds(op, t, parsed(tpl, field, bound)):
                        out.append(render(tpl, field, bound, word))
    out += MERCHANT_FALSE if not rec["merchant_first_seen"] else MERCHANT_TRUE
    return out


@pytest.mark.safety
def test_no_false_variant_is_ever_verified():
    rng = random.Random(20260517)
    total = slipped_through = contradicted = 0
    slips: list[tuple[str, str]] = []
    for _ in range(40):
        rec = sample_record(rng)
        variants = false_variants(rng, rec)
        # the same false claims inside a longer reason, beside true ones
        trues = true_variants(rng, rec)
        for text, why in list(variants):
            joiner = rng.choice(JOINERS)
            other = rng.choice(trues)
            mixed = f"{other}{joiner}{text}" if rng.random() < 0.5 else f"{text}{joiner}{other}"
            if joiner == " (":
                mixed += ")"
            variants.append((mixed, why + " (in a longer reason)"))
        for text, why in variants:
            total += 1
            outcome = check_reason(text, rec)
            contradicted += outcome == "contradicted"
            if outcome == "verified":
                slipped_through += 1
                slips.append((text, why))
    assert total >= 3000, total
    assert slipped_through == 0, slips[:10]
    assert contradicted > total // 4, (contradicted, total)  # the check is not just giving up


@pytest.mark.safety
def test_hand_written_false_and_tricky_claims_are_never_verified():
    cases = [
        "Amount is 12.01",  # off by one
        "Amount is not 11.01",
        "Amount is 11.01 times the card's usual",  # the right number on the wrong field
        "Amount is 0.2134 times the card's usual, not 11.01",
        "Hour is 21",
        "hour is not 22",
        "Purchase hour is 9 p.m.",
        "Purchase hour is 10 a.m.",
        "Hour is 22 (10 a.m.)",
        "category_freq is 0.38",
        "category_freq is 38%",
        "category_freq is 0.38%",
        "Category travel is 38% of the card's earlier purchases",
        "category_freq is not below 0.05 so it is not rare",
        "amount is not under 50",
        "The card has 1546 earlier purchases",
        "The card has 154 earlier purchases",
        "The card has no earlier purchases",
        "First purchase at this merchant",
        "The merchant is new",
        "This is not the first purchase at this merchant",
        "The merchant is not known to the card",
        "Not a known merchant",
        "The merchant is known to the card and it is the first purchase at this merchant",
        "Amount is 11.01 and the amount is 12.01",
        "Amount is 11.01 respectively 12.01",
        "Amount is under 5",
        "amount is above 11.01",  # boundary: not strictly above
        "amount is below 11.01",
        "risk score is above 0.0386",
        "risk score of 0.9",
        "Risk score is 3.86",
    ]
    for text in cases:
        assert check_reason(text, REC) != "verified", text


# ---------------------------------------------------------------- true claims

TRUE_CLAIMS = [
    "Amount is 11.01",
    "The purchase amount of $11.01",
    "A $11.01 purchase",
    "amount is under 50",
    "The purchase is below $50",
    "amount is not above 50",
    "Amount is 0.21 times the card's usual",
    "0.2134x the usual amount",
    "0.21× the card's usual",
    "amt_vs_usual is 0.213",
    "Amount is below 5 times the card's usual",
    "Risk score is 0.04",
    "risk_score 0.0386",
    "Risk score is low at 0.0386",
    "risk score is 3.9%",
    "risk score is below 0.9",
    "category_freq 0.038",
    "Category travel is 3.8% of the card's earlier purchases",
    "Category share is only 0.038",
    "category_freq is below 0.05",
    "category_freq is not above 0.05",
    "Card has 1545 earlier purchases",
    "The card has 1,545 earlier purchases",
    "1545 prior transactions",
    "card_history_n is 1545",
    "The card has more than 1000 earlier purchases",
    "Purchase hour is 22",
    "hour 22",
    "The hour is 22:00",
    "Purchase hour is 10 p.m.",
    "Purchase hour is 22 (10 p.m.)",
    "hour is at least 22",
    "The merchant is known to the card",
    "The merchant is not new (merchant_first_seen is false)",
    "merchant_first_seen is false",
    "Amount is 11.01, which is 0.21 times the card's usual",
    "Purchase hour is 22 and the card has 1545 earlier purchases",
]


@pytest.mark.parametrize("text", TRUE_CLAIMS)
def test_true_claims_are_verified(text):
    assert check_reason(text, REC) == "verified"


def test_a_true_claim_of_every_pattern_is_verified_over_many_records():
    rng = random.Random(7)
    n = 0
    for _ in range(30):
        rec = sample_record(rng)
        for text in true_variants(rng, rec):
            assert check_reason(text, rec) == "verified", (text, rec)
            n += 1
    assert n > 1500


@pytest.mark.parametrize(
    "text",
    [
        "Amount is 12.01",
        "Amount is 2 times the card's usual",
        "Hour is 3",
        "category_freq is 0.4",
        "category_freq is 0.38%",
        "Card has 7 earlier purchases",
        "amount is over 50",
        "risk score is above 0.9",
        "The merchant is new",
        "First purchase at this merchant",
        "Purchase hour is 9 p.m.",
    ],
)
def test_false_claims_are_contradicted(text):
    assert check_reason(text, REC) == "contradicted"


# ---------------------------------------------------------------- unverifiable


@pytest.mark.parametrize(
    "text",
    [
        "Looks routine",  # no claim
        "",
        "Amount is 11.01, under 50, and 0.21 times the card's usual amount",  # two fields
        "amount 11.01 is 0.21 times the card's usual",  # two fields in one clause
        "Amount is 0.21 times the card's usual, above the 5x fraud signal level",
        "risk score is above the 0.9 threshold",
        "Amount is 11.01 but P3.1 and 5 do not apply",
        "Amount is 11.01 under P3.1",
        "the 5x signal",
        "amt_vs_usual is 0.21 P3.1",  # a clause id in the same clause as a number
        "Purchase hour is 22, inside the 10 p.m. to 6 a.m. window",
        "Purchase at 10 p.m.",  # a clock time with no hour field named
        "Purchase at 22:00",
        "the purchase was at 10 pm, 22 hours",
        "Amount, risk score and hour are 11.01, 0.04 and 22 respectively",
        "amount between 5 and 50",
        "amount is 5-50",
        "amount is not 11.01",  # a negation that is not before a comparison word
        "Amount is not 12.01",
        "amount never exceeded 5.0",
        "The merchant is not a known merchant",
        "Amount is 11.01 or more",
        "category_freq of 0.038 or less",
        "the card's usual amount of 11.01",  # a baseline, not the amount
        "average amount is 11.01",
        "Amount is 11.01 at hour 22",  # two fields
        "There were 12 alerts",  # a number with no field
        "Amount 3 of 4",
        "Hour 25",
    ],
)
def test_unrecognised_or_ambiguous_claims_are_not_verified(text):
    assert check_reason(text, REC) != "verified"


@pytest.mark.parametrize(
    "text",
    [
        "Looks routine",
        "Amount is 11.01, under 50, and 0.21 times the card's usual amount",
        "Amount is 0.21 times the card's usual, above the 5x fraud signal level",
        "risk score is above the 0.9 threshold",
        "amt_vs_usual is 0.21 P3.1",
        "Amount is 11.01 under P3.1",
        "Purchase hour is 22, inside the 10 p.m. to 6 a.m. window",
        "Purchase at 10 p.m.",
        "Purchase at 22:00",
        "Amount, risk score and hour are 11.01, 0.04 and 22 respectively",
        "amount is not 11.01",
        "amount is 11.01 or more",
        "the card's usual amount of 11.01",
        "Amount is 11.01 at hour 22",
        "The merchant is not a known merchant",
    ],
)
def test_these_are_unverifiable(text):
    assert check_reason(text, REC) == "unverifiable"


def test_a_clause_with_a_correct_rounding_outside_the_tolerance_is_unverifiable():
    # 0.2134 is 0.21 to two places and 0.2 to one: 0.2 is a correct rounding, 0.5% off no more
    assert check_reason("Amount is 0.2 times the card's usual", REC) == "unverifiable"
    assert check_reason("Amount is 0.3 times the card's usual", REC) == "contradicted"


def test_a_missing_or_null_field_is_unverifiable():
    rec = {**REC, "amt_vs_usual": None}
    assert check_reason("Amount is 0.21 times the card's usual", rec) == "unverifiable"
    assert check_reason("Amount is 11.01", {}) == "unverifiable"
    assert check_reason("The merchant is known", {"merchant_first_seen": None}) == "unverifiable"
    mismatch = {**REC, "ts_hour": 3}  # the two hour fields disagree: trust neither
    assert check_reason("Purchase hour is 22", mismatch) == "unverifiable"


def test_negation_directly_before_a_comparison_inverts_it_and_any_other_negation_blocks():
    assert check_reason("amount is not above 50", REC) == "verified"
    assert check_reason("amount is not above 5", REC) == "contradicted"
    assert check_reason("amount is not below 5", REC) == "verified"
    assert check_reason("amount isn't above 50", REC) == "verified"
    assert check_reason("amount is no more than 50", REC) == "unverifiable"
    assert check_reason("amount is never above 50", REC) == "unverifiable"
    assert check_reason("amount is not a lot above 5", REC) == "unverifiable"
    assert check_reason("amount is without doubt under 50", REC) == "unverifiable"


def test_percent_and_scale_forms():
    assert check_reason("category_freq is 3.8%", REC) == "verified"
    assert check_reason("category_freq is 38%", REC) == "contradicted"
    assert check_reason("category_freq is 0.38%", REC) == "contradicted"
    assert (
        check_reason("category_freq is 3.8", REC) == "unverifiable"
    )  # neither a share nor a percent
    assert check_reason("Amount is 11.01%", REC) == "unverifiable"
    assert check_reason("The card has 1545% earlier purchases", REC) == "unverifiable"
    assert check_reason("category_freq is 0.04", REC) == "verified"  # within 0.005


# ---------------------------------------------------------------- combining clauses


def test_a_contradicted_clause_makes_the_reason_contradicted():
    assert check_reason("Amount is 11.01, but the hour is 3", REC) == "contradicted"
    assert check_reason("Hour is 3, which is P5.1 night", REC) == "contradicted"


def test_every_claim_clause_must_be_verified():
    assert check_reason("Amount is 11.01 and the hour is 22", REC) == "verified"
    assert check_reason("Amount is 11.01 and the hour is 22, so P5.9 applies", REC) == "verified"
    # a number the checker cannot bind keeps the reason from being verified
    assert check_reason("Amount is 11.01 and 3 alerts were open", REC) == "unverifiable"
    assert (
        check_reason("Amount is 11.01, and the card is in the 5x signal band", REC)
        == "unverifiable"
    )
    # a sentence with no number does not count against it, nor for it
    assert check_reason("Amount is 11.01. The pattern looks routine.", REC) == "verified"
    # a clause that only cites a clause id makes no numeric claim
    assert check_reason("Amount is 11.01 (P3.1)", REC) == "verified"
    assert check_reason("The pattern looks routine. It is fine.", REC) == "unverifiable"


# ---------------------------------------------------------------- decisions and shares


def test_shares_count_and_give_wilson_intervals_over_all_reasons():
    out = shares(["verified", "verified", "contradicted", "unverifiable", "unverifiable"])
    assert (out["n"], out["verified"], out["contradicted"], out["unverifiable"]) == (5, 2, 1, 2)
    assert (out["verified_share"].k, out["verified_share"].n) == (2, 5)
    assert out["contradicted_share"].k == 1
    assert out["verified_share"].lo < 0.4 < out["verified_share"].hi
    empty = shares([])
    assert empty["n"] == 0 and empty["verified_share"].hi == 1.0


def test_check_decision_gives_one_outcome_per_reason():
    from tests.test_evalkit_bridge import case, decision  # the synthetic alert and reply helpers

    c = case(random.Random(11), 1, amt_vs_usual=2.0, hour=14, amount=20.0)
    d = decision(c, "pass", ("P3.1",))
    assert d.output is not None
    d = d.model_copy(
        update={
            "output": d.output.model_copy(
                update={
                    "reasons": (
                        "Amount is 2 times the card's usual",
                        "Amount is 9 times the card's usual",
                        "Looks routine",
                    )
                }
            )
        }
    )
    assert vc.check_decision(d, c) == ["verified", "contradicted", "unverifiable"]
    assert vc.check_decision(d.model_copy(update={"output": None}), c) == []
    other = c.model_copy(update={"alert_id": "a-abc00099de00099f"})
    with pytest.raises(ValueError):
        vc.check_decision(d, other)


# ---------------------------------------------------------------- what the real reasons taught us


def test_rare_category_is_read_against_p6_1_which_sets_rare_below_five_percent():
    low = {**REC, "category_freq": 0.03}
    mid = {**REC, "category_freq": 0.0739}  # the model often calls 5-10% "rare"
    text = "category_freq is {v}, so travel is a rare category for this card"
    assert check_reason(text.format(v="0.03"), low) == "verified"
    assert check_reason(text.format(v="0.0739"), mid) == "unverifiable"
    assert (
        check_reason("Personal_care is a rare category (category_freq 0.0739)", mid) != "verified"
    )
    assert check_reason("Travel is not a rare category (category_freq 0.03)", low) != "verified"


def test_a_sentence_about_what_a_clause_requires_is_not_a_claim_about_the_alert():
    new = {**REC, "merchant_first_seen": True}  # the alert has a new merchant
    old = {**REC, "merchant_first_seen": False}
    for text in (
        "P7.1 needs a category the card uses often and a merchant that is not new",
        "No clear clause applies: this merchant is new, so P7.2 (known merchant) fails.",
        "merchant_first_seen is true, which rules out P7.2, which requires a known merchant.",
    ):
        assert check_reason(text, new) != "contradicted", text
        assert check_reason(text, old) != "verified", text


def test_a_number_that_belongs_to_another_field_is_not_taken_for_the_amount():
    # "purchase is at 11 local hour": 11 is the hour, not a dollar amount of 133.01
    rec = {**REC, "amount": 133.01, "hour": 11, "ts_hour": 11}
    out = check_reason("Purchase is at 11 local hour and the merchant is known to the card", rec)
    assert out != "contradicted"


def test_numbers_in_words_and_unread_comparisons_stop_a_verdict():
    five = "Amount is 0.21 times the card's usual, above the five-times signal"
    assert check_reason(five, REC) != "verified"
    assert check_reason("Amount is 11.01, which is well above the card's norm", REC) != "verified"
    assert check_reason("Amount is 11.01, twice the limit", REC) != "verified"
