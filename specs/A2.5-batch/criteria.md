# A2.5 acceptance criteria

**RUN-01: batch run**
- Every case gets exactly one disposition, in input order.
- Concurrency never exceeds `concurrency`.
- The run folder holds decisions, dispositions, a verified audit chain whose head is recorded in `run.json`, and the run facts.
- If the spend cap stops the run, it exits 3 and writes the finished alerts; a rerun completes it, and finished calls cost nothing.
- Progress lines show alerts done and spend so far.

**RUN-02: dev log**
- Each dev run appends one line with its settings digest and dev result to `dev-runs.jsonl`.

**RUN-03: freeze**
- `fraud freeze` records the settings with a digest. A change to the prompt, policy, model, effort, output limit, router setting, seed or code version changes the digest.
- `fraud triage --split eval` exits 2 with the differing fields when they differ, or when nothing is frozen.

**RUN-04: eval run**
- The eval run is done only after owner approval. Its spend is logged and reported.

**RUN-05: golden fixture**
- `tests/fixtures/golden` holds 20 alerts. Replaying them with a client that fails on any call reproduces the recorded dispositions exactly (`pytest -m golden`).
