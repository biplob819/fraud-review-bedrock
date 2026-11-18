"""The walkthrough GIF stands in for a video: nine real screens, small, looping, labelled."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import ImageSequence
from PIL.Image import open as open_image  # binary read

ROOT = Path(__file__).resolve().parent.parent
GIF = ROOT / "docs" / "assets" / "walkthrough.gif"
SHOTS = (ROOT / "docs" / "walkthrough.md").read_text(encoding="utf-8")


def test_the_gif_is_nine_looping_frames_960_wide_and_small() -> None:
    assert GIF.stat().st_size < 600 * 1024
    with open_image(GIF) as g:
        assert g.size[0] == 960 and g.info.get("loop") == 0
        frames = [f.info.get("duration") for f in ImageSequence.Iterator(g)]
    assert len(frames) == 9 and all(d == 2500 for d in frames)


def test_the_shot_list_names_every_screen_and_its_source_exists() -> None:
    rows = re.findall(r"^\| (\d) \| .+ \| (.+) \|$", SHOTS, flags=re.M)
    assert [r[0] for r in rows] == [str(i) for i in range(1, 10)]
    for _, source in rows:
        for path in re.findall(r"`([^`]+\.png)`", source):
            assert (ROOT / path).exists(), path


def test_the_page_says_simulated_and_no_spend_and_links_the_gif() -> None:
    assert "Simulated data" in SHOTS and "no spend" in SHOTS
    assert "assets/walkthrough.gif" in (ROOT / "docs" / "project.md").read_text(encoding="utf-8")


def test_the_cost_tiles_are_not_in_the_value_crop() -> None:
    script = (ROOT / "scripts" / "build_walkthrough.py").read_text(encoding="utf-8")
    assert "(190, 980, 1210, 1395)" in script  # starts below the stat tiles that show cost
    with open_image(ROOT / "docs" / "assets" / "screens" / "dashboard.png") as shot:
        tiles_bottom = 955  # the cost tile ends here in the 1400 px dashboard screenshot
        assert shot.size[1] > tiles_bottom and tiles_bottom < 980
