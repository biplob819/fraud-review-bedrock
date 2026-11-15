# fraud-review-bedrock

A fraud-review agent on Amazon Bedrock that triages flagged card transactions, explains each decision against a written policy, and sends anything it is unsure about to a person. It has no tool that can block a card or contact a customer. All data is simulated (Sparkov, through Amazon's Fraud Dataset Benchmark).

**Status: under construction.** The build follows a dependency-gated node graph; run `python scripts/status.py` for live progress.

- [PRD](PRD.md): what the pack does and why.
- [Tech Spec](TECH_SPEC.md): how it is built, design decisions, and the loophole review of the PRD.
- [Build graph](docs/graph/graph.md), [capability catalog](docs/graph/catalog.tsv), [decisions](docs/decisions.md), [lessons](docs/lessons.md), [changelog](changelog.md).

## Working on it

```bash
uv sync
uv run python scripts/status.py               # where the build is
uv run python scripts/node_start.py N1.1      # may this node start?
uv run python scripts/node_converge.py N1.1   # run all 12 gates for a node
```

MIT licence.
