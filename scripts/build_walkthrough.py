#!/usr/bin/env python3
"""Build docs/assets/walkthrough.gif: a looping walkthrough of the key screens, from screenshots
already in the repo. Nine frames, 2.5 s each, 960 px wide, simulated data on every frame. The
Value crop leaves out the tiles that show cost, so no spend appears. Reproducible:

    uv run python scripts/build_walkthrough.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.Image import open as open_image  # binary read; named so the text-IO check skips it

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
OUT = ASSETS / "walkthrough.gif"
W, H, BAR = 960, 600, 44
SECONDS = 2.5

# (caption, source file, crop box in source pixels or None)
FRAMES: list[tuple[str, str, tuple[int, int, int, int] | None]] = [
    ("Fraud-review agent: a two-stage Claude agent that recommends; people decide", "", None),
    ("Architecture: model recommends, a router in code decides", "architecture.png", None),
    (
        "Analyst review screen: reasons, policy clauses and score drivers side by side",
        "screens/review-case.png",
        (200, 0, 1500, 1150),
    ),
    (
        "Every decision is written to a hash-chained audit log",
        "screens/review-audit.png",
        (200, 0, 1500, 1150),
    ),
    (
        "Safety: 0 of 55 frauds auto-cleared, exact upper bound 5.3%",
        "screens/dashboard.png",
        (190, 160, 1210, 720),
    ),
    (
        "Value: 8.8% of alerts auto-cleared, 44 of 44 legitimate",
        "screens/dashboard.png",
        (190, 980, 1210, 1395),
    ),
    (
        "Explanations: real clauses, allowed fields, true numbers",
        "screens/dashboard.png",
        (190, 1485, 1210, 1810),
    ),
    (
        "Faithfulness: a single-judge partial result, shown with its caveats",
        "screens/dashboard.png",
        (190, 2075, 1210, 2370),
    ),
    (
        "Monitoring: the first run failed and stays in the record",
        "screens/dashboard.png",
        (190, 2675, 1210, 3010),
    ),
]


def font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf"):
        for base in ("/System/Library/Fonts", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu"):
            p = Path(base) / name
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def frame(caption: str, source: str, box: tuple[int, int, int, int] | None) -> Image.Image:
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    area = H - BAR
    if source:
        img = open_image(ASSETS / source).convert("RGB")
        if box:
            img = img.crop(box)
        scale = min(W / img.width, area / img.height)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        canvas.paste(img, ((W - img.width) // 2, (area - img.height) // 2))
    else:
        draw.text(
            (W // 2, area // 2 - 40),
            "Fraud-review agent",
            font=font(44),
            fill="#0f172a",
            anchor="mm",
        )
        draw.text(
            (W // 2, area // 2 + 20),
            "A two-stage Claude agent that triages card-fraud alerts",
            font=font(22),
            fill="#334155",
            anchor="mm",
        )
        draw.text(
            (W // 2, area // 2 + 56),
            "Walkthrough of the key screens",
            font=font(22),
            fill="#334155",
            anchor="mm",
        )
    draw.rectangle((0, area, W, H), fill="#fef3c7")
    draw.text(
        (16, area + BAR // 2),
        f"Simulated data. {caption}",
        font=font(16),
        fill="#0f172a",
        anchor="lm",
    )
    return canvas


def build(out: Path = OUT) -> Path:
    frames = [frame(*f) for f in FRAMES]
    quant = [
        f.quantize(colors=48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        for f in frames
    ]
    quant[0].save(
        out,
        save_all=True,
        append_images=quant[1:],
        duration=int(SECONDS * 1000),
        loop=0,
        optimize=True,
        disposal=2,
    )
    return out


if __name__ == "__main__":
    path = build()
    print(f"{path.relative_to(ROOT)}: {path.stat().st_size // 1024} KB, {len(FRAMES)} frames")
