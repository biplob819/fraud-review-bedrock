"""Run configuration (Tech Spec §3, Config). Loaded from YAML; errors name the file and field.

The YAML is validated in strict JSON mode: numbers and booleans are not coerced from strings,
duplicate keys are refused, and the safety settings cannot be switched off from config: the
leakage bar must be exactly `leakage == 0` (OWNER D-10), the clear ceiling is at most 0.99 and the
minimum confidence at least 0.5 (router rules 5 and 6), and the spend cap is at most $100.
Numbers follow JSON: YAML 1.1 forms such as `0x1F` or `1:30` are not numbers here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import Field, ValidationError, model_validator

from fraudreview.schemas import Effort, Model, Provider, Text, ThresholdResult

MAX_COST_CAP_USD = 100.0  # no single run may be configured to spend more (owner rule: ask > $25)


class ConfigError(Exception):
    """A config file is missing, unreadable or invalid. The message names file and field."""


class StageConfig(Model):
    model: Text  # Bedrock model or inference profile id, verified in W0.2
    effort: Effort
    max_output_tokens: Annotated[int, Field(ge=256, le=128_000)]


class RunConfig(Model):
    provider: Provider  # D-27: bedrock (default, published results) or anthropic
    region: Annotated[str, Field(pattern=r"^[a-z]{2}(-[a-z]+)+-[0-9]$")]
    stage1: StageConfig
    stage2: StageConfig
    # router rules 5 and 6 cannot be configured into no-ops
    clear_ceiling: Annotated[float, Field(ge=0.05, le=0.99, allow_inf_nan=False)]
    clear_min_confidence: Annotated[float, Field(ge=0.5, le=1.0, allow_inf_nan=False)]
    concurrency: Annotated[int, Field(ge=1, le=64)]
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)]
    max_cost_usd: Annotated[float, Field(gt=0.0, le=MAX_COST_CAP_USD, allow_inf_nan=False)]
    thresholds: Annotated[tuple[ThresholdResult, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _check(self) -> RunConfig:
        leakage = [t for t in self.thresholds if t.metric == "leakage"]
        if len(leakage) != 1 or leakage[0].op != "==" or leakage[0].value != 0:
            raise ValueError(
                "thresholds must hold exactly one leakage row: leakage == 0 (OWNER D-10)"
            )
        if len({t.metric for t in self.thresholds}) != len(self.thresholds):
            raise ValueError("each threshold metric may appear once")
        if any(t.passed is not None for t in self.thresholds):
            raise ValueError("config thresholds must not carry a result")
        return self


class StrictLoader(yaml.SafeLoader):
    """A safe loader that refuses duplicate keys instead of letting the last one win, and reads
    YAML timestamps as plain strings (JSON has no date type)."""


_DROPPED = {"tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:int", "tag:yaml.org,2002:float"}
StrictLoader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag not in _DROPPED]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
# JSON-style numbers only (no hex, octal, sexagesimal or underscores)
StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int", re.compile(r"^-?(?:0|[1-9][0-9]*)$"), list("-0123456789")
)
StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$|^\.(?:nan|NaN|NAN)$"
        r"|^[-+]?\.(?:inf|Inf|INF)$"
    ),
    list("-0123456789."),
)


def _cycle_path(node: yaml.Node, path: str = "", seen: frozenset[int] = frozenset()) -> str | None:
    """Dotted path of the first node that contains itself (a YAML alias cycle)."""
    if id(node) in seen:
        return path or "(root)"
    inner = seen | {id(node)}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            found = _cycle_path(value, f"{path}.{key.value}".lstrip("."), inner)
            if found:
                return found
    elif isinstance(node, yaml.SequenceNode):
        for i, value in enumerate(node.value):
            found = _cycle_path(value, f"{path}.{i}".lstrip("."), inner)
            if found:
                return found
    return None


def _first_non_json(value: Any, path: str = "") -> str | None:
    """Dotted path of the first value JSON cannot express (bytes, sets, dates, cycles)."""
    stack: list[tuple[Any, str, frozenset[int]]] = [(value, path, frozenset())]
    while stack:
        item, where, seen = stack.pop()
        if isinstance(item, dict):
            if id(item) in seen:
                return where or "(root)"
            for k, v in item.items():
                if not isinstance(k, str):
                    return f"{where}.{k}".lstrip(".")
                stack.append((v, f"{where}.{k}".lstrip("."), seen | {id(item)}))
        elif isinstance(item, list):
            if id(item) in seen:
                return where or "(root)"
            for i, v in enumerate(item):
                stack.append((v, f"{where}.{i}".lstrip("."), seen | {id(item)}))
        elif not (item is None or isinstance(item, bool | int | float | str)):
            return where or "(root)"
    return None


def _mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
    dupes = sorted({str(k) for k in keys if keys.count(k) > 1})
    if dupes:
        raise yaml.constructor.ConstructorError(
            None, None, f"duplicate key(s) {', '.join(dupes)}", node.start_mark
        )
    return loader.construct_mapping(node, deep=deep)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_config(path: Path | str) -> RunConfig:
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read ({exc.strerror})") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not UTF-8 text") from exc
    try:
        raw: Any = yaml.load(text, Loader=StrictLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        if "recursive" in str(exc):
            node = yaml.compose(text, Loader=StrictLoader)
            where = (_cycle_path(node) if node is not None else None) or "(root)"
            raise ConfigError(f"{path}: {where}: a value that contains itself (cycle)") from exc
        raise ConfigError(f"{path}: not valid YAML ({exc})") from exc
    except RecursionError as exc:
        raise ConfigError(f"{path}: nested too deeply to read") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    bad = _first_non_json(raw)
    if bad is not None:
        raise ConfigError(f"{path}: {bad}: a value JSON cannot express (date, binary, set, cycle)")
    try:
        # strict JSON mode: lists become tuples, but "15" is not a number and "yes" not a bool
        return RunConfig.model_validate_json(json.dumps(raw))
    except (TypeError, ValueError) as exc:
        if not isinstance(exc, ValidationError):
            raise ConfigError(f"{path}: holds a value JSON cannot express ({exc})") from exc
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "(root)"
        raise ConfigError(f"{path}: {loc}: {first['msg']}") from exc
