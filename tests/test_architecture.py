"""B3.3: the reference architecture diagram (ARC-01..03). The SVG keeps every label as real text,
its legend separates built, supported and proposed parts, nothing is too small to read, and the
PNG export for the site exists."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "docs" / "assets" / "architecture.svg"
PNG = ROOT / "docs" / "assets" / "architecture.png"
NS = "{http://www.w3.org/2000/svg}"
MIN_PX = 11.0

LEGEND = ("Built (ran)", "Supported (not run)", "Proposed (pilot)")
BUILT = (
    "Local Python CLI",
    "Claude API",
    "Haiku 5.5",
    "Sonnet 5.5",
    "Router and policy-as-code",
    "Hash-chained audit log",
    "Content-addressed cache",
    "Spend cap and ledger",
    "LightGBM baseline",
)
PROPOSED = (
    "IAM boundary",
    "Amazon S3",
    "Amazon EventBridge",
    "AWS Step Functions",
    "Amazon DynamoDB",
    "Object Lock",
    "Amazon CloudWatch",
    "AWS KMS",
    "Analyst queue app",
    "Hosted demo: Lambda",
    "Amazon Bedrock (Mantle)",
)
FONT_PX = re.compile(r"font(?:-size)?\s*:[^;}]*?(\d+(?:\.\d+)?)px")
FONT_OTHER_UNIT = re.compile(r"font(?:-size)?\s*:[^;}]*?\d(?:\.\d+)?(em|rem|pt|%)")


def _root() -> ET.Element:
    return ET.fromstring(SVG.read_text(encoding="utf-8"))


def _texts(root: ET.Element) -> list[str]:
    """Each <text> element's full string, tspans included, with whitespace collapsed."""
    return [" ".join("".join(t.itertext()).split()) for t in root.iter(f"{NS}text")]


def _joined() -> str:
    return "\n".join(_texts(_root()))


@pytest.mark.contract
def test_legend_has_three_categories() -> None:
    texts = _joined()
    for label in LEGEND:
        assert label in texts, f"legend category missing: {label}"


@pytest.mark.contract
@pytest.mark.parametrize("name", BUILT)
def test_built_components_named_as_text(name: str) -> None:
    assert name in _joined()


@pytest.mark.contract
@pytest.mark.parametrize("name", PROPOSED)
def test_aws_services_and_iam_boundary_named(name: str) -> None:
    assert name in _joined()


@pytest.mark.contract
def test_only_project_region() -> None:
    """The owner's AWS project allows us-east-2 only: no other Region is named."""
    regions = set(re.findall(r"\b[a-z]{2}-[a-z]+-\d\b", _joined()))
    assert regions == {"us-east-2"}


@pytest.mark.contract
def test_no_font_below_minimum() -> None:
    root = _root()
    sizes: list[float] = []
    for el in root.iter():
        attr = el.get("font-size")
        if attr is not None:
            sizes.append(float(attr.removesuffix("px")))
        sizes += [float(s) for s in FONT_PX.findall(el.get("style", ""))]
        if el.tag == f"{NS}style":
            sizes += [float(s) for s in FONT_PX.findall(el.text or "")]
    assert sizes, "no font sizes found"
    assert min(sizes) >= MIN_PX, f"font-size {min(sizes)}px is below {MIN_PX}px"
    text = SVG.read_text(encoding="utf-8")
    assert not FONT_OTHER_UNIT.findall(text), "font sizes must be in px so they can be checked"


@pytest.mark.contract
def test_png_export_exists() -> None:
    assert PNG.is_file()
    data = PNG.read_bytes()
    assert len(data) > 0
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


# ------------------------------------------------------------------ box kinds and the boundary

KIND = {"out": "built", "agent": "supported", "eval": "proposed"}
BOX_KIND = {
    "Local Python CLI": "built",
    "LightGBM baseline": "built",
    "Router and policy-as-code": "built",
    "Content-addressed cache": "built",
    "Spend cap and ledger": "built",
    "Hash-chained audit log": "built",
    "Claude API (Anthropic)": "built",
    "Amazon Bedrock (Mantle)": "supported",
    "Hosted demo: Lambda": "proposed",
    "Amazon S3": "proposed",
    "Amazon EventBridge": "proposed",
    "AWS Step Functions": "proposed",
    "Amazon S3 with Object Lock": "proposed",
    "Analyst queue app": "proposed",
    "Amazon CloudWatch": "proposed",
    "AWS KMS": "proposed",
}
INSIDE_BOUNDARY = (
    "Amazon S3",
    "Amazon EventBridge",
    "AWS Step Functions",
    "Amazon S3 with Object Lock",
)


def _rects(root: ET.Element) -> list[tuple[set[str], float, float, float, float]]:
    out = []
    for r in root.iter(f"{NS}rect"):
        cls = set((r.get("class") or "").split())
        if not cls:
            continue
        x, y = float(r.get("x", 0)), float(r.get("y", 0))
        out.append((cls, x, y, x + float(r.get("width", 0)), y + float(r.get("height", 0))))
    return out


def _headings(root: ET.Element) -> dict[str, tuple[float, float]]:
    found: dict[str, tuple[float, float]] = {}
    for t in root.iter(f"{NS}text"):
        if "h" in (t.get("class") or "").split():
            found.setdefault(
                " ".join("".join(t.itertext()).split()),
                (float(t.get("x", 0)), float(t.get("y", 0))),
            )
    return found


def _box_of(point: tuple[float, float], rects: list[Any]) -> set[str]:
    x, y = point
    hits = [r for r in rects if "box" in r[0] and r[1] <= x <= r[3] and r[2] <= y <= r[4]]
    assert len(hits) == 1, f"heading at {point} is in {len(hits)} boxes"
    return hits[0][0]


@pytest.mark.contract
def test_each_box_is_drawn_as_its_legend_kind() -> None:
    root = _root()
    rects, heads = _rects(root), _headings(root)
    for title, kind in BOX_KIND.items():
        assert title in heads, f"no box titled {title!r}"
        cls = _box_of(heads[title], rects)
        drawn = {KIND[c] for c in cls if c in KIND}
        assert drawn == {kind}, f"{title!r} is drawn as {drawn}, expected {kind}"


@pytest.mark.contract
def test_legend_lines_put_each_part_under_its_kind() -> None:
    lines = {t.split(":", 1)[0]: t for t in _texts(_root()) if t.split(":", 1)[0] in LEGEND}
    assert set(lines) == set(LEGEND)
    built, supported, proposed = (lines[k] for k in LEGEND)
    for name in ("Claude API", "LightGBM baseline", "local Python CLI"):
        assert name in built and name not in supported + proposed
    assert "Bedrock" in supported and "Bedrock" not in built + proposed
    assert "hosted demo" in proposed and "hosted demo" not in built + supported


@pytest.mark.contract
def test_the_iam_boundary_is_drawn_around_the_pilot_workflow() -> None:
    root = _root()
    rects, heads = _rects(root), _headings(root)
    gates = [r for r in rects if "gate" in r[0]]
    assert len(gates) == 1, "the IAM boundary is drawn once"
    _, gx0, gy0, gx1, gy1 = gates[0]
    for title in INSIDE_BOUNDARY:
        x, y = heads[title]
        assert gx0 <= x <= gx1 and gy0 <= y <= gy1, f"{title!r} is outside the boundary"
    x, y = heads["Claude API (Anthropic)"]
    assert not (gx0 <= x <= gx1 and gy0 <= y <= gy1)


@pytest.mark.contract
def test_no_other_region_or_cross_region_wording_anywhere() -> None:
    text = SVG.read_text(encoding="utf-8")  # title, desc and labels alike
    regions = set(re.findall(r"\b[a-z]{2}-[a-z]+-\d\b", text))
    assert regions == {"us-east-2"}
    for phrase in (
        "global inference",
        "global profile",
        "cross-Region inference",
        "us. ",
        "us-east-1",
    ):
        assert phrase not in text


@pytest.mark.contract
def test_png_is_the_full_size_export_and_not_blank() -> None:
    import struct
    import zlib

    data = PNG.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 50_000
    assert struct.unpack(">II", data[16:24]) == (1750, 1175)
    idat = b"".join(
        data[i + 8 : i + 8 + struct.unpack(">I", data[i : i + 4])[0]]
        for i in range(8, len(data) - 12)
        if data[i + 4 : i + 8] == b"IDAT"
    )
    raw = zlib.decompress(idat)
    assert len(set(raw[:200_000])) > 16  # many distinct byte values: not a flat image
