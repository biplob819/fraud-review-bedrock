"""B3.3: the reference architecture diagram (ARC-01..03). The SVG keeps every label as real text,
its legend separates built, supported and proposed parts, nothing is too small to read, and the
PNG export for the site exists."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

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


@pytest.mark.contract
def test_png_export_exists() -> None:
    assert PNG.is_file()
    data = PNG.read_bytes()
    assert len(data) > 0
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
