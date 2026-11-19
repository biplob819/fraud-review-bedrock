import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import fraudreview
from fraudreview.cli import main

ROOT = Path(__file__).resolve().parent.parent


def test_version_string_matches_pyproject():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert fraudreview.__version__ == meta["version"] == "0.2.0"


def test_cli_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "fraud 0.2.0"


def test_cli_runs_as_module():
    out = subprocess.run(
        [sys.executable, "-m", "fraudreview.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "fraud 0.2.0"


@pytest.mark.contract
def test_pyproject_distribution_metadata():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["name"] == "fraud-review-bedrock"
    assert meta["requires-python"] == ">=3.12"
    assert meta["license"] == "MIT"
    assert meta["scripts"]["fraud"] == "fraudreview.cli:main"
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


def test_precommit_runs_ruff_and_secret_scan():
    text = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "id: ruff" in text and "secret_scan.py" in text


def test_precommit_hooks_parse_and_are_active():
    import yaml

    cfg = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {h["id"]: h for repo in cfg["repos"] for h in repo["hooks"]}
    assert "ruff" in hooks
    assert hooks["secret-scan"]["entry"] == "uv run python scripts/secret_scan.py"
    assert hooks["secret-scan"]["pass_filenames"] is False
