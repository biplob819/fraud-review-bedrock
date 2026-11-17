"""E2.3 DSH-02..05: the dashboard, its one number formatter, and the Pages workflow. No network."""

from __future__ import annotations

import re
import socket
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from fraudreview import monitor, report, summary
from fraudreview.cli import main
from fraudreview.report import FMT, FormatError, Num
from fraudreview.schemas import Rate

ROOT = Path(__file__).resolve().parent.parent
MEAS = ROOT / "docs" / "measurements"
SUMMARY = MEAS / "summary-eval2.json"


@pytest.fixture(scope="module")
def page() -> str:
    s = summary.read_summary(SUMMARY)
    return report.render(s, monitor.read_jsonl(MEAS / "runs.jsonl"))


class Text(HTMLParser):
    """Visible text nodes, and whether each sits inside a formatter-marked element."""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[tuple[str, bool]] = []
        self.urls: list[str] = []
        self._stack: list[bool] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.urls += [v for v in a.values() if v and re.match(r"\s*(https?:)?//", v)]
        void = tag in {"meta", "line", "rect", "circle", "polyline", "br", "link"}
        self._hidden += tag in {"style", "script"}
        if not void:
            self._stack.append("data-fmt" in a)

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self._hidden -= 1
        if self._stack and tag not in {"meta", "line", "rect", "circle", "polyline"}:
            self._stack.pop()

    def handle_data(self, data):
        if data.strip() and not self._hidden:
            self.nodes.append((data, any(self._stack)))


def parsed(html: str) -> Text:
    p = Text()
    p.feed(html)
    return p


def test_report_has_every_panel_and_the_banner(page):
    for pid in ("safety", "value", "explanations", "monitoring", "facts"):
        assert f'<section id="{pid}">' in page
    assert "Simulated data. The agent recommends; people decide." in page
    assert page.count("on simulated data") >= 5  # one per panel


def test_safety_shows_leakage_k_of_n_with_its_bound_and_the_baseline(page):
    assert "0 of 55" in page and "5.3%" in page
    assert "Baseline recall" in page and "Leakage target" in page and "Met" in page


def test_faithfulness_notes_sit_beside_the_number(page):
    start = page.index('<div class="faith">')
    block = page[start : page.index("Mismatch examples", start)]
    assert "48.0%" in block and "Partial, single judge" in block
    assert "Read the number with these notes" in block and "second judge" in block
    assert block.index("48.0%") < block.index("second judge")


def test_a_faithfulness_figure_is_refused_without_its_notes():
    s = summary.read_summary(SUMMARY)
    e = s.explanation.model_copy(update={"faithfulness_detail": None})
    with pytest.raises(report.ReportError):
        report.render(s.model_copy(update={"explanation": e}))


def test_mismatch_examples_show_hashes_not_ids(page):
    s = summary.read_summary(SUMMARY)
    for alert_id in s.explanation.mismatch_examples:
        assert alert_id not in page
        assert FMT.ref(alert_id) in page


def test_formatter_refuses_a_rate_without_n():
    with pytest.raises(TypeError):
        FMT.pct(0.5)  # type: ignore[call-arg]
    for bad in (None, -1, 2.5, True):
        with pytest.raises(FormatError):
            FMT.pct(0.5, n=bad)  # type: ignore[arg-type]
    with pytest.raises(FormatError):
        FMT.pct(1.5, n=10)
    assert FMT.pct(0.0883, n=500) == "8.8%"
    assert FMT.rate(Rate(k=44, n=500, point=0.088, lo=0.066, hi=0.116)) == "8.8%"
    assert FMT.pct(0.0, n=0) == "no data (n zero)"


def test_page_builders_refuse_a_digit_that_skipped_the_formatter():
    with pytest.raises(FormatError):
        report.text("5.3%")
    with pytest.raises(FormatError):
        report.j("rate ", "12")
    assert report.j("rate ", Num("12", "number")).endswith("</span>")


def test_no_number_reaches_the_page_except_through_the_formatter(page):
    p = parsed(page)
    stray = [t for t, marked in p.nodes if not marked and re.search(r"\d", t)]
    assert stray == []
    assert any(marked for _, marked in p.nodes)


def test_every_number_in_the_page_is_labelled_simulated_by_the_honesty_gate(page, tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import honesty_check

    ok = tmp_path / "ok.html"
    ok.write_text(page, encoding="utf-8")
    assert honesty_check.check([ok], tmp_path) == []
    bad = tmp_path / "bad.html"
    bad.write_text(page.replace("on simulated data", "on data"), encoding="utf-8")
    assert honesty_check.check([bad], tmp_path)


def test_html_has_no_external_url_script_or_link(page):
    assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}", page)
    assert parsed(page).urls == []
    assert "<script" not in page and "<link" not in page and "<img" not in page
    assert "@import" not in page and "url(" not in page


def test_example_renders_offline_with_no_network(monkeypatch, tmp_path):
    def refuse(*a, **k):
        raise AssertionError("the example opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    out = tmp_path / "example.html"
    assert main(["report", "--example", "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "Fraud review: Example" in html
    assert re.search(r"golden example: <span[^>]*>20</span> alerts", html)
    assert "<section" in html and "Simulated data." in html


def test_report_cli_renders_a_summary_file(tmp_path):
    out = tmp_path / "r.html"
    code = main(
        [
            "report",
            "--summary",
            str(SUMMARY),
            "--history",
            str(MEAS / "runs.jsonl"),
            "--out",
            str(out),
        ]
    )
    assert code == 0 and "Safety" in out.read_text(encoding="utf-8")
    assert main(["report"]) == 2


def test_committed_dashboard_is_what_the_committed_summary_renders(page):
    committed = (ROOT / "docs" / "dashboard.html").read_text(encoding="utf-8")
    assert committed == page, (
        "regenerate with fraud report (see pages.yml) into docs/dashboard.html"
    )
    assert "docs/dashboard.html" in (ROOT / "docs" / "published.txt").read_text(encoding="utf-8")


# ------------------------------------------------------------------ DSH-04 workflow


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    )


def triggers(wf: dict) -> dict:
    return wf.get("on") or wf[True]  # YAML reads a bare `on` as True


def test_workflow_runs_on_main_pushes_only_never_on_pull_requests(workflow):
    on = triggers(workflow)
    assert on["push"]["branches"] == ["main"]
    assert not {"pull_request", "pull_request_target"} & set(on)


def test_workflow_permissions_are_least_privilege(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert "permissions" not in workflow["jobs"]["build"]  # inherits read-only contents


def test_workflow_actions_are_pinned_and_the_official_pages_ones(workflow):
    uses = [s["uses"] for job in workflow["jobs"].values() for s in job["steps"] if "uses" in s]
    assert any(u.startswith("actions/upload-pages-artifact@") for u in uses)
    assert any(u.startswith("actions/deploy-pages@") for u in uses)
    for u in uses:
        ref = u.split("@", 1)[1]
        assert re.fullmatch(r"v\d+(\.\d+){0,2}|[0-9a-f]{40}", ref), u


def test_workflow_builds_from_the_committed_summary_with_fraud_report(workflow):
    runs = " ".join(str(s.get("run", "")) for s in workflow["jobs"]["build"]["steps"])
    assert "fraud report" in runs and "docs/measurements/summary-eval2.json" in runs
