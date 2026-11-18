# B3.1 — ROI model

**Wave 4. Depends on:** E2.3.

## Goal
A workbook a bank team can change to see what the agent saves and when it pays back, with the leakage-loss term in the sum (US6, H1, D-14).

## Outputs
- `docs/roi/roi-model.xlsx`, built by `scripts/build_roi.py`, with marked input cells and formula cells.
- `src/fraudreview/roi.py`: the same formulas in Python, plus the break-even leakage rate.
- `docs/roi/README.md`: inputs, formulas and a worked example.

## Rules
- Defaults for the auto-clear rate, fraud share and leakage come from `summary-eval2.json`; the leakage default is the exact upper bound, labelled a bound, not an estimate. Every other default is labelled illustrative.
- Payback reads "no payback" when net saving is zero or below.
