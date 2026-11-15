import pytest

import honesty_check as hc


def test_self_test_shows_the_check_can_fail():
    assert hc.self_test() == 0


@pytest.mark.parametrize("name", sorted(hc.SELF_TEST_CASES))
def test_metric_and_label_rules(tmp_path, name):
    text, should_fail = hc.SELF_TEST_CASES[name]
    page = tmp_path / "p.md"
    page.write_text(text + "\n", encoding="utf-8")
    assert bool(hc.check([page], tmp_path)) is should_fail


def test_unlabelled_metric_is_reported_on_its_own_line(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("# Results\n\nIntro line\nAuto-clear rate 38%, cost $6.40.\n", encoding="utf-8")
    assert hc.check([page], tmp_path) == [
        "p.md:4: 38% has no simulated label",
        "p.md:4: $6.40 has no simulated label",
    ]


def test_listing_ignores_comments_and_blank_lines(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "published.txt").write_text(
        "# artifacts\n\nREADME.md  # the repo front page\nnotes#1.md\n", encoding="utf-8"
    )
    assert hc.published_files(tmp_path) == [tmp_path / "README.md", tmp_path / "notes#1.md"]


@pytest.mark.parametrize("entry", ["../../etc/hosts", "/etc/hosts"])
def test_listing_rejects_paths_outside_the_repo(tmp_path, entry):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "published.txt").write_text(entry + "\n", encoding="utf-8")
    with pytest.raises(hc.ListingError, match="outside the repo"):
        hc.published_files(tmp_path)


def test_missing_listing_is_an_error(tmp_path):
    with pytest.raises(hc.ListingError, match="missing"):
        hc.published_files(tmp_path)


def test_listed_but_missing_file_fails(tmp_path):
    assert hc.check([tmp_path / "gone.md"], tmp_path) == ["gone.md: listed but missing"]


def test_cli_passes_on_the_live_repo():
    assert hc.main([]) == 0
