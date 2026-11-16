from pathlib import Path

import pytest

from fraudreview.config import ConfigError, RunConfig, load_config

ROOT = Path(__file__).resolve().parent.parent
GOOD = (ROOT / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.contract
def test_committed_config_loads():
    cfg = load_config(ROOT / "config.yaml")
    assert isinstance(cfg, RunConfig)
    assert cfg.stage1.effort == "medium" and cfg.stage2.effort == "high"
    assert any(t.metric == "leakage" and t.op == "==" and t.value == 0 for t in cfg.thresholds)


@pytest.mark.parametrize(
    "mutate, field",
    [
        (lambda t: t.replace("effort: high", "effort: extreme"), "stage2.effort"),
        (
            lambda t: t.replace(
                "region: us-east-2           # D-34: the project's own Region, "
                "in-Region endpoint\n",
                "",
            ),
            "region",
        ),
        (lambda t: t + "surprise: 1\n", "surprise"),
        (lambda t: t.replace("max_cost_usd: 5 ", "max_cost_usd: 0"), "max_cost_usd"),
        (lambda t: t.replace("provider: bedrock", "provider: openai"), "provider"),
        (lambda t: t.replace("metric: leakage", "metric: recall"), "thresholds.0.metric"),
    ],
)
def test_bad_config_names_file_and_field(tmp_path, mutate, field):
    p = tmp_path / "c.yaml"
    p.write_text(mutate(GOOD), encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert str(p) in str(exc.value) and field in str(exc.value)


def test_unreadable_or_non_mapping(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(p)
    p.write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(p)


@pytest.mark.parametrize(
    "raw",
    [b"\xff\xfe\x00\x01\x89PNG\r\n", GOOD.encode("utf-16")],
    ids=["binary", "utf-16"],
)
def test_non_utf8_config_is_a_config_error(tmp_path, raw):
    p = tmp_path / "c.yaml"
    p.write_bytes(raw)
    with pytest.raises(ConfigError, match="not UTF-8"):
        load_config(p)


@pytest.mark.parametrize(
    "mutate, field",
    [
        (lambda t: t + "max_cost_usd: 99\n", "duplicate"),
        (lambda t: t.replace("max_cost_usd: 5 ", "max_cost_usd: '5' "), "max_cost_usd"),
        (lambda t: t.replace("concurrency: 8", "concurrency: '8'"), "concurrency"),
        (lambda t: t.replace("seed: 20261115", "seed: true"), "seed"),
    ],
)
def test_no_duplicates_and_no_coercion(tmp_path, mutate, field):
    p = tmp_path / "c.yaml"
    p.write_text(mutate(GOOD), encoding="utf-8")
    with pytest.raises(ConfigError, match=field):
        load_config(p)


@pytest.mark.safety
@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace('op: "=="', 'op: ">="'),
        lambda t: t.replace("    value: 0", "    value: 1"),
        lambda t: t + '  - metric: leakage\n    op: "<="\n    value: 1\n',
        lambda t: t.replace("clear_ceiling: 0.9", "clear_ceiling: 1.0"),
        lambda t: t.replace("clear_min_confidence: 0.7", "clear_min_confidence: 0"),
        lambda t: t.replace("max_cost_usd: 5 ", "max_cost_usd: 1000000000000"),
    ],
    ids=["leakage-op", "leakage-value", "second-leakage-row", "ceiling-1", "confidence-0", "cap"],
)
def test_safety_settings_cannot_be_switched_off(tmp_path, mutate):
    p = tmp_path / "c.yaml"
    p.write_text(mutate(GOOD), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


@pytest.mark.parametrize(
    "mutate, field",
    [
        (lambda t: t.replace("region: us-east-2", "region: 2026-01-01"), "region"),
        (lambda t: t.replace("seed: 20261115", "seed: 2026-11-15"), "seed"),
        (lambda t: t.replace("region: us-east-2", "region: !!binary aGVsbG8="), "region"),
        (lambda t: t.replace("region: us-east-2", "region: !!set {a, b}"), "region"),
        (
            lambda t: t.replace("clear_min_confidence: 0.7", "clear_min_confidence: 0.3"),
            "clear_min_confidence",
        ),
        (lambda t: t.replace("clear_ceiling: 0.9", "clear_ceiling: 0.9999999"), "clear_ceiling"),
        (lambda t: t.replace("seed: 20261115", "seed: -1"), "seed"),
        (lambda t: t.replace("metric: leakage", "metric: Leakage"), "thresholds"),
    ],
)
def test_odd_values_name_the_field(tmp_path, mutate, field):
    p = tmp_path / "c.yaml"
    p.write_text(mutate(GOOD), encoding="utf-8")
    with pytest.raises(ConfigError, match=field):
        load_config(p)


@pytest.mark.parametrize(
    "mutate, field",
    [
        (lambda t: t.replace("seed: 20261115", "seed: 0x1F"), "seed"),
        (lambda t: t.replace("seed: 20261115", "seed: 1:30"), "seed"),
        (lambda t: t.replace("seed: 20261115", "seed: 20_261_115"), "seed"),
        (lambda t: t.replace("concurrency: 8 ", "concurrency: 010 "), "concurrency"),
    ],
)
def test_numbers_follow_json_not_yaml_1_1(tmp_path, mutate, field):
    p = tmp_path / "c.yaml"
    p.write_text(mutate(GOOD), encoding="utf-8")
    with pytest.raises(ConfigError, match=field):
        load_config(p)


def test_mapping_cycle_names_the_field(tmp_path):
    text = GOOD.replace("stage1:\n", "stage1: &s\n  loop: *s\n", 1)
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"stage1\.loop"):
        load_config(p)


def test_deep_nesting_is_a_config_error(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(GOOD + "deep: " + "[" * 5000 + "]" * 5000 + "\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)
