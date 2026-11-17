# E2.3 — Summary and dashboard

**Wave 3. Depends on:** E2.1, E2.2, E2.4. **Unblocks:** D4.2, D4.3.

## Goal
Turn a finished run into one validated `summary.json` and one self-contained dashboard that shows safety, value, explanation quality and the trend over runs, with every figure labelled as simulated (D-14, D-36, D-41).

## Outputs
- `src/fraudreview/summary.py`, `summary_model.py` and `fraud evaluate`: the summary, built from the metrics, explanation checks, value-check shares, the faithfulness partial summary and the monitor. Extensions are subclasses of the v1 summary so the freeze digest and fixtures are untouched. Exit 1 when any threshold fails; any leak fails.
- `src/fraudreview/report.py` and `fraud report [--example]`: one static HTML page with inline SVG, no external requests, light and dark themes. Faithfulness is always shown with its notes beside it. `--example` renders from the committed golden run (20 alerts).
- One number formatter; the honesty gate now checks HTML files per section. `docs/dashboard.html` and `docs/measurements/summary-eval2.json` are in `docs/published.txt`.
- `.github/workflows/pages.yml`: rebuilds the dashboard from the committed summary on pushes to main and deploys to GitHub Pages (D-41). Not yet run.

## Rules
- No figure without its n; no rate without its interval; the data label travels with every panel.
- Faithfulness is the single-judge partial (D-43) and says so.
