import re
import subprocess
from pathlib import Path

import pytest

import status
from harness_lib import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"


def _decision_rows(text: str) -> dict[str, list[str]]:
    rows = {}
    for line in text.splitlines():
        m = re.match(r"^\| ((?:OWNER )?D-\d+) \|", line)
        if m:
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            rows[m.group(1)] = cells
    return rows


def test_decisions_match_tech_spec_section_1():
    log = _decision_rows((ROOT / "docs" / "decisions.md").read_text(encoding="utf-8"))
    spec_text = (ROOT / "TECH_SPEC.md").read_text(encoding="utf-8")
    spec = _decision_rows(spec_text[: spec_text.index("## 2. Runtime pipeline")])
    assert set(range(1, 30)) <= {int(k.split("-")[1]) for k in log}
    assert spec.keys() == log.keys()
    for key, cells in spec.items():
        assert log[key][2:] == cells[1:], key  # log adds a date column after the id


def test_lessons_and_changelog_exist():
    assert "L-1" in (ROOT / "docs" / "lessons.md").read_text(encoding="utf-8")
    assert "[W0.1]" in (ROOT / "changelog.md").read_text(encoding="utf-8")


def test_status_prints_graph_and_node(capsys):
    assert status.main([]) == 0
    out = capsys.readouterr().out
    assert "W0.1" in out and "D4.2" in out and "capabilities green" in out
    assert status.main(["W0.1"]) == 0
    assert "HAR-07" in capsys.readouterr().out
    assert status.main(["Z9.9"]) == 1


def _yaml_code(path: Path) -> str:
    """Workflow text with comment-only lines and trailing comments removed."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split(" #", 1)[0].rstrip()
        if code.strip() and not code.lstrip().startswith("#"):
            lines.append(code)
    return "\n".join(lines)


@pytest.mark.contract
def test_ci_workflow_covers_gates_and_matrix():
    ci = _yaml_code(WORKFLOWS / "ci.yml")
    assert re.search(r"^on:\n  push:\n    branches: \[main\]\n  pull_request:", ci, re.M)
    assert "ruff format --check" in ci
    for step in ("ruff check", "pyright", "pytest", "uv build", "secret_scan.py"):
        assert step in ci
    assert "uv sync --locked" in ci
    assert "honesty_check.py" in ci and "verify_graph_eligibility.py" in ci
    assert '"3.12"' in ci
    for os_name in ("ubuntu-latest", "macos-latest"):
        assert os_name in ci
    assert "pull_request" in ci


def test_local_notes_and_guard_list_stay_out_of_public_repo():
    text = Path(ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*.local.md" in text and ".public-guard.local" in text


@pytest.mark.contract
def test_workflows_grant_least_privilege():
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        code = _yaml_code(wf)
        assert re.search(r"^permissions:\n", code, re.M), f"{wf.name} sets no top-level permissions"
        assert "secrets." not in code, f"{wf.name} reads repository secrets"
    ci = _yaml_code(WORKFLOWS / "ci.yml")
    assert re.search(r"^permissions:\n  contents: read$", ci, re.M)


def test_catalog_round_trip_is_byte_identical(tmp_path):
    from harness_lib import CATALOG_TSV, load_catalog, write_catalog

    out = tmp_path / "catalog.tsv"
    write_catalog(load_catalog(), out)
    assert out.read_bytes() == CATALOG_TSV.read_bytes()


def test_status_output_is_deterministic(capsys):
    status.main([])
    first = capsys.readouterr().out
    status.main([])
    assert capsys.readouterr().out == first


def test_gitattributes_force_lf():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in text


PUBLIC_DOCS = ("PRD.md", "TECH_SPEC.md", "README.md", "docs/graph/graph.md")
DATE_RE = re.compile(
    r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{2,4}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (?:\d{1,2}\b|20\d\d\b)"
    r"|\b\d{1,2}\.\d{1,2}\.20\d\d\b|\bQ[1-4] 20\d\d\b"
    r"|\b\d{1,2} ?[ap]\.m\.|\bat noon\b|\bmidnight\b|"
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.(?=\s)"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b"
    r"|\b\d{1,4}/\d{1,2}/\d{2,4}\b"
    r"|\b\d{1,2}\s?(?:am|pm)\b"
    r"|\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b"
    r"|\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d"
    r"|\b\d{1,2}:\d{2}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}\b"
    r"|\b\d{1,2} (?:January|February|March|April|May|June|July|August"
    r"|September|October|November|December)\b"
    r"|\b20\d\d-\d\d-\d\d\b"
)


def test_public_docs_carry_no_dates():
    for name in PUBLIC_DOCS:
        hits = DATE_RE.findall((ROOT / name).read_text(encoding="utf-8"))
        assert not hits, (name, hits[:3])


# Generic patterns are built from pieces so this file never matches its own scan.
# Sensitive terms live only in the git-ignored .public-guard.local.
HOME = "~" + "/"
USERS = "/" + "Users" + "/"
GENERIC_PRIVATE = [
    re.escape(HOME) + r"(?!\.(?:kaggle|aws|config)/)",  # standard tool config paths are fine
    re.escape(USERS),
]
GUARD_FILE = ROOT / ".public-guard.local"


def _tracked_text_files():
    """Every tracked file as staged in the index (what a commit would publish)."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    for rel in filter(None, out.split("\0")):
        if Path(rel).suffix in {".png", ".whl"}:
            continue
        blob = subprocess.run(
            ["git", "show", f":{rel}"], cwd=ROOT, capture_output=True, check=True
        ).stdout
        yield rel, rel + "\n" + blob.decode("utf-8", errors="replace")  # the path counts too


def private_patterns() -> list[str]:
    pats = list(GENERIC_PRIVATE)
    if GUARD_FILE.is_file():
        for line in GUARD_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def test_tracked_files_carry_no_private_content():
    banned = re.compile("|".join(f"(?:{p})" for p in private_patterns()), re.I)
    hits = [
        f"{rel}:{n}: {m.group(0)}"
        for rel, text in _tracked_text_files()
        for n, line in enumerate(text.splitlines(), 1)
        for m in [banned.search(line)]
        if m
    ]
    assert not hits, hits[:10]


def test_private_guard_catches_a_planted_leak():
    banned = re.compile("|".join(f"(?:{p})" for p in private_patterns()), re.I)
    assert banned.search("see " + HOME + "secret-notes")
    assert banned.search("at " + USERS + "someone/x")
    assert not banned.search("token in " + HOME + ".kaggle/kaggle.json")


@pytest.mark.contract
def test_ci_steps_parse_and_run_every_gate():
    import yaml

    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    triggers = ci[True] if True in ci else ci["on"]  # YAML 1.1 reads `on` as True
    assert triggers["push"]["branches"] == ["main"] and "pull_request" in triggers
    job = ci["jobs"]["test"]
    assert job["env"]["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert job["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest"],
        "python": ["3.12"],
    }
    runs = [s["run"] for s in job["steps"] if "run" in s]
    for cmd in (
        "uv sync --locked",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run pyright",
        "uv run pytest -q",
        "uv run python scripts/secret_scan.py",
        "uv run python scripts/verify_graph_eligibility.py",
        "uv run python scripts/honesty_check.py",
        "uv build",
    ):
        assert cmd in runs, cmd
