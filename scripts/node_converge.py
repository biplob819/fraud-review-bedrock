#!/usr/bin/env python3
"""Run every gate for a build node and report all of them, even after a failure.

Usage:
    python scripts/node_converge.py W0.1          # run all 12 gates
    python scripts/node_converge.py --self-test   # prove every gate can fail

Exit 0 only when no gate FAILs. SKIP means the gate had nothing to check yet
(for example no golden tests exist before node A2.5, and no published artifact
before E2.3); SKIP never hides a failure.

Gates:
   1 claims-work     at least one capability row of the node is building or green
   2 eligibility     graph has no cycles and nothing builds ahead of its dependencies
   3 proofs          every row has a proof for all five dimensions, and each proof resolves
   4 ruff            uv run ruff check .
   5 pyright         uv run pyright
   6 pytest          unit, property and contract tests
   7 golden          pytest -m golden
   8 honesty         every number in a published artifact carries the simulated label
   9 safety          pytest -m safety, plus the secret scan
  10 build           uv build, install the wheel in a clean venv, run the smoke commands
  11 changed-tests   if source changed since node start, tests changed too
  12 records         changelog entry, criteria cover every row, RUN-STATE.md exists
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import honesty_check
import secret_scan
from harness_lib import (
    PROOF_DIMENSIONS,
    ROOT,
    SPECS_DIR,
    Cap,
    HarnessError,
    Node,
    eligibility_problems,
    load_catalog,
    load_graph,
    parse_catalog,
    parse_graph,
    spec_dir,
)
from node_start import cap_mentioned

# Commands run inside the clean venv by gate 10. W0.2 adds `doctor --offline`; E2.3 adds
# `report --example`.
SMOKE_COMMANDS: list[list[str]] = [["fraud", "--version"]]
PYTEST_UNIT_MARKERS = "not golden and not safety"
NA_MIN_REASON = 10
NA_FORBIDDEN = {"logical"}  # every capability needs at least a real unit test


@dataclass
class Result:
    status: str  # PASS | FAIL | SKIP
    detail: str


@dataclass
class Ctx:
    node: str
    root: Path
    nodes: dict[str, Node]
    caps: list[Cap]
    specs: Path


def _run(cmd: list[str], cwd: Path, timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return p.returncode, (p.stdout + p.stderr).strip()


def _tail(text: str, n: int = 12) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


# ---------- gates ----------


def gate_claims_work(ctx: Ctx) -> Result:
    rows = [c for c in ctx.caps if c.node == ctx.node]
    active = [c for c in rows if c.status in ("building", "green")]
    if not rows:
        return Result("FAIL", f"{ctx.node} has no catalog rows")
    if not active:
        return Result(
            "FAIL", f"no row of {ctx.node} is building or green (run node_start --mark-building)"
        )
    return Result("PASS", f"{len(active)}/{len(rows)} rows active")


def gate_eligibility(ctx: Ctx) -> Result:
    problems = eligibility_problems(ctx.nodes, ctx.caps)
    return Result("FAIL", "; ".join(problems)) if problems else Result("PASS", "eligible")


PROOF_RE = re.compile(r'^(?P<path>[^›"]+)›"(?P<test>[^"]+)"$')


def _test_functions(path: Path) -> dict[str, bool]:
    return {k: v[0] for k, v in _test_index(path).items()}


def _test_index(path: Path) -> dict[str, tuple[bool, str]]:
    """Map each test name to (statically skipped, pytest node-id suffix)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, tuple[bool, str]] = {}

    def skipped(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        marks = " ".join(ast.unparse(d) for d in fn.decorator_list)
        body_skips = any(
            isinstance(n, ast.Call)
            and ast.unparse(n.func) in ("pytest.skip", "pytest.xfail", "skip", "xfail")
            for n in ast.walk(fn)
        )
        return "skip" in marks or "xfail" in marks or body_skips

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            found[node.name] = (skipped(node), node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(
                    item, ast.FunctionDef | ast.AsyncFunctionDef
                ) and item.name.startswith("test"):
                    found[item.name] = (skipped(item), f"{node.name}::{item.name}")
    return found


def proof_problems(cap: Cap, root: Path, nodeids: dict[str, str] | None = None) -> list[str]:
    """Static checks; when `nodeids` is given, also record each resolved proof as
    pytest node id -> "CAP dim" so the caller can run them (D-29)."""
    if not cap.proof:
        return [f"{cap.cap_id}: no proof"]
    entries: dict[str, str] = {}
    for part in cap.proof.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            return [f"{cap.cap_id}: malformed proof entry {part!r}"]
        key, value = (s.strip() for s in part.split("=", 1))
        if key in entries:
            return [f"{cap.cap_id}: duplicate proof key {key!r}"]
        entries[key] = value
    problems: list[str] = []
    tests_root = (root / "tests").resolve()
    for dim in PROOF_DIMENSIONS:
        value = entries.get(dim)
        if value is None:
            problems.append(f"{cap.cap_id}: missing {dim} proof")
            continue
        if value.startswith("n/a:"):
            if dim in NA_FORBIDDEN:
                problems.append(f"{cap.cap_id}: {dim} proof cannot be n/a")
            elif len(value[4:].strip()) < NA_MIN_REASON:
                problems.append(f"{cap.cap_id}: {dim} n/a needs a reason of {NA_MIN_REASON}+ chars")
            continue
        m = PROOF_RE.match(value)
        if not m:
            problems.append(f'{cap.cap_id}: {dim} proof must be path›"test" or n/a:<reason>')
            continue
        rel, test = m["path"].strip(), m["test"].strip()
        path = (root / rel).resolve()
        if (
            not path.is_relative_to(tests_root)
            or path.suffix != ".py"
            or not path.name.startswith("test_")
        ):
            problems.append(
                f"{cap.cap_id}: {dim} proof file {rel} must be a test_*.py file under tests/"
            )
            continue
        if not path.is_file():
            problems.append(f"{cap.cap_id}: {dim} proof file {rel} not found")
            continue
        try:
            tests = _test_functions(path)
        except SyntaxError as exc:
            problems.append(f"{cap.cap_id}: {dim} proof file {rel} does not parse: {exc.msg}")
            continue
        if test not in tests:
            problems.append(f"{cap.cap_id}: {dim} test {test!r} is not a test function in {rel}")
        elif tests[test]:
            problems.append(f"{cap.cap_id}: {dim} test {test!r} is skipped or xfail")
        elif nodeids is not None:
            nodeids.setdefault(f"{rel}::{_test_index(path)[test][1]}", f"{cap.cap_id} {dim}")
    extra = set(entries) - set(PROOF_DIMENSIONS)
    if extra:
        problems.append(f"{cap.cap_id}: unknown proof keys {sorted(extra)}")
    return problems


SCRIPTS_DIR = Path(__file__).resolve().parent


def pytest_outcomes(
    root: Path,
    args: list[str],
    python: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, set[str]] | None]:
    """Run pytest in `root` with the outcome plugin; return (exit code, output, outcomes per
    node id). Outcomes come from the plugin's JSON, never from printed text (L-23, L-24)."""
    import json

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "outcomes.json"
        env = {
            **os.environ,
            "FRAUD_OUTCOMES": str(report),
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(SCRIPTS_DIR), os.environ.get("PYTHONPATH", "")) if p
            ),
            **(extra_env or {}),
        }
        # -P: the working directory is not put first on sys.path, so a file there named
        # outcome_plugin.py cannot shadow the harness plugin
        cmd = [*(python or [sys.executable]), "-P", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        cmd += ["-p", "outcome_plugin", *args]
        try:
            proc = subprocess.run(
                cmd, cwd=root, env=env, capture_output=True, text=True, timeout=900
            )
        except subprocess.TimeoutExpired:
            return 124, "pytest timed out", None
        out = (proc.stdout + proc.stderr).strip()
        if not report.is_file():
            return proc.returncode, out, None
        raw = json.loads(report.read_text(encoding="utf-8"))
    plugin = raw.pop("<plugin>", [""])[0]
    if Path(plugin).resolve() != (SCRIPTS_DIR / "outcome_plugin.py").resolve():
        return proc.returncode, f"outcomes were written by an unexpected plugin: {plugin!r}", None
    return proc.returncode, out, {nid: set(o) for nid, o in raw.items()}


def run_proofs(nodeids: dict[str, str], root: Path) -> list[str]:
    """Run the suite the way CI does (normal collection, every marker) and check each proof
    test's recorded outcome: every parametrized case passed, none skipped, xfail or xpass.
    A proof the suite does not collect fails (D-29)."""
    if not nodeids:
        return []
    _code, out, outcomes = pytest_outcomes(root, [])
    if outcomes is None:
        return [f"pytest could not run the proofs: {_tail(out, 4)}"]
    by_base: dict[str, set[str]] = {}
    for nid, o in outcomes.items():
        by_base.setdefault(nid.split("[", 1)[0], set()).update(o)
    problems = []
    for nid, who in nodeids.items():
        got = by_base.get(nid, set())
        if got != {"passed"}:
            problems.append(
                f"{who}: {nid} {', '.join(sorted(got)) or 'not collected by the suite'}"
            )
    return problems


def gate_proofs(ctx: Ctx) -> Result:
    rows = [c for c in ctx.caps if c.node == ctx.node]
    nodeids: dict[str, str] = {}
    problems = [p for c in rows for p in proof_problems(c, ctx.root, nodeids)]
    if not problems:
        problems = run_proofs(nodeids, ctx.root)
    if problems:
        shown = "; ".join(problems[:6]) + (
            f"; … {len(problems) - 6} more" if len(problems) > 6 else ""
        )
        return Result("FAIL", shown)
    return Result(
        "PASS",
        f"{len(rows)} rows × {len(PROOF_DIMENSIONS)} dimensions resolve; "
        f"{len(nodeids)} proof tests ran and passed",
    )


def _tool_gate(cmd: list[str], root: Path, label: str) -> Result:
    code, out = _run(cmd, root)
    if code == 0:
        return Result("PASS", label)
    return Result("FAIL", _tail(out))


def gate_ruff(ctx: Ctx) -> Result:
    return _tool_gate([sys.executable, "-m", "ruff", "check", "."], ctx.root, "ruff clean")


def gate_pyright(ctx: Ctx) -> Result:
    return _tool_gate([sys.executable, "-m", "pyright"], ctx.root, "pyright clean")


def _pytest(root: Path, marker: str, label: str) -> Result:
    return _judge(*pytest_outcomes(root, ["-m", marker]), label)


def gate_pytest(ctx: Ctx) -> Result:
    return _pytest(ctx.root, PYTEST_UNIT_MARKERS, "unit")


MIN_PYTHON = "3.12"  # requires-python floor; float and stdlib behaviour can differ (EvalKit L-27)


def _judge(code: int, out: str, outcomes: dict[str, set[str]] | None, label: str) -> Result:
    if code == 5:
        return Result("SKIP", f"no {label} tests collected yet")
    if outcomes is None or code not in (0, 1):
        return Result("FAIL", _tail(out))
    bad = sorted(n for n, o in outcomes.items() if o - {"passed", "skipped", "xfail"})
    if code == 1 or bad:
        return Result("FAIL", _tail(out) if not bad else "failing: " + ", ".join(bad[:5]))
    passed = sum(1 for o in outcomes.values() if o == {"passed"})
    others = len(outcomes) - passed
    if passed == 0:
        return Result("SKIP", f"{label}: nothing passed ({others} skipped or xfail)")
    return Result("PASS", f"{label}: {passed} passed, {others} skipped or xfail")


def gate_golden(ctx: Ctx) -> Result:
    """Golden runs compare bytes, so run them on this interpreter and, when this interpreter
    is newer, on the minimum supported Python too (a separate .venv-py<ver>). Both legs are
    judged by the outcome plugin; a missing uv is a FAIL when the second leg is needed."""
    here = _pytest(ctx.root, "golden", "golden")
    on_min = f"{sys.version_info.major}.{sys.version_info.minor}" == MIN_PYTHON
    if here.status != "PASS" or on_min or os.environ.get("FRAUD_SKIP_MIN_PYTHON"):
        return here
    if not shutil.which("uv"):
        return Result("FAIL", f"golden on Python {MIN_PYTHON} needs uv, which was not found")
    leg = _judge(
        *pytest_outcomes(
            ctx.root,
            ["-m", "golden"],
            python=["uv", "run", "--python", MIN_PYTHON, "--all-extras", "python"],
            extra_env={
                "UV_PROJECT_ENVIRONMENT": str(ctx.root / f".venv-py{MIN_PYTHON.replace('.', '')}")
            },
        ),
        f"golden on Python {MIN_PYTHON}",
    )
    if leg.status != "PASS":
        return Result("FAIL", leg.detail)
    return Result("PASS", f"{here.detail}; also on Python {MIN_PYTHON}")


def gate_honesty(ctx: Ctx) -> Result:
    try:
        files = honesty_check.published_files(ctx.root)
    except honesty_check.ListingError as exc:
        return Result("FAIL", str(exc))
    if not files:
        return Result("SKIP", "docs/published.txt lists no artifacts yet")
    problems = honesty_check.check(files, ctx.root)
    if problems:
        shown = "; ".join(problems[:5]) + (
            f"; … {len(problems) - 5} more" if len(problems) > 5 else ""
        )
        return Result("FAIL", shown)
    return Result("PASS", f"{len(files)} published files: every number labelled")


def gate_safety(ctx: Ctx) -> Result:
    try:
        hits = secret_scan.scan(secret_scan.candidate_files(ctx.root))
    except RuntimeError as exc:
        return Result("FAIL", f"secret scan: {exc}")
    tests = _pytest(ctx.root, "safety", "safety")
    if hits:
        return Result("FAIL", "secret scan: " + "; ".join(hits[:5]))
    if tests.status == "FAIL":
        return tests
    if tests.status == "SKIP":  # zero passes is never PASS (L-21)
        reason = "all skipped" if "nothing passed" in tests.detail else "none collected"
        return Result("FAIL", f"safety tests {reason}: {tests.detail}")
    return Result("PASS", f"secret scan clean; {tests.detail}")


def _venv_bin(venv: Path, exe: str) -> Path:
    sub = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv / sub / f"{exe}{suffix}"


def run_smoke(venv: Path, smoke: list[list[str]], cwd: Path) -> Result | None:
    """Run each smoke command from the venv; the first failure is returned, naming it."""
    for cmd in smoke:
        exe = _venv_bin(venv, cmd[0])
        code, log = _run([str(exe), *cmd[1:]], cwd)
        if code != 0:
            return Result("FAIL", f"smoke `{' '.join(cmd)}` exited {code}: {_tail(log)}")
    return None


def gate_build(ctx: Ctx, smoke: list[list[str]] | None = None) -> Result:
    smoke = SMOKE_COMMANDS if smoke is None else smoke
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dist"
        code, log = _run(["uv", "build", "--wheel", "--out-dir", str(out)], ctx.root)
        if code != 0:
            return Result("FAIL", "uv build: " + _tail(log))
        wheels = sorted(out.glob("*.whl"))
        if not wheels:
            return Result("FAIL", "uv build produced no wheel")
        venv = Path(tmp) / "venv"
        code, log = _run(["uv", "venv", "-q", str(venv)], ctx.root)
        if code != 0:
            return Result("FAIL", "uv venv: " + _tail(log))
        py = _venv_bin(venv, "python")
        code, log = _run(
            ["uv", "pip", "install", "-q", "--python", str(py), str(wheels[0])], ctx.root
        )
        if code != 0:
            return Result("FAIL", "install wheel: " + _tail(log))
        failed = run_smoke(venv, smoke, Path(tmp))
        if failed:
            return failed
        return Result(
            "PASS", f"{wheels[0].name} installs; smoke: {', '.join(' '.join(c) for c in smoke)}"
        )


def changed_files(root: Path, base: str) -> list[str] | None:
    """Files changed since base: committed, staged, unstaged and untracked.

    Returns None when git is unusable.
    """
    code, out = _run(["git", "rev-parse", "--git-dir"], root)
    if code != 0:
        if (root / ".git").exists():  # a repository git cannot read is a tool error
            raise HarnessError(f"git cannot read the repository: {_tail(out, 2)}")
        if base not in ("", "none"):  # a recorded base proves a repository existed
            raise HarnessError(f"base {base[:10]} is recorded but git sees no repository")
        return None
    files: set[str] = set()
    if base not in ("", "none"):
        # a recorded base must resolve; a broken HEAD or ref is an error, never "no commits"
        code, out = _run(["git", "-c", "core.quotePath=false", "diff", "--name-only", base], root)
        if code != 0:
            raise HarnessError(f"git diff against base {base!r} failed: {_tail(out, 2)}")
        files.update(out.splitlines())
    else:
        code, out = _run(["git", "-c", "core.quotePath=false", "ls-files"], root)
        if code != 0:
            raise HarnessError(f"git ls-files failed: {_tail(out, 2)}")
        files.update(out.splitlines())
    code, out = _run(
        ["git", "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard"], root
    )
    if code != 0:
        raise HarnessError(f"git ls-files --others failed: {_tail(out, 2)}")
    files.update(out.splitlines())
    return sorted(f for f in files if f)


def changed_tests_problem(files: list[str]) -> str | None:
    src = [
        f for f in files if f.startswith("src/") or (f.startswith("scripts/") and f.endswith(".py"))
    ]
    tests = [f for f in files if f.startswith("tests/") and not f.endswith("__init__.py")]
    if src and not tests:
        return f"{len(src)} source files changed but no tests changed (e.g. {src[0]})"
    return None


def gate_changed_tests(ctx: Ctx) -> Result:
    d = spec_dir(ctx.node, ctx.specs)
    base_file = d / ".base" if d else None
    base = (
        base_file.read_text(encoding="utf-8").strip()
        if base_file and base_file.is_file()
        else "none"
    )
    try:
        files = changed_files(ctx.root, base)
    except HarnessError as exc:
        return Result("FAIL", str(exc))
    if files is None:
        if shutil.which("git") is None:
            return Result("FAIL", "git not found on PATH (a tool error is a FAIL, L-3)")
        return Result("SKIP", "not a git repository")
    problem = changed_tests_problem(files)
    if problem:
        return Result("FAIL", problem)
    return Result(
        "PASS", f"{len(files)} files changed since {base[:10]}; tests changed with source"
    )


def records_problems(node: str, caps: list[Cap], root: Path, specs: Path) -> list[str]:
    problems: list[str] = []
    changelog = root / "changelog.md"
    if not changelog.is_file() or f"[{node}]" not in changelog.read_text(encoding="utf-8"):
        problems.append(f"changelog.md has no [{node}] entry")
    d = spec_dir(node, specs)
    if d is None:
        return [*problems, f"no spec folder for {node}"]
    criteria = d / "criteria.md"
    text = criteria.read_text(encoding="utf-8") if criteria.is_file() else ""
    missing = [c.cap_id for c in caps if c.node == node and not cap_mentioned(c.cap_id, text)]
    if missing:
        problems.append(f"criteria.md misses {', '.join(missing)}")
    if not (d / "RUN-STATE.md").is_file():
        problems.append("RUN-STATE.md missing")
    return problems


def gate_records(ctx: Ctx) -> Result:
    problems = records_problems(ctx.node, ctx.caps, ctx.root, ctx.specs)
    return Result("FAIL", "; ".join(problems)) if problems else Result("PASS", "records complete")


GATES: list[tuple[str, Callable[[Ctx], Result]]] = [
    ("claims-work", gate_claims_work),
    ("eligibility", gate_eligibility),
    ("proofs", gate_proofs),
    ("ruff", gate_ruff),
    ("pyright", gate_pyright),
    ("pytest", gate_pytest),
    ("golden", gate_golden),
    ("honesty", gate_honesty),
    ("safety", gate_safety),
    ("build", gate_build),
    ("changed-tests", gate_changed_tests),
    ("records", gate_records),
]


# ---------- self-test: every gate must be shown to fail on bad input ----------


def self_test() -> int:
    """Feed each gate a planted defect and require FAIL that names the defect."""
    failures = 0

    def check(name: str, ok: bool) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'ok' if ok else 'WRONG'}] {name}")

    graph = parse_graph("| **W0.1** | Harness | — |\n| **N1.1** | Schemas | W0.1 |\n")
    hdr = "cap_id\tnode\tstatus\tproof\tnote\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        specs = root / "specs"
        (specs / "W0.1-harness").mkdir(parents=True)

        def ctx(cat: str, node: str = "W0.1", at: Path = root) -> Ctx:
            return Ctx(node, at, graph, parse_catalog(hdr + cat), specs)

        # 1 claims-work
        r = gate_claims_work(ctx("H-1\tW0.1\tspec\t\t\n"))
        check(
            "1 claims-work fails naming the node",
            r.status == "FAIL" and "no row of W0.1" in r.detail,
        )
        r = gate_claims_work(ctx("H-1\tW0.1\tbuilding\t\t\n"))
        check("1 claims-work passes when building", r.status == "PASS")

        # 2 eligibility
        r = gate_eligibility(ctx("H-1\tW0.1\tbuilding\t\t\nS-1\tN1.1\tbuilding\t\t\n", "N1.1"))
        check("2 eligibility fails when building ahead", r.status == "FAIL" and "W0.1" in r.detail)

        # 3 proofs
        tests_dir = root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text(
            "import pytest\n\n"
            "# test_in_comment\n"
            "NAME = 'test_in_string'\n\n"
            "def test_real():\n    pass\n\n"
            "def test_realistic():\n    pass\n\n"
            "@pytest.mark.skip\ndef test_skipped():\n    pass\n",
            encoding="utf-8",
        )
        (root / "outside.py").write_text("def test_real():\n    pass\n", encoding="utf-8")
        ok = "; ".join(
            [f'{d}=tests/test_x.py›"test_real"' for d in PROOF_DIMENSIONS[:3]]
            + ["repro=n/a:no state to reproduce here", "safety=n/a:no secrets or spend involved"]
        )
        r = gate_proofs(ctx(f"H-1\tW0.1\tgreen\t{ok}\t\n"))
        check("3 proofs pass on resolvable proof", r.status == "PASS")
        bad = {  # label: (proof, text the failure must contain)
            "empty proof": "",
            "missing dimension": 'logical=tests/test_x.py›"test_real"',
            "unknown test": ok.replace('"test_real"', '"test_ghost"', 1),
            "name only in a comment": ok.replace('"test_real"', '"test_in_comment"', 1),
            "name only in a string": ok.replace('"test_real"', '"test_in_string"', 1),
            "prefix of another test": ok.replace('"test_real"', '"test_rea"', 1),
            "skipped test": ok.replace('"test_real"', '"test_skipped"', 1),
            "path outside tests/": ok.replace("tests/test_x.py", "outside.py", 1),
            "path escaping repo": ok.replace("tests/test_x.py", "tests/../../x.py", 1),
            "short n/a reason": ok.replace("n/a:no state to reproduce here", "n/a:x"),
            "logical n/a": ok.replace(
                'logical=tests/test_x.py›"test_real"', "logical=n/a:nothing to unit test here"
            ),
            "duplicate key": ok + '; logical=tests/test_x.py›"test_real"',
        }
        must_name = {
            "empty proof": "no proof",
            "missing dimension": "missing contract proof",
            "unknown test": "test_ghost",
            "name only in a comment": "test_in_comment",
            "name only in a string": "test_in_string",
            "prefix of another test": "test_rea",
            "skipped test": "skipped",
            "path outside tests/": "outside.py",
            "path escaping repo": "under tests/",
            "short n/a reason": "needs a reason",
            "logical n/a": "cannot be n/a",
            "duplicate key": "duplicate proof key",
        }
        for label, proof in bad.items():
            r = gate_proofs(ctx(f"H-1\tW0.1\tgreen\t{proof}\t\n"))
            check(
                f"3 proofs fail on {label} naming it",
                r.status == "FAIL" and must_name[label] in r.detail,
            )

        (tests_dir / "test_sneaky.py").write_text(
            "import pytest\npytestmark = pytest.mark.skip\n\ndef test_real():\n    pass\n",
            encoding="utf-8",
        )
        sneaky = ok.replace("tests/test_x.py", "tests/test_sneaky.py")
        r = gate_proofs(ctx(f"H-1\tW0.1\tgreen\t{sneaky}\t\n"))
        check(
            "3 proofs fail on a module-level skip that only pytest can see",
            r.status == "FAIL" and "test_sneaky.py" in r.detail,
        )

        # 4 ruff — must name the planted rule, so a missing tool cannot pass for a caught defect
        lint = root / "lint"
        lint.mkdir()
        (lint / "bad.py").write_text("import os\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "ruff", "check", "--isolated", "--select", "F", "."]
        r = _tool_gate(cmd, lint, "")
        check("4 ruff fails naming F401", r.status == "FAIL" and "F401" in r.detail)

        # 5 pyright
        types = root / "types"
        types.mkdir()
        (types / "bad.py").write_text('x: int = "a"\n', encoding="utf-8")
        (types / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        r = _tool_gate([sys.executable, "-m", "pyright"], types, "")
        check("5 pyright fails naming bad.py", r.status == "FAIL" and "bad.py" in r.detail)

        # 6-9 pytest-backed gates, each on its own failing fixture
        pt = root / "pt"
        pt.mkdir()
        (pt / "test_fail.py").write_text(
            "import pytest\n\n"
            "def test_unit_fails():\n    assert False\n\n"
            "@pytest.mark.golden\ndef test_golden_fails():\n    assert False\n\n"
            "@pytest.mark.safety\ndef test_safety_fails():\n    assert False\n",
            encoding="utf-8",
        )
        r = _pytest(pt, PYTEST_UNIT_MARKERS, "unit")
        check(
            "6 pytest fails naming the failing test",
            r.status == "FAIL" and "test_unit_fails" in r.detail,
        )
        r = _pytest(pt, "golden", "golden")
        check(
            "7 golden fails naming the failing test",
            r.status == "FAIL" and "test_golden_fails" in r.detail,
        )
        hon = root / "hon"
        (hon / "docs").mkdir(parents=True)
        (hon / "docs" / "published.txt").write_text("page.md\n", encoding="utf-8")
        (hon / "page.md").write_text("Auto-clear rate 41.2%.\n", encoding="utf-8")
        r = gate_honesty(ctx("H-1\tW0.1\tbuilding\t\t\n", at=hon))
        check(
            "8 honesty fails naming the unlabelled number",
            r.status == "FAIL" and "41.2%" in r.detail,
        )
        (hon / "page.md").write_text("Auto-clear rate 41.2% on simulated data.\n", encoding="utf-8")
        r = gate_honesty(ctx("H-1\tW0.1\tbuilding\t\t\n", at=hon))
        check("8 honesty passes when labelled", r.status == "PASS")
        r = gate_honesty(ctx("H-1\tW0.1\tbuilding\t\t\n", at=pt))
        check(
            "8 honesty fails naming a missing listing",
            r.status == "FAIL" and "published.txt" in r.detail,
        )
        (hon / "docs" / "published.txt").write_text("# nothing yet\n", encoding="utf-8")
        r = gate_honesty(ctx("H-1\tW0.1\tbuilding\t\t\n", at=hon))
        check("8 honesty SKIPs on an empty listing", r.status == "SKIP")
        r = gate_safety(ctx("H-1\tW0.1\tbuilding\t\t\n", at=pt))
        check(
            "9 safety fails on a failing safety test",
            r.status == "FAIL" and "test_safety_fails" in r.detail,
        )
        empty = root / "empty"
        empty.mkdir()
        (empty / "test_none.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
        check(
            "7 golden reports SKIP, not PASS, with no golden tests",
            _pytest(empty, "golden", "g").status == "SKIP",
        )
        leak = root / "leak"
        leak.mkdir()
        (leak / "k.py").write_text('K = "sk-ant-' + "Q" * 30 + '"\n', encoding="utf-8")
        r = gate_safety(ctx("H-1\tW0.1\tbuilding\t\t\n", at=leak))
        check("9 safety fails on a leaked key", r.status == "FAIL" and "anthropic key" in r.detail)

        # 10 build
        broken = root / "broken"
        broken.mkdir()
        (broken / "pyproject.toml").write_text("[project\nname=", encoding="utf-8")
        has_uv = shutil.which("uv") is not None
        check("10 uv is installed (the build gate cannot run without it)", has_uv)
        r = gate_build(Ctx("W0.1", broken, graph, [], specs), smoke=[])
        check(
            "10 build fails naming the broken pyproject.toml",
            has_uv and r.status == "FAIL" and "pyproject.toml" in r.detail,
        )
        fake_venv = root / "fakevenv"
        bin_dir = _venv_bin(fake_venv, "fraud").parent
        bin_dir.mkdir(parents=True)
        exe = _venv_bin(fake_venv, "fraud")
        exe.write_text("#!/bin/sh\necho smoke-broke >&2\nexit 3\n", encoding="utf-8")
        exe.chmod(0o755)
        sm = run_smoke(fake_venv, [["fraud", "--version"]], root)
        check(
            "10 a failing smoke command fails naming it",
            sm is not None and "fraud --version" in sm.detail and "smoke-broke" in sm.detail,
        )

        # 11 changed-tests
        check(
            "11 fails when only source changed",
            changed_tests_problem(["src/fraudreview/x.py"]) is not None,
        )
        check(
            "11 an __init__.py does not count as a test change",
            changed_tests_problem(["src/fraudreview/x.py", "tests/__init__.py"]) is not None,
        )
        check(
            "11 passes when tests changed",
            changed_tests_problem(["src/fraudreview/x.py", "tests/test_x.py"]) is None,
        )
        repo = root / "repo"
        repo.mkdir()
        for c in (
            ["git", "init", "-q"],
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
        ):
            _run(c, repo)
        try:
            changed_files(repo, "deadbeef")
            check("11 an unknown base commit fails", False)
        except HarnessError:
            check("11 an unknown base commit fails", True)
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("X = 1\n", encoding="utf-8")
        rspecs = repo / "specs"
        (rspecs / "W0.1-harness").mkdir(parents=True)
        r = gate_changed_tests(Ctx("W0.1", repo, graph, [], rspecs))
        check(
            "11 the gate fails naming the untested source file",
            r.status == "FAIL" and "src/mod.py" in r.detail,
        )

        # 12 records
        cat = parse_catalog(hdr + "H-1\tW0.1\tgreen\t\t\n")
        rp = records_problems("W0.1", cat, root, specs)
        check(
            "12 records fails naming the missing changelog entry",
            any("changelog.md" in p for p in rp),
        )
        (specs / "W0.1-harness" / "criteria.md").write_text("H-10\n", encoding="utf-8")
        rp = records_problems("W0.1", cat, root, specs)
        check("12 records: H-10 does not cover H-1", any("misses H-1" in p for p in rp))
        (root / "changelog.md").write_text("[FEAT] [W0.1] — harness\n", encoding="utf-8")
        (specs / "W0.1-harness" / "criteria.md").write_text("H-1\n", encoding="utf-8")
        (specs / "W0.1-harness" / "RUN-STATE.md").write_text("ok\n", encoding="utf-8")
        check("12 records passes when complete", records_problems("W0.1", cat, root, specs) == [])
    print("self-test", "PASSED" if failures == 0 else f"FAILED ({failures})")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("node", nargs="?")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-log", action="store_true", help="do not write a log under .harness/")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.node:
        ap.error("node is required")
    try:
        ctx = Ctx(args.node, ROOT, load_graph(), load_catalog(), SPECS_DIR)
    except (HarnessError, OSError) as exc:
        print(f"node-converge: cannot read inputs: {exc}")
        return 1
    if args.node not in ctx.nodes:
        print(f"node-converge: unknown node {args.node}")
        return 1
    lines = [f"node-converge {args.node} — {dt.datetime.now().isoformat(timespec='seconds')}"]
    failed = 0
    for i, (name, fn) in enumerate(GATES, start=1):
        try:
            r = fn(ctx)
        except Exception as exc:  # a crashing gate is a failing gate
            r = Result("FAIL", f"gate crashed: {exc!r}")
        failed += r.status == "FAIL"
        first, *rest = r.detail.splitlines() or [""]
        lines.append(f"{i:>2} {name:<14} {r.status:<4}  {first}")
        lines.extend(f"{'':>22}{ln}" for ln in rest)
    lines.append(
        f"RESULT: {'FAIL' if failed else 'PASS'} "
        f"({failed} failing gate{'s' if failed != 1 else ''})"
    )
    report = "\n".join(lines)
    print(report)
    if args.no_log:
        return 1 if failed else 0
    log_dir = ROOT / ".harness" / args.node
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    (log_dir / f"converge-{stamp}.txt").write_text(report + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
