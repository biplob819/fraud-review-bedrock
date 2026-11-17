# E2.3 run state

**Now:** green

## History
- 2026-11-17 — built in a parallel lane and merged. Screenshot checked by eye.
- 2026-11-17 — verifier pass 1: no must-fix; every headline number recomputed from raw sources and the mutations were caught, except a hard-coded Met chip and a note dropped from both summary and page. Fixed: the recall tile says it is among alerts the baseline raised; faithfulness notes are now required whenever the figure is shown. The dashboard was regenerated.

## Open should-fix (carried forward)
- Workflow actions are pinned to major-version tags, not SHAs (no network in the lane); pin to SHAs.
- The Pages workflow has not run; GitHub Pages must be enabled for the repository.
- The run-facts panel was not looked at in the screenshot.
- No test renders a leaking summary, so the page's failure chip is unproven; a dropped note is not compared with the cache summary.
- Minutes and cost tiles carry no n; the monitoring warning chip shows no band; the trend compares runs with different settings and samples without saying so.
- `fraud evaluate` appends to the history by default (opt-out); make it opt-in so a dev run cannot enter the eval trend.
- SVG labels are small on a phone (one media rule only).
- The published summary JSON holds 3 opaque alert ids in the mismatch examples (the page hashes them).
- Pages workflow: pin actions to SHAs, run the honesty gate before deploy, protect the github-pages environment.
