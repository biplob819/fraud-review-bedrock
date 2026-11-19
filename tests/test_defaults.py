"""Shipped defaults: the policy, config and prices files are found with or without a clone."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from fraudreview import cli
from fraudreview.defaults import packaged

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["policy.md", "config.yaml", "prices.yaml"])
def test_the_default_files_exist(name):
    assert packaged(name).is_file()


def test_the_wheel_carries_the_default_files():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in ("policy.md", "config.yaml", "prices.yaml"):
        assert f'"{name}" = "fraudreview/_defaults/{name}"' in text


def test_an_untouched_default_path_falls_back_to_the_packaged_copy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a folder with none of the files
    args = argparse.Namespace(config=Path("config.yaml"), prices=Path("prices.yaml"))
    cli._use_packaged_defaults(args)
    assert args.config == packaged("config.yaml") and args.prices == packaged("prices.yaml")


def test_a_path_the_user_chose_is_never_replaced(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(config=Path("mine.yaml"), prices=Path("prices.yaml"))
    cli._use_packaged_defaults(args)
    assert args.config == Path("mine.yaml")
