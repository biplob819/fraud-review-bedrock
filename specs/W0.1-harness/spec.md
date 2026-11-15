# W0.1 — Repo and harness

**Wave 0. Depends on:** nothing. **Unblocks:** W0.2, N1.1 and through them every node.

## Goal
A public repo with a working uv project and the node-graph build harness ported from EvalKit (D-13), so every later node is dependency-gated and proven by self-tested gates.

## Outputs
- uv project `fraud-review-bedrock` (Python ≥3.12, hatchling, src layout), package `fraudreview`, console script `fraud` (`--version` only for now), MIT licence, ruff pre-commit with the secret scan, `.gitattributes` forcing LF (HAR-01).
- `.github/workflows/ci.yml`: ruff, format check, pyright, pytest, secret scan, graph eligibility, honesty check and a wheel build on every push and pull request; Linux and macOS, Python 3.12; read-only permissions (HAR-02).
- Ported scripts from [EvalKit's `scripts/`](https://github.com/biplob819/evalkit/tree/main/scripts): `harness_lib.py` (node ids `W N A E B D`), `verify_graph_eligibility.py` (HAR-03), `node_start.py` (HAR-04), `node_converge.py` (HAR-05), `status.py`, `secret_scan.py` (HAR-07). Gate 8 is the honesty gate in place of EvalKit's judge gate; minimum Python 3.12 (D-24).
- Records (HAR-06): `docs/graph/graph.md` and `catalog.tsv` generated from Tech Spec §11.2 and §11.4; `docs/decisions.md` with D-1 to D-29; `docs/lessons.md` seeded with 17 EvalKit lessons; `changelog.md`.
- `scripts/honesty_check.py` (HAR-08): every line of a file listed in `docs/published.txt` that holds a metric must itself say "simulated"; ships a self-test. The summary.json cross-check arrives in E2.3.
- `PRD.md` (product-only, OWNER D-23, D-28) and `TECH_SPEC.md`; local notes (`*.local.md`) git-ignored.

## Out of scope
Any `fraud` subcommand beyond `--version` (W0.2 onwards); the Pages workflow (E2.3).

## Constraints
- No dates or time slots in the PRD, Tech Spec, README or graph (OWNER D-17); logs (decisions, changelog, RUN-STATE) keep their timestamps.
- Repository operations as `biplob819` (D-25); push only after the node is green (OWNER D-22).
