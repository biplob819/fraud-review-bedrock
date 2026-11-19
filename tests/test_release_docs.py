"""Release docs (D4.3, OSS-01..06): links resolve, named commands exist, workflows parse, and no
data file is tracked."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from fraudreview.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "docs/versioning.md",
    "docs/guide/README.md",
    "docs/guide/policy.md",
    "docs/guide/own-data.md",
    "docs/guide/cost.md",
    "docs/guide/privacy.md",
]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^```[a-z]*\n(.*?)^```", re.M | re.S)

# every important artifact the README must link to (D-32)
README_LINKS = [
    "docs/dashboard.html",
    "demo/README.md",
    "docs/case-study.md",
    "docs/pr-faq.md",
    "docs/roi/README.md",
    "docs/discovery-summary.md",
    "docs/patterns.md",
    "docs/assets/architecture.png",
    "DATA_CARD.md",
    "docs/decisions.md",
    "TECH_SPEC.md",
    "docs/measurements/spend-ledger.jsonl",
    "docs/guide/policy.md",
    "docs/guide/own-data.md",
    "docs/guide/cost.md",
    "docs/guide/privacy.md",
]
SCREENSHOTS = [
    "docs/assets/screens/dashboard.png",
    "docs/assets/screens/review-case.png",
    "docs/assets/screens/review-audit.png",
    "docs/assets/architecture.png",
]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _targets(rel: str) -> list[str]:
    return [m.group(1) for m in LINK_RE.finditer(_text(rel))]


@pytest.mark.contract
@pytest.mark.parametrize("rel", DOCS)
def test_every_relative_link_exists(rel):
    base = (ROOT / rel).parent
    for target in _targets(rel):
        if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue  # a web address, mailto or an anchor in the same page
        path = target.split("#", 1)[0]
        assert (base / path).resolve().exists(), f"{rel} links to a missing path: {target}"


@pytest.mark.contract
def test_readme_links_every_important_artifact_and_screenshot():
    targets = set(_targets("README.md"))
    for rel in README_LINKS + SCREENSHOTS:
        assert rel in targets, f"README.md does not link {rel}"
    images = set(re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", _text("README.md")))
    for rel in SCREENSHOTS:
        assert rel in images, f"README.md does not show {rel}"


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def _fraud_commands(rel: str) -> list[list[str]]:
    """Every `fraud ...` command inside a fenced code block, as argument lists."""
    found: list[list[str]] = []
    for block in FENCE_RE.findall(_text(rel)):
        for raw in block.splitlines():
            line = raw.split("#", 1)[0].strip()
            tokens = shlex.split(line) if line else []
            if "fraud" not in tokens:
                continue
            found.append(tokens[tokens.index("fraud") + 1 :])
    return found


@pytest.mark.contract
@pytest.mark.parametrize("rel", DOCS)
def test_every_fraud_command_in_the_docs_exists(rel):
    subs = _subcommands()
    for args in _fraud_commands(rel):
        flags = [a.split("=", 1)[0] for a in args if a.startswith("-")]
        if not args or args[0].startswith("-"):
            assert set(flags) <= {"--version", "-h", "--help"}, (rel, args)
            continue
        assert args[0] in subs, f"{rel}: `fraud {args[0]}` is not a command"
        known = {opt for act in subs[args[0]]._actions for opt in act.option_strings}
        for flag in flags:
            assert flag in known, f"{rel}: `fraud {args[0]}` has no flag {flag}"


@pytest.mark.contract
def test_quickstart_names_the_steps_in_order():
    text = _text("README.md")
    steps = [
        "uv sync --locked",
        "fraud report --example",
        "streamlit run demo/app.py",
        "fraud doctor --offline",
        "fraud triage --split dev --alerts 100 --dry-run",
        "max_cost_usd",
        "ANTHROPIC_API_KEY",
        "provider: bedrock",
    ]
    last = -1
    for step in steps:
        at = text.find(step)
        assert at > last, f"README quickstart: {step!r} is missing or out of order"
        last = at
    assert "not run for the published results" in text or "was not run for the published" in text
    assert "no flag for it" in text  # the cap is a config key, not a command-line flag


@pytest.mark.contract
def test_readme_states_what_is_planned_and_what_is_not():
    text = _text("README.md")
    assert "planned for v0.2" in text
    assert "There is no video" in text
    assert "PyPI" in text


@pytest.mark.contract
def test_readme_numbers_come_from_the_summary_and_the_prfaq():
    summary = json.loads(_text("docs/measurements/summary-eval2.json"))
    prfaq = _text("docs/pr-faq.md")
    readme = _text("README.md")
    leak = summary["leakage"]
    clear = summary["auto_clear_rate"]
    faith = summary["explanation"]["faithfulness"]["claude-opus-5-5"]["point"]
    expected = {
        f"{leak['k']} of {leak['n']}": "frauds",
        f"{leak['hi'] * 100:.1f}%": "upper bound",
        f"{clear['point'] * 100:.1f}%": "auto-clear",
        f"{clear['k']} of {clear['n']}": "alerts",
        f"${summary['cost_per_1000_usd']['point']:.2f}": "per 1,000",
        f"{summary['explanation']['values_true']['k']:,}": "checkable",
        f"{faith * 100:.1f}%": "judged",
    }
    for number, why in expected.items():
        assert number in readme, f"README is missing {number} ({why})"
    assert "2 of 79" in readme and "2 of 79" in prfaq  # the failed first run
    assert "36.8%" in readme and "36.8%" in prfaq
    assert "simulated" in readme.lower()


@pytest.mark.contract
def test_data_files_are_not_tracked():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    tracked = [p for p in out.split("\0") if p]
    banned = {".csv", ".parquet", ".feather", ".pkl", ".pickle", ".npz", ".npy", ".h5", ".zip"}
    allowed_jsonl = ("tests/fixtures/", "demo/replay.jsonl", "docs/measurements/")
    for rel in tracked:
        path = Path(rel)
        assert path.suffix.lower() not in banned, f"data file tracked: {rel}"
        assert path.parts[0] not in {"data", "results"}, f"data folder tracked: {rel}"
        if path.suffix == ".jsonl":
            assert rel.startswith(allowed_jsonl), f"unexpected jsonl tracked: {rel}"
            assert (ROOT / rel).stat().st_size < 2_000_000, f"jsonl too large: {rel}"


@pytest.mark.contract
def test_licence_is_mit_with_the_owner_and_the_data_credit_is_named():
    project = tomllib.loads(_text("pyproject.toml"))["project"]
    owner = project["authors"][0]["name"]
    licence = _text("LICENSE")
    assert project["license"] == "MIT"
    assert licence.startswith("MIT License")
    assert owner in licence
    notice = _text("NOTICE")
    for phrase in ("Sparkov", "Fraud Dataset Benchmark", "fraud load", "No data file is committed"):
        assert phrase in notice, f"NOTICE lacks {phrase!r}"


@pytest.mark.contract
def test_community_files_exist_and_say_what_they_must():
    security = _text("SECURITY.md")
    assert ".env" in security and "ANTHROPIC_API_KEY" in security
    assert "private security advisory" in security
    assert "Code of Conduct" in _text("CONTRIBUTING.md")
    assert (ROOT / "CODE_OF_CONDUCT.md").is_file()
    for name in ("bug_report.md", "feature_request.md"):
        text = _text(f".github/ISSUE_TEMPLATE/{name}")
        front = yaml.safe_load(text.split("---")[1])
        assert front["name"] and front["about"]
    assert (ROOT / ".github" / "pull_request_template.md").is_file()


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    return wf[True] if True in wf else wf["on"]  # YAML 1.1 reads `on` as True


def _run_steps(job: dict) -> str:
    return "\n".join(str(s["run"]) for s in job["steps"] if "run" in s)


@pytest.mark.contract
def test_release_workflow_is_tag_triggered_least_privilege_and_never_publishes():
    wf = _workflow("release.yml")
    assert _on(wf) == {"push": {"tags": ["v*"]}}
    assert wf["permissions"] == {"contents": "write"}
    job = wf["jobs"]["release"]
    assert "permissions" not in job  # nothing wider than the top level
    runs = _run_steps(job)
    for needed in ("uv sync --locked", "uv run pytest", "uv build", "gh release create"):
        assert needed in runs, f"release.yml lacks {needed}"
    assert "changelog_section.py" in runs
    assert "dist/*.whl" in runs and "dist/*.tar.gz" in runs
    raw = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in raw.splitlines()).lower()
    for word in ("pypi", "twine", "uv publish", "trusted-publishing", "id-token", "secrets."):
        assert word not in code, f"release.yml must not use {word}"
    assert all("workflow_dispatch" not in str(k) for k in _on(wf))


@pytest.mark.contract
def test_smoke_workflow_is_a_fresh_clone_on_linux_and_macos():
    wf = _workflow("smoke.yml")
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["smoke"]
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "macos-latest"]
    clone = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert clone["with"]["path"] == "fresh"
    assert all(
        s.get("working-directory") == "fresh"
        for s in job["steps"]
        if "run" in s and "brew" not in s["run"]
    )
    runs = _run_steps(job)
    for needed in ("uv sync --locked", "fraud --version", "fraud report --example"):
        assert needed in runs, f"smoke.yml lacks {needed}"
    assert "results" in runs  # it asserts the clone has no results folder


@pytest.mark.contract
def test_changelog_section_picks_the_named_version_or_the_newest():
    sys.path.insert(0, str(ROOT / "scripts"))
    from changelog_section import section_for

    text = "# Changelog\n\n## v0.2.0\n- two\n\n## v0.1.0\n- one\n\n## 0.0.1\n- zero\n"
    assert section_for("v0.1.0", text) == "- one"
    assert section_for("v0.0.1", text) == "- zero"
    assert section_for("v0.2.0", text) == "- two"
    assert section_for("v0.3.0", text).startswith("_The changelog has no section for v0.3.0")
    assert "- two" in section_for("v0.3.0", text)
    assert section_for("v0.1", text).startswith("_The changelog")  # not a prefix match
    with pytest.raises(ValueError):
        section_for("v1.0.0", "# Changelog\n")
    real = section_for("v0.1.0", _text("changelog.md"))
    assert real.strip()


@pytest.mark.contract
def test_versioning_policy_names_the_release_steps():
    text = _text("docs/versioning.md")
    for phrase in ("MAJOR.MINOR.PATCH", "uv build", "Releases are not published to PyPI"):
        assert phrase in text


def test_release_workflow_requires_the_tag_to_be_on_main_and_docs_say_the_wheel_is_not_standalone():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "merge-base --is-ancestor" in wf and "origin/main" in wf
    assert "standalone" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "standalone" in (ROOT / "docs" / "versioning.md").read_text(encoding="utf-8")


@pytest.mark.contract
def test_every_workflow_action_is_pinned_to_a_full_commit_sha_with_its_version():
    pinned = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*v\d+(\.\d+)*$")
    found = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"\buses:\s*(\S.*)$", line)
            if m and not m.group(1).startswith("./"):
                found += 1
                assert pinned.match(m.group(1).strip()), f"{path.name}:{n} is not pinned: {line}"
    assert found >= 8


@pytest.mark.contract
def test_pypi_publish_is_a_manual_gated_trusted_publishing_workflow():
    wf = _workflow("publish-pypi.yml")
    assert set(_on(wf)) == {"workflow_dispatch"}  # never on a push or tag
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["publish"]
    assert job["environment"] == "pypi"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    raw = (WORKFLOWS / "publish-pypi.yml").read_text(encoding="utf-8").lower()
    assert "secrets." not in raw and "password" not in raw  # no stored token
