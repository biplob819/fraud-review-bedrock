from pathlib import Path

import pytest

import node_converge as nc
from harness_lib import PROOF_DIMENSIONS, parse_catalog, parse_graph

HDR = "cap_id\tnode\tstatus\tproof\tnote\n"
GRAPH = parse_graph("| **W0.1** | Harness | — |\n| **N1.1** | Schemas | W0.1 |\n")


def ctx(tmp: Path, rows: str, node: str = "W0.1") -> nc.Ctx:
    return nc.Ctx(node, tmp, GRAPH, parse_catalog(HDR + rows), tmp / "specs")


def test_self_test_shows_every_gate_can_fail():
    assert nc.self_test() == 0


def test_twelve_gates_in_documented_order():
    assert [name for name, _ in nc.GATES] == [
        "claims-work",
        "eligibility",
        "proofs",
        "ruff",
        "pyright",
        "pytest",
        "golden",
        "honesty",
        "safety",
        "build",
        "changed-tests",
        "records",
    ]


def test_claims_work(tmp_path):
    assert nc.gate_claims_work(ctx(tmp_path, "H\tW0.1\tnot-started\t\t\n")).status == "FAIL"
    assert nc.gate_claims_work(ctx(tmp_path, "H\tW0.1\tgreen\t\t\n")).status == "PASS"


def test_proof_must_cover_all_five_dimensions(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_t.py").write_text("def test_a(): pass\n", encoding="utf-8")
    full = "; ".join(f'{d}=tests/test_t.py›"test_a"' for d in PROOF_DIMENSIONS)
    assert nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{full}\t\n")).status == "PASS"
    partial = full.rsplit(";", 1)[0]
    r = nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{partial}\t\n"))
    assert r.status == "FAIL" and "missing safety proof" in r.detail


def test_proof_rejects_unknown_keys_and_bad_format(tmp_path):
    (tmp_path / "t.py").write_text("def test_a(): pass\n", encoding="utf-8")
    full = "; ".join(f'{d}=t.py›"test_a"' for d in PROOF_DIMENSIONS)
    r = nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{full}; vibes=n/a:looks fine to me\t\n"))
    assert r.status == "FAIL" and "unknown proof keys" in r.detail
    r = nc.gate_proofs(ctx(tmp_path, "H\tW0.1\tgreen\tlogical=t.py test_a\t\n"))
    assert r.status == "FAIL"


def test_pytest_no_tests_is_skip_not_pass(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    assert nc._pytest(tmp_path, "golden", "golden").status == "SKIP"
    assert nc._pytest(tmp_path, "not golden", "unit").status == "PASS"


def test_changed_tests_rule():
    assert nc.changed_tests_problem(["scripts/node_start.py"]) is not None
    assert nc.changed_tests_problem(["docs/decisions.md"]) is None
    assert nc.changed_tests_problem(["src/fraudreview/a.py", "tests/test_a.py"]) is None


def test_records_require_changelog_criteria_and_run_state(tmp_path):
    caps = parse_catalog(HDR + "H-1\tW0.1\tgreen\t\t\n")
    problems = nc.records_problems("W0.1", caps, tmp_path, tmp_path / "specs")
    assert "changelog.md has no [W0.1] entry" in problems


def test_a_crashing_gate_counts_as_failure(monkeypatch, capsys):
    def boom(_ctx):
        raise RuntimeError("kaput")

    monkeypatch.setattr(nc, "GATES", [("boom", boom)])
    assert nc.main(["W0.1", "--no-log"]) == 1
    assert "gate crashed" in capsys.readouterr().out


def _proof(test: str, path: str = "tests/test_p.py") -> str:
    return "; ".join(f'{d}={path}›"{test}"' for d in PROOF_DIMENSIONS)


def test_proofs_require_a_real_unskipped_test_function(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_p.py").write_text(
        "import pytest\n# test_comment\nX = 'test_string'\n"
        "def test_abcdef():\n    pass\n"
        "@pytest.mark.skipif(True, reason='x')\ndef test_skipped():\n    pass\n"
        "class TestGroup:\n    def test_method(self):\n        pass\n",
        encoding="utf-8",
    )
    passes = ["test_abcdef", "test_method"]
    fails = ["test_comment", "test_string", "test_abc", "test_skipped"]
    for name in passes:
        assert nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{_proof(name)}\t\n")).status == "PASS"
    for name in fails:
        r = nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{_proof(name)}\t\n"))
        assert r.status == "FAIL", name


def test_proofs_must_live_under_tests(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("def test_a(): pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_p.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    for path in ("README.md", "tests/../README.md", "/etc/hosts"):
        r = nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{_proof('test_a', path)}\t\n"))
        assert r.status == "FAIL", path


def test_logical_proof_cannot_be_na_and_keys_cannot_repeat(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_p.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    na = _proof("test_a").replace(
        'logical=tests/test_p.py›"test_a"', "logical=n/a:no logic here at all"
    )
    assert nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{na}\t\n")).status == "FAIL"
    dup = _proof("test_a") + '; logical=tests/test_p.py›"test_a"'
    assert nc.gate_proofs(ctx(tmp_path, f"H\tW0.1\tgreen\t{dup}\t\n")).status == "FAIL"


def test_changed_tests_fails_on_unknown_base(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "a",
        ],
        cwd=tmp_path,
        check=True,
    )
    d = tmp_path / "specs" / "W0.1-x"
    d.mkdir(parents=True)
    (d / ".base").write_text("deadbeef\n", encoding="utf-8")
    r = nc.gate_changed_tests(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "deadbeef" in r.detail


def test_honesty_gate_fails_on_unlabelled_published_number(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "published.txt").write_text("page.md\n", encoding="utf-8")
    (tmp_path / "page.md").write_text("Cost per 1,000 alerts: $13.10\n", encoding="utf-8")
    r = nc.gate_honesty(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "$13.10" in r.detail
    (tmp_path / "page.md").write_text(
        "Cost per 1,000 alerts: $13.10 (simulated data)\n", encoding="utf-8"
    )
    assert nc.gate_honesty(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n")).status == "PASS"


def test_honesty_gate_fails_without_a_listing_and_skips_on_an_empty_one(tmp_path):
    r = nc.gate_honesty(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "published.txt" in r.detail
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "published.txt").write_text("# none yet\n", encoding="utf-8")
    assert nc.gate_honesty(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n")).status == "SKIP"


def test_changed_tests_fails_when_git_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "changed_files", lambda root, base: None)
    monkeypatch.setattr(nc.shutil, "which", lambda name: None)
    r = nc.gate_changed_tests(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "git not found" in r.detail


def _proof_all(path, test):
    dims = [f'{d}={path}›"{test}"' for d in ("logical", "contract", "golden")]
    return "; ".join([*dims, "repro=n/a:nothing to reproduce", "safety=n/a:no secrets or spend"])


def test_proofs_reject_uncollected_files_xfail_and_in_body_skips(tmp_path):
    t = tmp_path / "tests"
    t.mkdir()
    body = (
        "import pytest\n\n"
        "def test_ok():\n    pass\n\n"
        "@pytest.mark.xfail\ndef test_xf():\n    pass\n\n"
        "def test_inbody():\n    pytest.skip('later')\n"
    )
    (t / "helpers.py").write_text(body, encoding="utf-8")
    (t / "test_a.py").write_text(body, encoding="utf-8")

    def cap(proof):
        return parse_catalog(HDR + f"H\tW0.1\tgreen\t{proof}\t\n")[0]

    assert nc.proof_problems(cap(_proof_all("tests/test_a.py", "test_ok")), tmp_path) == []
    assert (
        "test_*.py"
        in nc.proof_problems(cap(_proof_all("tests/helpers.py", "test_ok")), tmp_path)[0]
    )
    assert "xfail" in nc.proof_problems(cap(_proof_all("tests/test_a.py", "test_xf")), tmp_path)[0]
    assert (
        "skipped"
        in nc.proof_problems(cap(_proof_all("tests/test_a.py", "test_inbody")), tmp_path)[0]
    )


def test_run_smoke_names_the_failing_command(tmp_path):
    exe = nc._venv_bin(tmp_path / "v", "fraud")
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho nope >&2\nexit 2\n", encoding="utf-8")
    exe.chmod(0o755)
    r = nc.run_smoke(tmp_path / "v", [["fraud", "doctor", "--offline"]], tmp_path)
    assert r is not None and "fraud doctor --offline" in r.detail and "nope" in r.detail


REAL = "\n\ndef test_real():\n    pass\n"
SNEAKY = {  # verifier pass 2: proofs a static check accepted though pytest never passed them
    "module pytestmark skip": "import pytest\npytestmark = pytest.mark.skip" + REAL,
    "module pytestmark xfail list": "import pytest\npytestmark = [pytest.mark.xfail]" + REAL,
    "class-level skip": (
        "import pytest\n\n@pytest.mark.skip\nclass TestA:\n    def test_real(self):\n        pass\n"
    ),
    "module importorskip": "import pytest\npytest.importorskip('no_such_module_xyz')" + REAL,
    "in-body importorskip": (
        "import pytest\n\ndef test_real():\n    pytest.importorskip('no_such_module_xyz')\n"
    ),
    "module-level skip": "import pytest\npytest.skip('later', allow_module_level=True)" + REAL,
    "aliased skip mark": "import pytest\nSKIP = pytest.mark.skip\n\n@SKIP" + REAL[1:],
    "__test__ = False": REAL.lstrip() + "\ntest_real.__test__ = False\n",
    "non-strict xfail module mark": (
        "import pytest\npytestmark = pytest.mark.xfail(strict=False, reason='x')" + REAL
    ),
    "aliased non-strict xfail": (
        "import pytest\nloose = pytest.mark.xfail(strict=False)\n\n@loose" + REAL[1:]
    ),
    "applymarker xfail": (
        "import pytest\n\ndef test_real(request):\n"
        "    request.applymarker(pytest.mark.xfail(strict=False))\n"
    ),
    "class with __init__": (
        "class TestB:\n    def __init__(self):\n        pass\n\n"
        "    def test_real(self):\n        pass\n"
    ),
}


@pytest.mark.parametrize("label", sorted(SNEAKY))
def test_proofs_must_actually_pass_under_pytest(tmp_path, label):
    t = tmp_path / "tests"
    t.mkdir()
    (t / "test_s.py").write_text(SNEAKY[label], encoding="utf-8")
    cls = "TestA::" if "class-level" in label else "TestB::" if "__init__" in label else ""
    problems = nc.run_proofs({f"tests/test_s.py::{cls}test_real": "H logical"}, tmp_path)
    assert problems and problems[0].startswith("H logical:"), (label, problems)


def test_run_proofs_accepts_a_real_passing_test(tmp_path):
    t = tmp_path / "tests"
    t.mkdir()
    (t / "test_ok.py").write_text(
        "import pytest\n\n@pytest.mark.parametrize('x', [1, 2])\ndef test_real(x):\n    assert x\n",
        encoding="utf-8",
    )
    assert nc.run_proofs({"tests/test_ok.py::test_real": "H logical"}, tmp_path) == []


def test_all_skipped_is_skip_for_golden_and_fail_for_safety(tmp_path):
    (tmp_path / "test_g.py").write_text(
        "import pytest\n\n@pytest.mark.golden\n@pytest.mark.skip\ndef test_g():\n    pass\n\n"
        "@pytest.mark.safety\n@pytest.mark.skip\ndef test_s():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    golden\n    safety\n", encoding="utf-8"
    )
    assert nc._pytest(tmp_path, "golden", "golden").status == "SKIP"
    r = nc.gate_safety(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "all skipped" in r.detail


def _git(repo, *args):
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_changed_tests_fails_on_a_broken_ref_instead_of_passing(tmp_path):
    import subprocess

    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    d = repo / "specs" / "W0.1-x"
    d.mkdir(parents=True)
    (d / ".base").write_text(sha, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "new.py").write_text("X = 1\n", encoding="utf-8")
    c = nc.Ctx("W0.1", repo, GRAPH, parse_catalog(HDR), repo / "specs")
    r = nc.gate_changed_tests(c)
    assert r.status == "FAIL" and "src/new.py" in r.detail
    (repo / ".git" / "refs" / "heads" / "main").write_text("zzzz\n", encoding="utf-8")
    (d / ".base").write_text("zzzz", encoding="utf-8")
    assert nc.gate_changed_tests(c).status == "FAIL"


def test_changed_tests_fails_when_the_repository_is_unreadable(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /nonexistent/path\n", encoding="utf-8")
    r = nc.gate_changed_tests(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "cannot read" in r.detail


def test_safety_gate_fails_with_no_safety_tests(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    r = nc.gate_safety(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "none collected" in r.detail


def test_printed_result_lines_cannot_spoof_a_proof(tmp_path):
    t = tmp_path / "tests"
    t.mkdir()
    (t / "test_spoof.py").write_text(
        "import pytest\n\n"
        "def test_a():\n    print('PASSED tests/test_spoof.py::test_b')\n\n"
        "def test_b():\n    getattr(pytest, 'sk' + 'ip')('x')\n",
        encoding="utf-8",
    )
    problems = nc.run_proofs(
        {"tests/test_spoof.py::test_a": "H contract", "tests/test_spoof.py::test_b": "H logical"},
        tmp_path,
    )
    assert problems == ["H logical: tests/test_spoof.py::test_b skipped"]


def test_a_proof_the_suite_ignores_fails(tmp_path):
    t = tmp_path / "tests"
    t.mkdir()
    (t / "test_hidden.py").write_text("def test_real():\n    pass\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "collect_ignore = ['tests/test_hidden.py']\n", encoding="utf-8"
    )
    problems = nc.run_proofs({"tests/test_hidden.py::test_real": "H logical"}, tmp_path)
    assert problems and "not collected by the suite" in problems[0]


def test_non_ascii_source_names_are_not_missed(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "é.py").write_text("X = 1\n", encoding="utf-8")
    files = nc.changed_files(repo, "none")
    assert files is not None and "src/é.py" in files
    assert nc.changed_tests_problem(files) is not None


def test_recorded_base_without_a_repository_fails(tmp_path):
    import pytest as _pt

    with _pt.raises(nc.HarnessError, match="no repository"):
        nc.changed_files(tmp_path, "0123456789abcdef")


def test_a_summary_printed_at_exit_cannot_pass_the_safety_gate(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    safety\n", encoding="utf-8")
    (tmp_path / "test_s.py").write_text(
        "import atexit\nimport pytest\n\n"
        "atexit.register(lambda: print('1 passed in 0.01s'))\n\n"
        "@pytest.mark.safety\ndef test_s():\n    pytest.skip('x')\n",
        encoding="utf-8",
    )
    r = nc.gate_safety(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "all skipped" in r.detail


def test_golden_min_python_leg_is_judged_by_outcomes(tmp_path, monkeypatch):
    calls = []

    def fake_outcomes(root, args, python=None, extra_env=None):
        calls.append(python)
        if python is None:
            return 0, "", {"tests/test_g.py::test_g": {"passed"}}
        return 0, "1 passed", {"tests/test_g.py::test_g": {"skipped"}}  # printed text lies

    monkeypatch.setattr(nc, "pytest_outcomes", fake_outcomes)
    monkeypatch.setattr(nc, "MIN_PYTHON", "3.0")
    monkeypatch.setattr(nc.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.delenv("FRAUD_SKIP_MIN_PYTHON", raising=False)
    r = nc.gate_golden(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "nothing passed" in r.detail
    assert calls[1] is not None and calls[1][0] == "uv"
    monkeypatch.setattr(nc.shutil, "which", lambda name: None)
    r = nc.gate_golden(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "needs uv" in r.detail


def test_a_local_outcome_plugin_file_cannot_shadow_the_harness_plugin(tmp_path):
    (tmp_path / "outcome_plugin.py").write_text(
        "def pytest_sessionfinish(session):\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "test_x.py").write_text(
        "import pytest\n\ndef test_x():\n    pytest.skip('x')\n", encoding="utf-8"
    )
    _code, _out, outcomes = nc.pytest_outcomes(tmp_path, [])
    assert outcomes == {"test_x.py::test_x": {"skipped"}}


def test_outcomes_from_an_unexpected_plugin_are_rejected(tmp_path):
    fake = tmp_path / "shadow"
    fake.mkdir()
    (fake / "outcome_plugin.py").write_text(
        "import json, os\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    open(os.environ['FRAUD_OUTCOMES'], 'w').write(json.dumps({'t::x': ['passed']}))\n",
        encoding="utf-8",
    )
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert False\n", encoding="utf-8")
    nc_scripts = nc.SCRIPTS_DIR
    try:
        nc.SCRIPTS_DIR = fake  # the harness now points at a plugin that is not its own file
        _code, out, outcomes = nc.pytest_outcomes(tmp_path, [])
    finally:
        nc.SCRIPTS_DIR = nc_scripts
    assert outcomes is None and "unexpected plugin" in out


def test_secret_scan_fails_when_git_cannot_list_a_repository(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
    r = nc.gate_safety(ctx(tmp_path, "H\tW0.1\tbuilding\t\t\n"))
    assert r.status == "FAIL" and "could not list" in r.detail
