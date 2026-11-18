"""B3.1: the Python formulas match the workbook, evaluated offline (ROI-04)."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import load_workbook

from build_roi import build
from fraudreview import roi
from fraudreview.roi import NO_PAYBACK, RoiInputs, compute

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "docs" / "measurements" / "summary-eval2.json"
INPUT_CELLS = dict(
    zip(
        [
            "alerts_per_month",
            "minutes_per_alert",
            "cost_per_hour",
            "auto_clear_rate",
            "capacity_share",
            "fixed_cost",
            "fraud_share",
            "leakage_rate",
            "avg_fraud_loss",
        ],
        [f"C{r}" for r in range(5, 14)],
        strict=True,
    )
)
OUT_CELLS = {
    "hours_saved": "C17",
    "gross_saving": "C18",
    "leakage_loss": "C19",
    "net_saving": "C20",
    "payback_months": "C21",
}

TOKEN = re.compile(r'\s*(\d+\.?\d*|"[^"]*"|[A-Z]+\d+|IF|<=|>=|<|>|=|.)')
Value = float | bool | str


def evaluate(formula: str, cells: dict[str, object]) -> Value:
    """A minimal evaluator: numbers, strings, cell refs, + - * /, parentheses, comparisons, IF."""
    tokens = TOKEN.findall(formula.removeprefix("="))
    pos = 0

    def peek() -> str:
        return tokens[pos] if pos < len(tokens) else ""

    def take() -> str:
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def num(v: Value) -> float:
        assert isinstance(v, (int, float)) and not isinstance(v, bool)
        return float(v)

    def expr() -> Value:
        left = arith()
        if peek() in ("<=", ">=", "<", ">", "="):
            op = take()
            a, b = num(left), num(arith())
            return {"<=": a <= b, ">=": a >= b, "<": a < b, ">": a > b, "=": a == b}[op]
        return left

    def arith() -> Value:
        v = term()
        while peek() in ("+", "-"):
            op = take()
            r = term()
            v = num(v) + num(r) if op == "+" else num(v) - num(r)
        return v

    def term() -> Value:
        v = atom()
        while peek() in ("*", "/"):
            op = take()
            r = atom()
            v = num(v) * num(r) if op == "*" else num(v) / num(r)
        return v

    def atom() -> Value:
        t = take()
        if t == "(":
            v = expr()
            assert take() == ")"
            return v
        if t == "IF":
            assert take() == "("
            cond = expr()
            assert take() == ","
            a = expr()
            assert take() == ","
            b = expr()
            assert take() == ")"
            return a if cond else b
        if t.startswith('"'):
            return t[1:-1]
        if re.fullmatch(r"[A-Z]+\d+", t):
            v = cells[t]
            if isinstance(v, str) and v.startswith("="):
                return evaluate(v, cells)
            assert isinstance(v, (int, float))
            return float(v)
        return float(t)

    result = expr()
    assert pos == len(tokens), f"unparsed tokens in {formula}"
    return result


@pytest.fixture(scope="module")
def workbook_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build(tmp_path_factory.mktemp("roi") / "roi-model.xlsx")


def workbook_values(path: Path, inputs: RoiInputs) -> dict[str, Value]:
    ws = load_workbook(path)["ROI"]
    cells: dict[str, object] = {c.coordinate: c.value for row in ws.iter_rows() for c in row}
    for name, ref in INPUT_CELLS.items():
        cells[ref] = getattr(inputs, name)
    return {name: evaluate(str(cells[ref]), cells) for name, ref in OUT_CELLS.items()}


D = RoiInputs()
SETS = [
    D,
    replace(D, leakage_rate=0.0),
    replace(
        D,
        alerts_per_month=50000,
        minutes_per_alert=6,
        cost_per_hour=55,
        capacity_share=0.8,
        fixed_cost=90000,
        leakage_rate=0.0005,
        avg_fraud_loss=300,
    ),
]


@pytest.mark.parametrize("inputs", SETS, ids=["defaults", "zero-leakage", "custom"])
def test_python_matches_workbook(workbook_path: Path, inputs: RoiInputs) -> None:
    sheet = workbook_values(workbook_path, inputs)
    py = compute(inputs)
    for name in OUT_CELLS:
        a, b = sheet[name], getattr(py, name)
        if isinstance(b, str):
            assert a == b
        else:
            assert a == pytest.approx(b, rel=1e-9)


def test_no_payback_when_net_not_positive(workbook_path: Path) -> None:
    assert compute(D).net_saving < 0
    assert compute(D).payback_months == NO_PAYBACK
    assert workbook_values(workbook_path, D)["payback_months"] == NO_PAYBACK
    zero = replace(D, leakage_rate=0.0, capacity_share=0.0)
    assert compute(zero).net_saving == 0
    assert compute(zero).payback_months == NO_PAYBACK
    assert workbook_values(workbook_path, zero)["payback_months"] == NO_PAYBACK


def test_leakage_above_break_even_flips_sign(workbook_path: Path) -> None:
    be = roi.break_even_leakage(D)
    assert 0 < be < D.leakage_rate
    below = compute(replace(D, leakage_rate=be * 0.99))
    above = compute(replace(D, leakage_rate=be * 1.01))
    assert below.net_saving > 0
    assert isinstance(below.payback_months, float)
    assert above.net_saving < 0
    assert above.payback_months == NO_PAYBACK
    sheet = workbook_values(workbook_path, replace(D, leakage_rate=be * 1.01))
    assert sheet["net_saving"] < 0  # type: ignore[operator]


def test_workbook_inputs_are_marked_and_outputs_are_formulas(workbook_path: Path) -> None:
    ws = load_workbook(workbook_path)["ROI"]
    for ref in INPUT_CELLS.values():
        assert str(ws[ref].fill.fgColor.rgb).endswith("FFF2CC")
    for ref in OUT_CELLS.values():
        assert str(ws[ref].value).startswith("=")


def test_defaults_match_summary() -> None:
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert s["data_label"] == "simulated"
    assert s["auto_clear_rate"]["point"] == roi.AUTO_CLEAR_RATE
    assert s["leakage"]["hi"] == roi.LEAKAGE_UPPER_BOUND
    assert s["n_frauds"] / s["n_alerts"] == roi.FRAUD_SHARE
    assert s["minutes_per_alert"] == roi.MINUTES_PER_ALERT


def test_committed_workbook_matches_builder(workbook_path: Path) -> None:
    def dump(p: Path) -> list[tuple[str, object]]:
        ws = load_workbook(p)["ROI"]
        cells = [c for row in ws.iter_rows() for c in row]
        return [(c.coordinate, c.value) for c in cells if c.value is not None]

    assert dump(ROOT / "docs" / "roi" / "roi-model.xlsx") == dump(workbook_path)


def test_discovery_summary_uses_the_roi_defaults() -> None:
    text = (ROOT / "docs" / "discovery-summary.md").read_text(encoding="utf-8")
    out = compute(D)
    expected = [
        f"{D.alerts_per_month:,.0f}",
        f"{D.minutes_per_alert:.0f} minutes",
        f"${D.cost_per_hour:.0f}",
        f"{D.fraud_share:.0%}",
        f"{D.auto_clear_rate:.1%}",
        f"{D.leakage_rate:.1%}",
        f"${D.avg_fraud_loss:.0f}",
        f"{D.alerts_per_month * D.minutes_per_alert / 60:,.0f} analyst hours",
        f"{out.hours_saved:.0f} analyst hours",
    ]
    for item in expected:
        assert item in text, item
    assert "illustrative" in text.lower()
    assert "simulated" in text.lower()
