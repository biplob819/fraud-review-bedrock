"""A strict check of the numbers and comparisons in a decision's reasons (E2.5).

One rule must never be broken: a false claim never comes back `verified`. When in doubt the
outcome is `unverifiable`. Pure: no model, no network, no randomness.

How a reason is read
- It is split into clauses (sentence ends, commas, "and", "but", "which", "so", brackets).
- A clause is judged only through RECOGNISED PATTERNS, each of which binds one field name, or one
  of its alias phrases, to a number or a comparison:
  `amt_vs_usual` ("9 times the card's usual"), `amount` ("amount is 11.01", "$11.01", "amount
  under 50"), `risk_score`, `category_freq` ("category_freq 0.04", "4% of the card's purchases"
  beside the word "category"), `card_history_n` ("12 earlier purchases"), `hour` ("hour 22",
  "10 p.m." when the clause names the hour), `merchant_first_seen` ("first purchase at this
  merchant", "known merchant", "not new").
- Every number in the clause must be bound by exactly one pattern, and the clause must name one
  field only. A leftover number, a second field, a leftover negation (one directly before a
  comparison word is part of the comparison and inverts it), a leftover comparison word, a
  threshold or range word ("signal", "threshold", "between", "respectively", "window"), a
  clause id such as P7.1 beside another number, or a clock time with no hour named, makes the
  clause `unverifiable`.
- Integers (`card_history_n`, `hour`) are exact. Fractions (`category_freq`, `risk_score`)
  also accept a percent form, scaled by 100. Floats use 0.5% relative or 0.005 absolute. A
  number that is just a correct rounding of the true value but outside that tolerance is
  `unverifiable`, not `contradicted`.
- A reason is `contradicted` if any clause is; else `verified` only if every clause that makes a
  claim (a number, or a recognised yes/no claim about the merchant) is `verified` and there is
  at least one; else `unverifiable`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fraudreview.prompts import alert_payload
from fraudreview.schemas import AlertCase, TriageDecision
from fraudreview.stats import wilson

Outcome = Literal["verified", "contradicted", "unverifiable"]

Kind = Literal["int", "float", "fraction", "bool"]
# the one table of field kinds
KINDS: dict[str, Kind] = {
    "card_history_n": "int",
    "hour": "int",
    "amount": "float",
    "amt_vs_usual": "float",
    "category_freq": "fraction",
    "risk_score": "fraction",
    "merchant_first_seen": "bool",
}
REL_TOL = 0.005
ABS_TOL = 0.005

# ---------------------------------------------------------------- text normalisation

_NUM = r"(?<![\w.])\d+(?:\.\d+)?(?!\d|\.\d)"
_NUM_RE = re.compile(_NUM)
_ID_RE = re.compile(r"\bp\d+\.\d+\b")
_FILLER = (
    r"(?:(?:only|just|about|around|roughly|approximately|approx|nearly|almost|very|quite|fairly|"
    r"low|high|small|large|modest|moderate)\s+){0,3}(?:at\s+)?"
)
_CONNECT = r"(?:(?:is|was|of|at|=|:|equals|comes to|came to|totals?)\s*)?"

_LT = r"under|below|less than|lower than|fewer than|smaller than"
_GT = r"over|above|more than|greater than|higher than|exceeds|exceeding"
_GE = r"at least"
_LE = r"at most"
_CMP_WORDS = f"{_GE}|{_LE}|{_LT}|{_GT}"
_CMP = rf"(?:(?P<neg>not)\s+)?(?P<cmp>{_GE}|{_LE}|{_LT}|{_GT})"
_OPS = {w: ">=" for w in ("at least",)} | {"at most": "<="}
_OPS |= {w: "<" for w in _LT.split("|")} | {w: ">" for w in _GT.split("|")}
_INVERT = {"<": ">=", ">": "<=", ">=": "<", "<=": ">"}

_NEGATION = re.compile(
    r"\b(?:not|no|never|without|nor|neither|none|nothing|cannot|hardly|barely)\b"
    r"|n't\b|\bnon-"
)
_LEFT_CMP = re.compile(rf"\b(?:{_CMP_WORDS}|exceed|exceeded|beyond|within|before|after)\b")
# words that leave a threshold, a baseline or a range in the clause once the claims are removed
_BLOCK = re.compile(
    r"\b(?:thresholds?|signals?|limits?|rules?|clauses?|window|range|ranges|period|between|from|"
    r"until|through|respectively|cut-?offs?|ceilings?|criteri\w+|policy|requirements?|"
    r"usual|typical|median|normal|average|mean|baseline|norm|triggers?|bars?|each|both)\b"
    r"|\bor (?:later|earlier|more|less|higher|lower|fewer|greater|above|below|over|under)\b|\+"
)
# the same idea for the whole clause: a range or a list of numbers
_RANGE = re.compile(
    r"\b(?:between|from|respectively|until|through)\b|\d\s*(?:am|pm)?\s*(?:to|-|–)\s*\d"
)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_CLAUSE = re.compile(
    r",\s+|\s+—\s+|\s+–\s+|\s*[()]\s*|"
    r"\s+(?:and|but|while|whereas|so|which|yet|because|although|though|since)\s+"
)
_WORDNUM = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|fifty|"
    r"hundred|thousand|half|double|twice|thrice|triple|dozen)\b|\b\w+-?fold\b|\b\w+-times\b"
)
# comparison and rarity words that no recognised claim consumed: the sentence says something about
# the alert that this check does not read, so nothing in it is `verified`
_QUAL = re.compile(
    r"\b(?:under|below|over|above|more than|less than|at least|at most|exceed\w*|greater|"
    r"higher|lower|fewer|smaller|larger|bigger)\b"
)
_RARE = re.compile(r"\b(?:rare\w*|uncommon|unusual\w*|infrequent\w*|seldom|occasional\w*)\b")
# a sentence that states what a clause needs or when it fires says nothing about this alert
_HYPO = re.compile(
    r"\b(?:requires?|required|requiring|needs?|needed|must|only if|if|unless|when|would|could|"
    r"should|otherwise|rules? out|fits?|criteria|conditions?|satisf\w+|qualif\w+|eligible|"
    r"allows?|permits?|permitted)\b"
)


def _normalise(reason: str) -> str:
    t = reason.lower().replace("’", "'").replace("×", "x").replace("−", "-")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 1,899.38
    t = re.sub(r"\b([ap])\.m\.?", r"\1m", t)
    t = re.sub(r"(?<=[a-z])n't\b", " not", t)
    t = re.sub(r"\((\s*\d{1,2}(?::00)?\s*[ap]m\s*)\)", r" \1 ", t)  # "hour 22 (10 pm)"
    return t


# ---------------------------------------------------------------- claims


@dataclass(frozen=True)
class Claim:
    field: str
    op: str  # "==", "<", ">", "<=", ">=", or "bool"
    value: float | bool
    shown: str  # the number as written ("3.8"); "" for bool
    pct: bool
    tokens: tuple[int, ...]  # start positions of the numeric tokens it binds
    span: tuple[int, int]


_PCT = r"(?P<pct>\s*(?:%|percent))?"
_NO_UNIT = (
    r"(?!\s*(?:%|percent|x\b|times|:\d|[ap]m\b|days?\b|weeks?\b|months?\b|years?\b|hours?\b|"
    r"minutes?\b|earlier\b|prior\b|previous\b|purchases?\b|transactions?\b|of the card))"
)


def _num_re(allow_pct: bool) -> str:
    if allow_pct:
        return rf"\$?(?P<n>{_NUM}){_PCT}"
    return rf"\$?(?P<n>{_NUM}){_NO_UNIT}"


def _generic(alias: str, allow_pct: bool) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{alias})\s*{_CONNECT}{_FILLER}(?:{_CMP}\s+)?{_num_re(allow_pct)}")


_AMOUNT_ALIAS = r"purchase amount|amount|purchase"
# the amount word is the most ambiguous ("purchase is at 11 local hour"): its number must end the
# clause or be followed by a currency word, "is", "was", "with" or "that"
_AMOUNT_END = r"(?=\s*$|\s+(?:dollars?|usd|us dollars?|bucks|is|was|with|that)\b)"
_P_AMOUNT = re.compile(
    rf"(?<!usual )(?<!typical )(?<!median )(?<!normal )(?<!average )(?<!earlier )(?<!prior )"
    rf"\b(?:{_AMOUNT_ALIAS})\s*{_CONNECT}{_FILLER}(?:{_CMP}\s+)?\$?(?P<n>{_NUM}){_AMOUNT_END}"
)
_P_DOLLAR = re.compile(rf"(?:{_CMP}\s+)?\$(?P<n>{_NUM})(?!\s*(?:%|percent|x\b|times))")
_P_AMT_ALIAS = _generic(r"amt_vs_usual", False)
_P_AMT_ALIAS_X = re.compile(
    rf"\bamt_vs_usual\s*{_CONNECT}{_FILLER}(?:{_CMP}\s+)?(?P<n>{_NUM})\s*(?:x\b|times)"
)
_P_TIMES = re.compile(
    rf"(?:{_CMP}\s+)?(?P<n>{_NUM})\s*(?:x|times)\s+(?:(?:the|its|this|that|their)\s+)?"
    r"(?:(?:card's|cards|card)\s+)?(?:usual|normal|typical|median)\b"
)
_P_RISK = _generic(r"risk[ _]score|fraud score|screening score", True)
_P_FREQ = _generic(r"category_freq|category freq(?:uency)?|category share", True)
_P_FREQ_PCT = re.compile(
    rf"(?:{_CMP}\s+)?(?P<n>{_NUM}){_PCT}\s+of\s+(?:the\s+|its\s+|this\s+)?(?:card's\s+|cards\s+)?"
    r"(?:earlier\s+|prior\s+|previous\s+|past\s+)?(?:purchases|transactions|spend)\b"
)
_P_HIST_N = re.compile(
    rf"(?:{_CMP}\s+)?(?P<n>{_NUM})\s+(?:earlier|prior|previous|past)\s+(?:purchases?|transactions?)\b"
)
_P_HIST_ALIAS = re.compile(
    rf"\b(?:card_history_n|card history|purchase history|transaction history)\s*{_CONNECT}"
    rf"{_FILLER}(?:{_CMP}\s+)?(?P<n>{_NUM})(?!\s*(?:%|percent|x\b|times|days?|weeks?|months?|years?))"
)
_P_HOUR = re.compile(
    rf"\b(?:ts_)?hour\s*(?:of day\s*)?{_CONNECT}{_FILLER}(?:{_CMP}\s+)?(?P<n>{_NUM})"
    r"(?!\s*(?:%|percent|x\b|times|:\d|[ap]m\b|hours?\b|:))"
)
_P_CLOCK = re.compile(r"(?<![\w.:])(?P<h>\d{1,2})(?::(?P<m>00))?\s*(?P<ap>[ap])m\b")
_P_CLOCK24 = re.compile(r"(?<![\w.:])(?P<h>\d{1,2}):(?P<m>00)\b(?!\s*[ap]m)")
_HOUR_WORD = re.compile(r"\b(?:ts_)?hour\b")

_MERCH_TRUE = re.compile(
    r"\bfirst (?:purchase|time|use|transaction|visit)\b[^.;,]{0,30}\bmerchant\b"
    r"|\bnew merchant\b|\bmerchant is new\b"
    r"|\bnever (?:been )?(?:used|seen|shopped)\b[^.;,]{0,25}\bmerchant\b"
    r"|\bmerchant_first_seen\s*(?:is|=|:)?\s*true\b"
)
_MERCH_FALSE = re.compile(
    r"\bknown merchant\b|\bmerchant is known\b|\bknown to the card\b|\bnot new\b"
    r"|\bfamiliar merchant\b|\bmerchant is familiar\b"
    r"|\bmerchant_first_seen\s*(?:is|=|:)?\s*false\b"
)


def _cmp_of(m: re.Match[str]) -> str:
    groups = m.groupdict()
    word = groups.get("cmp")
    if not word:
        return "=="
    op = _OPS[word]
    return _INVERT[op] if groups.get("neg") else op


def _claims_from(
    pattern: re.Pattern[str], text: str, field: str, *, allow_pct: bool
) -> list[Claim]:
    out = []
    for m in pattern.finditer(text):
        shown = m.group("n")
        pct = bool(m.groupdict().get("pct"))
        if pct and not allow_pct:
            continue
        out.append(Claim(field, _cmp_of(m), float(shown), shown, pct, (m.start("n"),), m.span()))
    return out


def find_claims(text: str) -> list[Claim]:
    claims: list[Claim] = []
    claims += _claims_from(_P_AMOUNT, text, "amount", allow_pct=False)
    claims += [c for c in _claims_from(_P_DOLLAR, text, "amount", allow_pct=False)]
    claims += _claims_from(_P_AMT_ALIAS, text, "amt_vs_usual", allow_pct=False)
    claims += _claims_from(_P_AMT_ALIAS_X, text, "amt_vs_usual", allow_pct=False)
    claims += _claims_from(_P_TIMES, text, "amt_vs_usual", allow_pct=False)
    claims += _claims_from(_P_RISK, text, "risk_score", allow_pct=True)
    claims += _claims_from(_P_FREQ, text, "category_freq", allow_pct=True)
    if "categor" in text:
        claims += _claims_from(_P_FREQ_PCT, text, "category_freq", allow_pct=True)
    claims += _claims_from(_P_HIST_N, text, "card_history_n", allow_pct=False)
    claims += _claims_from(_P_HIST_ALIAS, text, "card_history_n", allow_pct=False)
    claims += _claims_from(_P_HOUR, text, "hour", allow_pct=False)
    if _HOUR_WORD.search(text):
        for m in _P_CLOCK.finditer(text):
            h = int(m.group("h"))
            if not 1 <= h <= 12:
                continue
            hour = h % 12 + (12 if m.group("ap") == "p" else 0)
            toks = (m.start("h"),) + ((m.start("m"),) if m.group("m") else ())
            claims.append(Claim("hour", "==", float(hour), m.group(0), False, toks, m.span()))
        for m in _P_CLOCK24.finditer(text):
            h = int(m.group("h"))
            toks = (m.start("h"), m.start("m"))
            claims.append(Claim("hour", "==", float(h), m.group(0), False, toks, m.span()))
    return claims


# ---------------------------------------------------------------- evaluating one claim


def _decimals(shown: str) -> int:
    return len(shown.split(".")[1]) if "." in shown else 0


def _claimed(c: Claim) -> float:
    return c.value / 100 if c.pct else float(c.value)


def _truth(field: str, record: Mapping[str, Any]) -> float | bool | None:
    value = record.get(field)
    if value is None:
        return None
    if field == "merchant_first_seen":
        return value if isinstance(value, bool) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value):
        return None
    if field == "hour":
        other = record.get("ts_hour")
        if other is not None and other != value:
            return None
    return float(value)


def _holds(op: str, v: float, t: float) -> bool:
    return {"<": v < t, ">": v > t, "<=": v <= t, ">=": v >= t}[op]


def _eval_claim(c: Claim, record: Mapping[str, Any]) -> Outcome:
    kind = KINDS[c.field]
    truth = _truth(c.field, record)
    if truth is None or kind == "bool":
        return "unverifiable"
    t = float(truth)
    if c.pct and kind not in ("fraction",):
        return "unverifiable"
    v = _claimed(c)
    if kind == "fraction" and not c.pct and v > 1:
        return "unverifiable"  # "8 of the card's purchases" is a count, not a share
    if c.op != "==":
        return "verified" if _holds(c.op, t, v) else "contradicted"
    if kind == "int":
        if c.pct:
            return "unverifiable"
        return "verified" if v == t else "contradicted"
    diff = abs(v - t)
    if diff <= max(ABS_TOL, REL_TOL * abs(t)):
        return "verified"
    half = 0.5 * 10 ** (-_decimals(c.shown)) / (100 if c.pct else 1)
    if diff <= half * (1 + 1e-9):
        return "unverifiable"  # a correct rounding, but outside the tolerance
    return "contradicted"


# ---------------------------------------------------------------- one clause


def _judge(
    text: str, record: Mapping[str, Any], hypo: bool = False, in_id_sentence: bool = False
) -> Outcome | None:
    """None when the clause makes no claim at all."""
    has_id = bool(_ID_RE.search(text))
    body = _ID_RE.sub(" ", text)
    tokens = [m.start() for m in _NUM_RE.finditer(body)]
    t_true = [m for m in _MERCH_TRUE.finditer(body)]
    t_false = [m for m in _MERCH_FALSE.finditer(body)]
    bools = t_true + t_false
    if not tokens and not bools:
        # a number in words ("the five-times signal") cannot be bound: it is a claim we cannot read
        return "unverifiable" if _WORDNUM.search(body) and not has_id else None
    if has_id or hypo:
        return "unverifiable"
    if _RANGE.search(body):
        return "unverifiable"
    if t_true and t_false:
        return "unverifiable"
    claims = find_claims(body)
    if in_id_sentence and (
        any(c.op != "==" for c in claims)
        or any(not m.group().startswith("merchant_") for m in bools)
    ):
        return "unverifiable"  # a sentence about a clause: a comparison or phrase may quote it
    # one token, one meaning
    seen: dict[int, tuple[str, str, float]] = {}
    for c in claims:
        for tok in c.tokens:
            key = (c.field, c.op, _claimed(c))
            if tok in seen and seen[tok] != key:
                return "unverifiable"
            seen[tok] = key
    if any(tok not in seen for tok in tokens):
        return "unverifiable"
    fields = {c.field for c in claims} | ({"merchant_first_seen"} if bools else set())
    if len(fields) != 1:
        return "unverifiable"
    rest = list(body)
    for c in claims:
        rest[c.span[0] : c.span[1]] = " " * (c.span[1] - c.span[0])
    for m in bools:
        rest[m.start() : m.end()] = " " * (m.end() - m.start())
    left = "".join(rest)
    if _NEGATION.search(left) or _LEFT_CMP.search(left) or _BLOCK.search(left):
        return "unverifiable"
    if bools:
        truth = _truth("merchant_first_seen", record)
        if truth is None:
            return "unverifiable"
        return "verified" if truth is bool(t_true) else "contradicted"
    results = {_eval_claim(c, record) for c in claims}
    if "contradicted" in results:
        return "contradicted"
    return "verified" if results == {"verified"} else "unverifiable"


# ---------------------------------------------------------------- the public API


def _leftover(text: str) -> str:
    """The clause without its recognised claims and clause ids."""
    body = _ID_RE.sub(" ", text)
    rest = list(body)
    spans = [c.span for c in find_claims(body)]
    spans += [m.span() for m in (*_MERCH_TRUE.finditer(body), *_MERCH_FALSE.finditer(body))]
    for a, b in spans:
        rest[a:b] = " " * (b - a)
    return "".join(rest)


def check_reason(reason: str, record: Mapping[str, Any]) -> Outcome:
    """One reason against the flat alert record: verified only when every claim holds."""
    verdicts: list[Outcome] = []
    freq = _truth("category_freq", record)
    for sentence in _SENTENCE.split(_normalise(reason)):
        hypo = bool(_HYPO.search(sentence))
        in_id = bool(_ID_RE.search(sentence))
        parts = [p.strip(" \t\n.,;:!?") for p in _CLAUSE.split(sentence)]
        parts = [p for p in parts if p]
        found: list[Outcome] = []
        unread = False
        for part in parts:
            verdict = _judge(part, record, hypo, in_id)
            if verdict is not None:
                found.append(verdict)
            left = _leftover(part)
            unread = unread or bool(_QUAL.search(left))
            if _RARE.search(left):  # P6.1: rare means a share below 0.05
                unread = unread or not (
                    isinstance(freq, float) and freq < 0.05 and not _NEGATION.search(sentence)
                )
        if unread:
            found = ["unverifiable" if v == "verified" else v for v in found]
        verdicts += found
    if "contradicted" in verdicts:
        return "contradicted"
    if verdicts and all(v == "verified" for v in verdicts):
        return "verified"
    return "unverifiable"


def flat_record(case: AlertCase) -> dict[str, Any]:
    """The agent-visible alert, flattened to one level (same keys as `evalkit_bridge.record`)."""
    payload = alert_payload(case)
    features = payload.pop("features")
    return {**payload, **features}


def check_decision(decision: TriageDecision, case: AlertCase) -> list[Outcome]:
    """One outcome per reason; an error reply (no output) has none."""
    if decision.alert_id != case.alert_id:
        raise ValueError(f"decision {decision.decision_id} is for another alert")
    if decision.output is None:
        return []
    rec = flat_record(case)
    return [check_reason(r, rec) for r in decision.output.reasons]


def shares(outcomes: Iterable[Outcome] | Sequence[Outcome]) -> dict[str, Any]:
    """Counts of each outcome, and Wilson 95% intervals of verified and contradicted over all."""
    items = list(outcomes)
    counts = {o: items.count(o) for o in ("verified", "contradicted", "unverifiable")}
    n = len(items)
    return {
        "n": n,
        **counts,
        "verified_share": wilson(counts["verified"], n),
        "contradicted_share": wilson(counts["contradicted"], n),
    }


__all__ = [
    "KINDS",
    "Outcome",
    "check_decision",
    "check_reason",
    "flat_record",
    "shares",
]
