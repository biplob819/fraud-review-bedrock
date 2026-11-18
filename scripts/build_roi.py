#!/usr/bin/env python3
"""Build docs/roi/roi-model.xlsx from the defaults in fraudreview.roi (B3.1).

Usage: python scripts/build_roi.py [out.xlsx]
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from fraudreview.roi import NO_PAYBACK, RoiInputs

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "roi" / "roi-model.xlsx"

INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
OUTPUT_FILL = PatternFill("solid", fgColor="E2EFDA")

D = RoiInputs()
# (symbol, label, default, number format, source note)
INPUT_ROWS = [
    ("A", "Alerts per month", D.alerts_per_month, "#,##0", "Illustrative assumption"),
    ("m", "Minutes per alert", D.minutes_per_alert, "0.0", "Assumption used in summary-eval2.json (simulated)"),
    ("c", "Cost per analyst hour (USD)", D.cost_per_hour, "#,##0.00", "Illustrative assumption"),
    ("r", "Auto-clear rate", D.auto_clear_rate, "0.0%", "summary-eval2.json, 44 of 500 (simulated)"),
    ("k", "Analyst capacity share", D.capacity_share, "0%", "Illustrative: share of freed hours that becomes a saving"),
    ("B", "Baseline fixed cost, one-off (USD)", D.fixed_cost, "#,##0", "Illustrative assumption: build and set-up"),
    ("f", "Fraud share of alerts", D.fraud_share, "0.0%", "summary-eval2.json, 55 of 500 (simulated)"),
    ("λ", "Leakage rate", D.leakage_rate, "0.00%", "summary-eval2.json, exact 95% UPPER BOUND for 0 of 55 (simulated); a bound, not an estimate"),
    ("v", "Average fraud loss (USD)", D.avg_fraud_loss, "#,##0", "Illustrative assumption"),
]  # fmt: skip


def build(out: Path = OUT) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "ROI"
    ws["A1"] = (
        "Fraud-review agent: ROI model (illustrative; inputs marked simulated come from simulated data)"
    )
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "Legend:"
    ws["B2"] = "Input cell (change these)"
    ws["B2"].fill = INPUT_FILL
    ws["C2"] = "Formula cell (do not edit)"
    ws["C2"].fill = OUTPUT_FILL
    for col, head in zip("ABCD", ["Symbol", "Name", "Value", "Source"], strict=True):
        ws[f"{col}4"] = head
        ws[f"{col}4"].font = Font(bold=True)
    # Rows: A=5 m=6 c=7 r=8 k=9 B=10 f=11 λ=12 v=13
    for n, (sym, label, val, fmt, note) in enumerate(INPUT_ROWS):
        row = 5 + n
        ws[f"A{row}"], ws[f"B{row}"], ws[f"C{row}"], ws[f"D{row}"] = sym, label, val, note
        ws[f"C{row}"].fill = INPUT_FILL
        ws[f"C{row}"].number_format = fmt
    ws["A16"] = "Outputs"
    ws["A16"].font = Font(bold=True)
    outs = [
        ("Hours saved per month", "=C5*C8*C6/60", "#,##0.0", "A × r × m / 60"),
        ("Gross saving per month (USD)", "=C17*C7*C9", "#,##0", "hours × c × k"),
        ("Leakage loss per month (USD)", "=C12*C11*C5*C13", "#,##0", "λ × f × A × v"),
        ("Net saving per month (USD)", "=C18-C19", "#,##0", "gross − leakage loss"),
        ("Payback (months)", f'=IF(C20<=0,"{NO_PAYBACK}",C10/C20)', "0.0", "B / net saving"),
        ("Break-even leakage rate", "=C18/(C11*C5*C13)", "0.000%", "gross / (f × A × v)"),
    ]
    for n, (label, formula, fmt, note) in enumerate(outs):
        row = 17 + n
        ws[f"B{row}"], ws[f"C{row}"], ws[f"D{row}"] = label, formula, note
        ws[f"C{row}"].fill = OUTPUT_FILL
        ws[f"C{row}"].number_format = fmt
        ws[f"C{row}"].alignment = Alignment(horizontal="right")
    ws["A24"] = "Payback shows 'no payback' when net saving is zero or below."
    ws["A25"] = (
        "Default λ is an upper bound, so the default result is a worst case, not a forecast."
    )
    for col, w in zip("ABCD", [8, 38, 16, 90], strict=True):
        ws.column_dimensions[col].width = w
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT))
