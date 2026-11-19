"""`fraud report`: one self-contained HTML dashboard from a `summary.json` (E2.3, DSH-02..05).

One file, inline SVG and plain CSS with light and dark themes. No script, no font, no image and
no link to anything outside the file, so it can sit on any static host and open offline.

Every number on the page goes through one formatter (`Formatter`, DSH-03, NFR5):

- a method returns a `Num`, and the page builders accept a number only as a `Num`; any other
  text with a digit in it is refused (`text`), so a number cannot reach the page another way;
- a rate is formatted only with its n (`pct` refuses without it, and a `Rate` carries its own);
- each panel states "on simulated data" at its head, and the banner says the data is simulated;
- the markup of a formatted number carries `data-fmt`, so a test can prove nothing else on the
  page holds a digit.

The faithfulness figure is drawn together with its partial flag and its notes (D-43), and is
refused without them. Mismatch examples appear as a short hash of the alert id, never the id.
"""

from __future__ import annotations

import hashlib
import html
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fraudreview import explain, monitor
from fraudreview.defaults import packaged
from fraudreview.schemas import Interval, Rate
from fraudreview.summary_model import EvaluatedSummary as RunSummary

STAGE = {"stage1": "first stage", "stage2": "second stage"}
BANNER = "Simulated data. The agent recommends; people decide."
LABEL = "on simulated data"


class ReportError(Exception):
    """The summary cannot be drawn honestly (for example a faithfulness figure with no notes)."""


class FormatError(ReportError, ValueError):
    """A number was offered to the page without going through the formatter's rules."""


# ------------------------------------------------------------------ the one formatter


class Num(str):
    """Text made by `Formatter`: the only way a digit may reach the page."""

    kind: str

    def __new__(cls, text: str, kind: str) -> Num:
        obj = super().__new__(cls, text)
        obj.kind = kind
        return obj


class Formatter:
    """DSH-03. Every method returns a `Num`. A rate needs its n: `pct` takes it as a required
    keyword and refuses a missing, negative or non-integer one."""

    @staticmethod
    def _n(n: object) -> int:
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise FormatError(f"a rate needs its n (a non-negative integer), got {n!r}")
        return n

    def pct(self, value: float, *, n: int | None) -> Num:
        count = self._n(n)
        if not isinstance(value, int | float) or not math.isfinite(value):
            raise FormatError(f"not a finite rate: {value!r}")
        if not 0 <= value <= 1:
            raise FormatError(f"a rate lies between 0 and 1, got {value!r}")
        if count == 0:
            return Num("no data (n zero)", "pct")
        return Num(f"{value * 100:.1f}%", "pct")

    def rate(self, r: Rate) -> Num:
        return self.pct(r.point, n=r.n)

    def rate_ci(self, r: Rate) -> Num:
        if r.n == 0:
            return self.pct(r.point, n=0)
        lo, hi = self.pct(r.lo, n=r.n), self.pct(r.hi, n=r.n)
        return Num(f"{self.rate(r)} ({lo} to {hi})", "pct")

    def interval_ci(self, iv: Interval, *, n: int | None) -> Num:
        lo, hi = self.pct(iv.lo, n=n), self.pct(iv.hi, n=n)
        return Num(f"{self.pct(iv.point, n=n)} ({lo} to {hi})", "pct")

    def points(self, value: float, *, n: int | None) -> Num:
        """A difference of two rates, in percentage points."""
        count = self._n(n)
        if not isinstance(value, int | float) or not math.isfinite(value) or abs(value) > 1:
            raise FormatError(f"not a difference of rates: {value!r}")
        if count == 0:
            return Num("no data (n zero)", "pct")
        return Num(
            f"{value * 100:+.1f} points".replace("+0.0", "0.0").replace("-0.0", "0.0"), "pct"
        )

    def count(self, value: int) -> Num:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise FormatError(f"not a count: {value!r}")
        return Num(f"{value:,}", "count")

    def counted(self, value: int, noun: str) -> Num:
        self.count(value)
        return Num(f"{value:,} {noun}", "count")

    def kn(self, k: int, n: int) -> Num:
        self.count(k)
        self._n(n)
        if k > n:
            raise FormatError("k above n")
        return Num(f"{k:,} of {n:,}", "count")

    def usd(self, value: float) -> Num:
        if not math.isfinite(value) or value < 0:
            raise FormatError(f"not an amount: {value!r}")
        return Num(f"${value:,.2f}", "usd")

    def usd_ci(self, iv: Interval) -> Num:
        return Num(f"{self.usd(iv.point)} ({self.usd(iv.lo)} to {self.usd(iv.hi)})", "usd")

    def minutes(self, iv: Interval) -> Num:
        return Num(f"{iv.point:,.0f} min ({iv.lo:,.0f} to {iv.hi:,.0f})", "minutes")

    def ms(self, value: int) -> Num:
        self.count(value)
        return Num(f"{value:,} ms", "ms")

    def number(self, value: float) -> Num:
        """A plain target value (not a rate)."""
        if not math.isfinite(value):
            raise FormatError(f"not a finite number: {value!r}")
        return Num(f"{value:g}", "number")

    def tick(self, value: float) -> Num:
        """An axis tick: a scale mark, not a measurement."""
        return Num(f"{value * 100:g}%", "tick")

    def level(self) -> Num:
        return Num("95%", "level")

    def ident(self, value: str) -> Num:
        """An opaque identifier or name that may hold digits: a run id, digest, model, hash."""
        if not value.strip():
            raise FormatError("an identifier cannot be empty")
        return Num(value, "id")

    def prose(self, value: str) -> Num:
        """A stored note, carried through as written (it may quote figures)."""
        if not value.strip():
            raise FormatError("a note cannot be empty")
        return Num(value, "note")

    def ref(self, alert_id: str) -> Num:
        """A short hash that stands for an alert id; the id itself is never shown."""
        return Num(hashlib.sha256(alert_id.encode("utf-8")).hexdigest()[:8], "ref")


FMT = Formatter()


def text(value: str) -> str:
    """Escaped plain text. Refused when it holds a digit: numbers come only as `Num`."""
    if any(ch.isdigit() for ch in value):
        raise FormatError(f"a digit outside the formatter: {value!r}")
    return html.escape(value, quote=False)


def h(part: str | Num) -> str:
    """One page fragment: a `Num` becomes a marked span, other text is checked and escaped."""
    if isinstance(part, Num):
        return f'<span class="n" data-fmt="{part.kind}">{html.escape(part, quote=False)}</span>'
    return text(part)


def j(*parts: str | Num) -> str:
    """Text and numbers joined, in order, with the page's escaping rules."""
    return "".join(h(p) for p in parts)


def svg_num(x: float, y: float, num: Num, *, cls: str = "v", anchor: str = "start") -> str:
    return (
        f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
        f'data-fmt="{num.kind}">{html.escape(num, quote=False)}</text>'
    )


def svg_text(x: float, y: float, value: str, *, cls: str = "l", anchor: str = "start") -> str:
    return (
        f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{text(value)}</text>'
    )


# ------------------------------------------------------------------ charts


@dataclass(frozen=True)
class Bar:
    label: str
    sub: Num | None
    point: float
    lo: float
    hi: float
    shown: Num
    cls: str = "s1"


def bar_of(label: str, r: Rate, cls: str = "s1") -> Bar:
    return Bar(label, FMT.kn(r.k, r.n), r.point, r.lo, r.hi, FMT.rate_ci(r), cls)


def nice_max(value: float) -> float:
    for step in (0.05, 0.1, 0.2, 0.25, 0.5, 1.0):
        if value <= step:
            return step
    return 1.0


def rate_bars(bars: Sequence[Bar], axis_max: float, desc: str) -> str:
    """Horizontal bars on a 0..axis_max share axis, each with its 95% interval as a whisker."""
    left, right, top, row = 200.0, 490.0, 8.0, 44.0
    height = top + row * len(bars) + 30
    scale = (right - left) / axis_max

    def px(v: float) -> float:
        return left + min(v, axis_max) * scale

    out = [
        f'<svg class="chart" viewBox="0 0 760 {height:.0f}" role="img" aria-label="{text(desc)}">'
    ]
    for i in range(5):
        v = axis_max * i / 4
        x = px(v)
        out.append(
            f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - 24:.0f}"/>'
        )
        out.append(svg_num(x, height - 8, FMT.tick(v), cls="t", anchor="middle"))
    for k, b in enumerate(bars):
        y = top + k * row
        mid = y + row / 2 - 4
        out.append(svg_text(left - 10, mid, b.label, cls="l", anchor="end"))
        if b.sub is not None:
            out.append(svg_num(left - 10, mid + 15, b.sub, cls="sub", anchor="end"))
        out.append(
            f'<rect class="{b.cls}" x="{left}" y="{mid - 10:.1f}" width="{max(px(b.point) - left, 2):.1f}" height="20" rx="2"/>'
        )
        out.append(
            f'<line class="whisker" x1="{px(b.lo):.1f}" x2="{px(b.hi):.1f}" y1="{mid:.1f}" y2="{mid:.1f}"/>'
        )
        for v in (b.lo, b.hi):
            out.append(
                f'<line class="whisker" x1="{px(v):.1f}" x2="{px(v):.1f}" y1="{mid - 7:.1f}" y2="{mid + 7:.1f}"/>'
            )
        out.append(svg_num(right + 12, mid + 4, b.shown, cls="v"))
    out.append("</svg>")
    return "".join(out)


@dataclass(frozen=True)
class Seg:
    label: str
    count: int
    share: Rate
    cls: str


def stacked(segs: Sequence[Seg], total: int, desc: str) -> str:
    """One stacked bar with a legend that gives each part's count and share."""
    width, h_ = 640.0, 26.0
    out = [
        f'<svg class="chart wide" viewBox="0 0 {width:.0f} {h_ + 4:.0f}" role="img" '
        f'aria-label="{text(desc)}" preserveAspectRatio="none">'
    ]
    x = 0.0
    for s in segs:
        if s.count and total:
            w = width * s.count / total
            out.append(
                f'<rect class="{s.cls}" x="{x:.1f}" y="2" width="{w:.1f}" height="{h_:.0f}"/>'
            )
            out.append(
                f'<line class="gap" x1="{x + w:.1f}" x2="{x + w:.1f}" y1="2" y2="{h_ + 2:.0f}"/>'
            )
            x += w
    out.append("</svg>")
    legend = "".join(
        f'<li><span class="sw {s.cls}"></span> {text(s.label)}: '
        f"{h(FMT.kn(s.count, total))}, {h(FMT.rate(s.share))}</li>"
        for s in segs
    )
    return "".join(out) + f'<ul class="legend">{legend}</ul>'


def trend_chart(history: Sequence[Mapping[str, Any]], current: str) -> str:
    """Leakage upper bound and auto-clear rate over the logged runs, in run order."""
    series = {s["metric"]: s["values"] for s in monitor.trend(history)}
    leak = series["leakage_hi"]
    clear = series["auto_clear_rate"]
    pts: list[tuple[str, list[float | None], str, str]] = [
        ("leakage upper bound", leak, "s2", "m-circle"),
        ("auto-clear rate", clear, "s1", "m-square"),
    ]
    top = max([v for _, vs, _, _ in pts for v in vs if v is not None] or [0.1])
    axis_max = nice_max(top * 1.08)
    left, right, y0, y1 = 56.0, 690.0, 20.0, 190.0
    n = len(history)

    def px(i: int) -> float:
        return (left + right) / 2 if n == 1 else left + 30 + (right - left - 60) * i / (n - 1)

    def py(v: float) -> float:
        return y1 - (y1 - y0) * v / axis_max

    out = [
        '<svg class="chart" viewBox="0 0 720 236" role="img" '
        'aria-label="Leakage upper bound and auto-clear rate across the logged runs">'
    ]
    for i in range(5):
        v = axis_max * i / 4
        out.append(
            f'<line class="grid" x1="{left}" x2="{right}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
        )
        out.append(svg_num(left - 8, py(v) + 4, FMT.tick(v), cls="t", anchor="end"))
    for i, run in enumerate(history):
        mark = "cur" if run["run_id"] == current else "t"
        out.append(svg_num(px(i), 214, FMT.count(i + 1), cls=mark, anchor="middle"))
    for name, values, cls, marker in pts:
        coords = [(i, px(i), py(v), v) for i, v in enumerate(values) if v is not None]
        if len(coords) > 1:
            path = " ".join(f"{x:.1f},{y:.1f}" for _, x, y, _ in coords)
            out.append(f'<polyline class="line {cls}" points="{path}"/>')
        for i, x, y, v in coords:
            if marker == "m-circle":
                out.append(f'<circle class="pt {cls}" cx="{x:.1f}" cy="{y:.1f}" r="5"/>')
            else:
                out.append(
                    f'<rect class="pt {cls}" x="{x - 5:.1f}" y="{y - 5:.1f}" width="10" height="10"/>'
                )
            above = marker == "m-square"
            run_n = _run_n(history[i], name)
            out.append(
                svg_num(
                    x, y - 11 if above else y + 20, FMT.pct(v, n=run_n), cls="v", anchor="middle"
                )
            )
    out.append("</svg>")
    return "".join(out)


def _run_n(line: Mapping[str, Any], series: str) -> int:
    return int(line["leakage"]["n"] if series == "leakage upper bound" else line["n"])


# ------------------------------------------------------------------ panels


def panel(pid: str, title: str, body: str) -> str:
    return (
        f'<section id="{pid}"><div class="head"><h2>{text(title)}</h2>'
        f'<span class="sim">{text(LABEL)}</span></div>{body}</section>'
    )


def stat(label: str, value: Num, detail: str | Num | Sequence[str | Num] = "") -> str:
    parts = [detail] if isinstance(detail, str | Num) else list(detail)
    body = j(*parts) if parts and parts != [""] else ""
    return (
        f'<div class="stat"><div class="k">{text(label)}</div><div class="big">{h(value)}</div>'
        f'<div class="d">{body}</div></div>'
    )


def _chip(passed: bool | None) -> str:
    if passed is None:
        return '<span class="chip warn">Not evaluated</span>'
    return (
        '<span class="chip good">Met</span>' if passed else '<span class="chip bad">Not met</span>'
    )


def _threshold_words(metric: str, op: str, value: float) -> str:
    if metric == "leakage" and op == "==" and value == 0:
        return j("Leakage target: no fraud is auto-cleared")
    return j(FMT.ident(metric), " ", FMT.ident(op), " ", FMT.number(value))


def safety(s: RunSummary) -> str:
    leak, rec, base = s.leakage, s.recall_end_to_end, s.baseline
    exact = leak.k == 0
    bound = "exact one-sided upper bound" if exact else "Wilson upper limit"
    tiles = (
        stat(
            "Leakage",
            FMT.kn(leak.k, leak.n),
            ["frauds auto-cleared; ", bound, " ", FMT.pct(leak.hi, n=leak.n)],
        )
        + stat(
            "End-to-end recall, among alerts the baseline raised",
            FMT.rate(rec),
            ["frauds that reach a person, ", FMT.kn(rec.k, rec.n)],
        )
        + stat(
            "Baseline recall",
            FMT.rate(base.recall_test),
            ["alert model on the test split, ", FMT.kn(base.recall_test.k, base.recall_test.n)],
        )
    )
    targets = "".join(
        f"<li>{_chip(t.passed)} {_threshold_words(t.metric, t.op, t.value)}</li>"
        for t in s.thresholds
    )
    diff = FMT.points(-leak.point, n=leak.n)
    chart = rate_bars(
        [bar_of("Leakage", leak, "s2")],
        nice_max(leak.hi * 1.05),
        "Leakage with its upper bound",
    ) + rate_bars(
        [
            bar_of("System, end to end", rec, "s1"),
            bar_of("Baseline, test split", base.recall_test, "s3"),
        ],
        1.0,
        "End-to-end recall beside the baseline's recall",
    )
    note = j(
        "Every scored alert was one the baseline raised, so its recall on them is full and the "
        "system can only lower it, by exactly its leakage (paired difference ",
        diff,
        "). Whiskers and bracketed ranges are ",
        FMT.level(),
        " intervals.",
    )
    return panel(
        "safety",
        "Safety",
        f'<div class="stats">{tiles}</div><ul class="targets">{targets}</ul>{chart}'
        f'<p class="note">{note}</p>',
    )


def value(s: RunSummary) -> str:
    m = s.minutes_per_alert
    tiles = (
        stat(
            "Auto-clear rate",
            FMT.rate(s.auto_clear_rate),
            ["alerts cleared with no person, ", FMT.kn(s.auto_clear_rate.k, s.auto_clear_rate.n)],
        )
        + stat(
            "Auto-clear precision",
            FMT.rate(s.auto_clear_precision),
            [
                "auto-clears that were legitimate, ",
                FMT.kn(s.auto_clear_precision.k, s.auto_clear_precision.n),
            ],
        )
        + stat(
            "Modelled minutes saved per thousand alerts",
            Num(f"{s.minutes_saved_per_1000.point:,.0f} min", "minutes"),
            ["range ", FMT.minutes(s.minutes_saved_per_1000)]
            + ([", at ", FMT.number(m), " analyst minutes per alert (a model input)"] if m else []),
        )
        + stat(
            "Model cost per thousand alerts",
            FMT.usd(s.cost_per_1000_usd.point),
            ["range ", FMT.usd_ci(s.cost_per_1000_usd), "; run total ", FMT.usd(s.cost_total_usd)],
        )
    )
    bars = rate_bars(
        [
            bar_of("Auto-clear rate", s.auto_clear_rate, "s1"),
            bar_of("Auto-clear precision", s.auto_clear_precision, "s1"),
            bar_of("Cleared at stage one", s.stage1_clear_rate, "s3"),
            bar_of("Reached stage two", s.stage2_reach_rate, "s3"),
        ],
        1.0,
        "Auto-clear rate and precision, and the share of alerts at each stage",
    )
    hold, esc = s.queue_mix.get("hold", 0), s.queue_mix.get("escalate", 0)
    total = s.n_alerts
    mix = stacked(
        [
            Seg("Auto-cleared", s.auto_clear_rate.k, s.auto_clear_rate, "s1"),
            Seg("Standard queue (escalated)", esc, _share(esc, total), "s2"),
            Seg("Priority queue (held)", hold, _share(hold, total), "s3"),
        ],
        total,
        "Where the alerts went: auto-cleared, standard queue or priority queue",
    )
    return panel(
        "value",
        "Value",
        f'<div class="stats">{tiles}</div>{bars}<h3>Queue mix</h3>{mix}'
        '<p class="note">Minutes saved are modelled from the auto-clear count, not measured. '
        "Cost is the model spend recorded for the run.</p>",
    )


def _share(k: int, n: int) -> Rate:
    from fraudreview.stats import wilson

    return wilson(k, n)


def _faithfulness(s: RunSummary) -> str:
    e = s.explanation
    if e.judge_mode == "none":
        return '<p class="note">Faithfulness was not judged for this run.</p>'
    d = e.faithfulness_detail
    if d is None:
        raise ReportError("a faithfulness figure is never drawn without its notes (D-43)")
    cards = []
    for name, iv in e.faithfulness.items():
        j_ = d.judges[name]
        sup = j_.claims_supported
        if sup is None:
            continue
        full = j_.explanations_fully_supported
        lines = [
            j(
                "Claims supported, ",
                FMT.kn(sup.k, sup.n),
                " judged (",
                FMT.pct(j_.claim_coverage, n=d.claims_extracted),
                " of ",
                FMT.count(d.claims_extracted),
                " extracted claims reached)",
            )
        ]
        if full is not None:
            lines.append(
                j(
                    "Explanations fully supported, ",
                    FMT.kn(full.k, full.n),
                    " fully judged",
                )
            )
        cards.append(
            f'<div class="fig"><div class="k">Judge {h(FMT.ident(name))}</div>'
            f'<div class="big">{h(FMT.interval_ci(iv, n=sup.n))}</div>'
            + "".join(f'<div class="d">{x}</div>' for x in lines)
            + "</div>"
        )
    kind = "Partial" if d.partial else "Complete"
    mode = {"single": "single judge", "dual": "two judges"}[e.judge_mode]
    notes = "".join(f"<li>{h(FMT.prose(n))}</li>" for n in d.notes)
    return (
        f'<div class="faith"><div class="figs">{"".join(cards)}'
        f'<span class="chip warn">{text(kind)}, {text(mode)}</span></div>'
        f'<div class="why"><h4>Read the number with these notes</h4><ul class="notes">{notes}</ul>'
        "</div></div>"
    )


def _mismatches(s: RunSummary) -> str:
    detail = s.explanation.mismatch_detail
    if not detail:
        return '<p class="note">No mismatch examples for this run.</p>'
    cards = []
    for m in detail:
        mapped = [explain.FIELD_TO_DRIVER.get(f) for f in m.cited]
        comparable = [x for x in mapped if x is not None]
        hits = sum(x in m.positive_drivers for x in comparable)
        cited = ", ".join(text(f) for f in m.cited) or "none"
        drivers = ", ".join(text(f) for f in m.positive_drivers) or "none"
        cards.append(
            '<div class="card">'
            f'<div class="k">Alert {h(FMT.ref(m.alert_id))}</div>'
            f'<div class="d">Cited fields that are positive drivers: {h(FMT.kn(hits, len(comparable)))}</div>'
            f'<div class="d">Cited: {cited}</div>'
            f'<div class="d">Positive drivers: {drivers}</div>'
            f'<div class="d">Protected mentions: {h(FMT.count(m.protected_mentions))}</div>'
            "</div>"
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def explanations(s: RunSummary) -> str:
    e = s.explanation
    n_agree = e.driver_agreement_n
    bars = rate_bars(
        [
            bar_of("Valid schema", e.schema_valid),
            bar_of("Real policy clauses", e.clauses_valid),
            bar_of("Allowed fields", e.fields_valid),
            bar_of("Numbers true (checkable)", e.values_true),
            Bar(
                "Driver agreement",
                FMT.counted(n_agree, "decisions") if n_agree is not None else None,
                e.driver_agreement.point,
                e.driver_agreement.lo,
                e.driver_agreement.hi,
                FMT.interval_ci(e.driver_agreement, n=n_agree if n_agree is not None else 0),
                "s3",
            ),
        ],
        1.0,
        "Explanation validity rates and driver agreement",
    )
    body = [bars]
    body.append(
        '<div class="stats">'
        + stat(
            "Protected-attribute mentions",
            FMT.count(e.protected_mentions),
            "across every reason the agent wrote",
        )
        + (
            stat(
                "Clears citing a no-clear clause that did not apply",
                FMT.count(e.suspicious_no_clear),
                "counted apart; odd to cite, not a failure",
            )
            if e.suspicious_no_clear is not None
            else ""
        )
        + "</div>"
    )
    vs = e.value_shares
    if vs is not None:
        body.append(
            "<h3>Value check</h3>"
            + stacked(
                [
                    Seg("Verified", vs.verified, vs.verified_share, "good"),
                    Seg("Contradicted", vs.contradicted, vs.contradicted_share, "bad"),
                    Seg("Unverifiable", vs.unverifiable, vs.unverifiable_share, "mute"),
                ],
                vs.n,
                "Share of checked reasons that were verified, contradicted or unverifiable",
            )
            + '<p class="note">A false claim is never marked verified, and anything the checker '
            "is unsure of is unverifiable.</p>"
        )
    body.append("<h3>Faithfulness</h3>" + _faithfulness(s))
    body.append("<h3>Mismatch examples</h3>" + _mismatches(s))
    return panel("explanations", "Explanations", "".join(body))


def monitoring(s: RunSummary, history: Sequence[Mapping[str, Any]], example: bool) -> str:
    if history:
        rows = "".join(
            "<tr>"
            f"<td>{h(FMT.ident(str(r['run_id'])))}</td>"
            f"<td>{h(FMT.count(int(r['n'])))}</td>"
            f"<td>{h(FMT.kn(r['leakage']['k'], r['leakage']['n']))}</td>"
            f"<td>{h(FMT.pct(r['leakage']['hi'], n=r['leakage']['n']))}</td>"
            f"<td>{h(FMT.pct(r['auto_clear_rate']['point'], n=r['auto_clear_rate']['n']))}</td>"
            "</tr>"
            for r in history
        )
        trend = (
            '<ul class="legend inline">'
            '<li><span class="sw s2"></span> Leakage upper bound</li>'
            '<li><span class="sw s1 sq"></span> Auto-clear rate</li></ul>'
            + trend_chart(history, s.run_id)
            + '<table><thead><tr><th scope="col">Run</th><th scope="col">Alerts</th>'
            '<th scope="col">Leakage</th><th scope="col">Upper bound</th>'
            f'<th scope="col">Auto-clear</th></tr></thead><tbody>{rows}</tbody></table>'
        )
        if example:
            trend += (
                '<p class="note">The example run is not in this log; the trend shows the '
                "evaluation runs logged so far.</p>"
            )
    else:
        trend = '<p class="note">No run history is logged yet, so there is no trend to draw.</p>'
    if s.alarms is None:
        alarms = '<p class="note">Alarm bands were not checked for this run.</p>'
    elif not s.alarms:
        alarms = '<p class="note">No alarm band tripped on this run.</p>'
    else:
        items = "".join(
            f'<li><span class="chip {"bad" if a.level == "fail" else "warn"}">'
            f"{'Failure' if a.level == 'fail' else 'Warning'}</span> "
            f"{h(FMT.ident(a.metric))}: {h(_alarm_value(s, a.metric, a.value))}</li>"
            for a in s.alarms
        )
        alarms = f'<ul class="targets">{items}</ul>'
    drift = ""
    if s.drift:
        rows = "".join(
            f"<tr><td>{h(FMT.ident(d.feature))}</td><td>{h(FMT.number(d.psi))}</td>"
            f"<td>{text(d.level)}</td></tr>"
            for d in s.drift
        )
        drift = (
            "<h3>Drift against the reference sample</h3><table><thead><tr>"
            '<th scope="col">Feature</th><th scope="col">Population stability index</th>'
            f'<th scope="col">Level</th></tr></thead><tbody>{rows}</tbody></table>'
        )
    return panel(
        "monitoring",
        "Monitoring",
        f'{trend}<h3>Alarms</h3>{alarms}{drift}<p class="note">Bands come from the run '
        "configuration; any leakage above zero is always a failure.</p>",
    )


def _alarm_value(s: RunSummary, metric: str, value: float) -> Num:
    rate_n = {
        "auto_clear_rate": s.n_alerts,
        "auto_clear_precision": s.auto_clear_rate.k,
        "schema_error_rate": s.n_alerts,
        "refusal_rate": s.n_alerts,
        "leakage": s.n_frauds,
    }
    if metric in rate_n:
        return FMT.pct(value, n=rate_n[metric])
    if metric == "cost_per_1000_usd":
        return FMT.usd(value)
    if metric.endswith("_ms"):
        return FMT.ms(int(value))
    return FMT.number(value)


def run_facts(s: RunSummary, example: bool) -> str:
    def row(k: str, v: str) -> str:
        return f"<dt>{text(k)}</dt><dd>{v}</dd>"

    models = ", ".join(
        f"{text(STAGE[stage])}: {h(FMT.ident(model))}" for stage, model in sorted(s.models.items())
    )
    latency = "; ".join(
        f"{text(STAGE[stage])} median {h(FMT.ms(v.p50))}, {h(FMT.ident('p95'))} {h(FMT.ms(v.p95))}"
        for stage, v in sorted(s.latency_ms.items())
    )
    errors = ", ".join(f"{text(k)} {h(FMT.count(v))}" for k, v in sorted(s.errors.items()))
    rows = [
        row("Run", h(FMT.ident(s.run_id))),
        row("Split", text(s.split)),
        row("Alerts (n)", h(FMT.count(s.n_alerts))),
        row("Frauds among them", h(FMT.count(s.n_frauds))),
        row("Policy version", h(FMT.ident(s.policy_version))),
        row("Models", models),
        row("Provider", text(s.provider)),
    ]
    if s.freeze_digest:
        rows.append(row("Settings digest", h(FMT.ident(s.freeze_digest))))
    if s.audit_head:
        rows.append(
            row(
                "Audit log head",
                f"{h(FMT.ident(s.audit_head.hash[:16]))}, entry {h(FMT.count(s.audit_head.seq))}",
            )
        )
    rows += [
        row("Cost", h(FMT.usd(s.cost_total_usd))),
        row("Latency", latency or "not measured (replayed from recorded replies)"),
        row("Errors", errors or "none"),
        row("Started", h(FMT.ident(s.started_at.strftime("%Y-%m-%d %H:%M UTC")))),
    ]
    if s.finished_at:
        rows.append(row("Finished", h(FMT.ident(s.finished_at.strftime("%Y-%m-%d %H:%M UTC")))))
    note = ""
    if example:
        note = (
            '<p class="note strong">'
            + j(
                "This is the committed golden example: ",
                FMT.count(s.n_alerts),
                " alerts, far too few to support any claim about performance. It shows the "
                "page, not the result.",
            )
            + "</p>"
        )
    return panel("facts", "Run facts", f"{note}<dl>{''.join(rows)}</dl>")


# ------------------------------------------------------------------ the page

CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1b1f24;--muted:#58626e;--panel:#f6f7f9;--line:#d3d8df;
--s1:#2563c9;--s2:#c2570c;--s3:#7c4dc4;--good:#1a7f4b;--bad:#b42318;--warn:#8a5a00;--mute:#9aa4b0;
--banner:#fff4d6;--bannerfg:#5c3d00}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1318;--fg:#e6e9ee;
--muted:#a0abb8;--panel:#171c23;--line:#2e3641;--s1:#6ea8fe;--s2:#f0a05a;--s3:#b79cf2;
--good:#4cc38a;--bad:#ff8f86;--warn:#e3b341;--mute:#6f7b88;--banner:#3a2f10;--bannerfg:#ffe29a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header,.wrap,footer{max-width:1040px;margin:0 auto;padding:0 16px}
header{padding-top:20px}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:20px;margin:0}
h3{font-size:16px;margin:22px 0 6px}
h4{font-size:14px;margin:0 0 6px}
.banner{background:var(--banner);color:var(--bannerfg);border:1px solid var(--line);border-radius:6px;
padding:10px 14px;font-weight:600;margin:10px 0 0}
.meta{color:var(--muted);margin:6px 0 0}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin:18px 0}
.head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px;
border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:12px}
.sim{color:var(--muted);font-size:13px;font-style:italic}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:0 0 14px}
.stat,.fig,.card{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.k{color:var(--muted);font-size:13px}
.big{font-size:24px;font-weight:700;line-height:1.25;overflow-wrap:anywhere}
.d{color:var(--muted);font-size:13px;margin-top:2px;overflow-wrap:anywhere}
.note{color:var(--muted);font-size:13px;margin:8px 0 0}
.note.strong{color:var(--fg);font-size:14px;font-weight:600}
.chart{width:100%;height:auto;display:block;margin:6px 0}
.chart.wide{height:30px;border-radius:4px}
.chart .grid{stroke:var(--line);stroke-width:1}
.chart .l{fill:var(--fg);font-size:13px}
.chart .sub,.chart .t{fill:var(--muted);font-size:12px}
.chart .cur{fill:var(--fg);font-size:12px;font-weight:700}
.chart .v{fill:var(--fg);font-size:13px;font-weight:600}
.chart .whisker{stroke:var(--fg);stroke-width:1.5}
.chart .gap{stroke:var(--bg);stroke-width:2}
.chart .line{fill:none;stroke-width:2.5}
.chart polyline.s1{stroke:var(--s1)}.chart polyline.s2{stroke:var(--s2)}
.s1{fill:var(--s1)}.s2{fill:var(--s2)}.s3{fill:var(--s3)}.good{fill:var(--good)}.bad{fill:var(--bad)}.mute{fill:var(--mute)}
.legend{list-style:none;padding:0;margin:8px 0 0;display:flex;flex-wrap:wrap;gap:4px 18px;font-size:13px}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-1px;margin-right:4px}
.sw.s1{background:var(--s1)}.sw.s2{background:var(--s2)}.sw.s3{background:var(--s3)}
.sw.good{background:var(--good)}.sw.bad{background:var(--bad)}.sw.mute{background:var(--mute)}
.sw.sq{border-radius:0}
.targets{list-style:none;padding:0;margin:0 0 8px}
.targets li{margin:4px 0}
.chip{display:inline-block;border:1px solid currentColor;border-radius:999px;padding:0 9px;font-size:12px;font-weight:700}
.chip.good{color:var(--good)}.chip.bad{color:var(--bad)}.chip.warn{color:var(--warn)}
.faith{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:14px;align-items:start}
.figs{display:flex;flex-direction:column;gap:8px;align-items:flex-start}
.why{border-left:3px solid var(--warn);padding-left:12px}
.notes{margin:0;padding-left:18px;font-size:13px}
.notes li{margin:3px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}
th,td{text-align:left;border-bottom:1px solid var(--line);padding:5px 8px;overflow-wrap:anywhere}
th{color:var(--muted);font-weight:600}
dl{display:grid;grid-template-columns:minmax(120px,200px) minmax(0,1fr);gap:6px 14px;margin:0}
dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
footer{color:var(--muted);font-size:13px;padding-bottom:28px}
@media (max-width:700px){.faith{grid-template-columns:1fr}}
"""


def render(
    s: RunSummary, history: Sequence[Mapping[str, Any]] = (), *, example: bool = False
) -> str:
    """The whole page for one summary and the run history. Raises `ReportError` rather than draw
    a number the formatter would refuse."""
    subtitle = j(
        "Run ",
        FMT.ident(s.run_id),
        " on the ",
        s.split,
        " split, ",
        FMT.count(s.n_alerts),
        " alerts",
    )
    panels = (
        safety(s)
        + value(s)
        + explanations(s)
        + monitoring(s, history, example)
        + run_facts(s, example)
    )
    kind: Literal["Example", "Results"] = "Example" if example else "Results"
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        "<title>Fraud review dashboard</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        f"<header><h1>Fraud review: {text(kind)}</h1>"
        f'<p class="banner">{text(BANNER)}</p>'
        f'<p class="meta">{subtitle}</p></header>\n'
        f'<div class="wrap">{panels}</div>\n'
        "<footer><p>Built from one summary file by the fraud command; the page loads nothing "
        "from the network. Numbers are formatted by one formatter and each panel states that it "
        "is on simulated data. Data source: Sparkov, a simulated card-transaction set.</p>"
        "</footer>\n</body>\n</html>\n"
    )


def render_example(root: Path) -> str:
    """DSH-05: the committed golden run, evaluated offline, drawn with the repo's run history."""
    from fraudreview import summary
    from fraudreview.config import load_config
    from fraudreview.policy import load_policy

    golden = root / "tests" / "fixtures" / "golden"
    if not (golden / "expected.json").is_file():
        raise ReportError(f"the golden fixture is not here ({golden}); run from a source checkout")
    built = summary.build(
        summary.load_golden(golden),
        load_config(root / "config.yaml"),
        load_policy(packaged("policy.md")),
        summary.read_baseline(root / summary.BASELINE_SUMMARY),
    )
    return render(built.summary, monitor.read_jsonl(root / monitor.RUNS), example=True)
