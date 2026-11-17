# E2.5 acceptance criteria

**EVK-01 — rule compliance**
- `compliance` agrees with `policy.clearing_allowed` and router rules 2–4 on accept or refuse for every clear among 2,000+ seeded alerts, with the same reasons once names are mapped; P5.9 night cases refuse.
- A missing value fails safe, as in the router.

**EVK-02 — structured evidence and value checks**
- `evidence` gives one sentence per glossary field.
- The strict local `valuecheck` verifies true claims, contradicts false ones, and never returns `verified` for a false claim (a seeded mutation test), and `value_shares` counts every reason once.

**EVK-04 — pin**
- `pyproject.toml` pins EvalKit to the exact v0.3.0 commit, and its public symbols import at that version.
