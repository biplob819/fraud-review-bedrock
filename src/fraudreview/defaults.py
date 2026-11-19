"""Where the shipped default files are (policy.md, config.yaml, prices.yaml).

In a source checkout they are the files at the repository root. In an installed wheel the build
copies them to `fraudreview/_defaults/`, so a pip install works without a clone."""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent


def packaged(name: str) -> Path:
    """The default copy of `name`: the repository root file if there is one, else the wheel's."""
    repo = _HERE.parents[1] / name
    return repo if repo.is_file() else _HERE / "_defaults" / name
