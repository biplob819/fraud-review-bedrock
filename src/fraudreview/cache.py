"""Content-addressed response cache (N1.3, Tech Spec §2.1, D-1, EvalKit L-18 and RUN-07).

A key is sha256 over canonical JSON of everything that decides a step's output. Entries live at
`.fraud/store/<key[:2]>/<key>.json`; a write goes to a private temporary file that is then linked
into place, so the first writer wins and a crash never leaves a partial entry. A read checks that
the stored key and `code_version` match what the caller asked for.

`code_versions.json` records, for each registered step, its `code_version` and the sha256 of its
source. `code_version_problems` fails when a step's source changes and its version was not bumped,
so a logic change can never silently reuse old cached replies.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REGISTRY = Path(__file__).with_name("code_versions.json")


class CacheError(Exception):
    """A stored entry is unreadable or does not belong to the key it is filed under."""


def canonical(value: Any) -> str:
    """Canonical JSON: sorted keys, no spaces, no NaN; the same data always gives the same text."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def cache_key(
    *,
    step: str,
    code_version: str,
    inp: Any,
    model: str,
    effort: str,
    prompt_hash: str,
    policy_version: str,
    seed: int,
) -> str:
    parts = {
        "step": step,
        "code_version": code_version,
        "input": inp,
        "model": model,
        "effort": effort,
        "prompt_hash": prompt_hash,
        "policy_version": policy_version,
        "seed": seed,
    }
    return hashlib.sha256(canonical(parts).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise CacheError("a cache key is 64 lowercase hex characters")
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str, code_version: str) -> dict[str, Any] | None:
        p = self.path(key)
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise CacheError(
                f"cache entry {key[:12]} is unreadable ({type(exc).__name__})"
            ) from None
        if not isinstance(entry, dict) or entry.get("key") != key:
            raise CacheError(f"cache entry {key[:12]} is filed under the wrong key")
        if entry.get("code_version") != code_version:
            raise CacheError(
                f"cache entry {key[:12]} was written by code_version {entry.get('code_version')!r}"
            )
        value = entry.get("value")
        if not isinstance(value, dict):
            raise CacheError(f"cache entry {key[:12]} holds no value")
        return value

    def put(self, key: str, code_version: str, value: Mapping[str, Any]) -> bool:
        """Write once. Returns True if this call wrote the entry, False if one already existed
        (the existing entry is kept: first writer wins)."""
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        text = canonical({"key": key, "code_version": code_version, "value": dict(value)})
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, p)  # atomic, and fails if the entry exists
            except FileExistsError:
                return False
            return True
        finally:
            os.unlink(tmp)


def source_hash(fn: Callable[..., Any]) -> str:
    return hashlib.sha256(inspect.getsource(fn).encode("utf-8")).hexdigest()


def code_version_problems(
    steps: Mapping[str, tuple[str, Callable[..., Any]]], registry: Path = REGISTRY
) -> list[str]:
    """`steps` maps a step name to (its code_version, the function holding its logic). A step is a
    problem when it is missing from the registry, or its source hash changed while its
    code_version stayed the same."""
    recorded = json.loads(registry.read_text(encoding="utf-8"))
    problems = []
    for name, (version, fn) in sorted(steps.items()):
        row = recorded.get(name)
        if not isinstance(row, dict):
            problems.append(f"{name}: not in {registry.name}")
            continue
        changed = row.get("source_sha256") != source_hash(fn)
        if changed and row.get("code_version") == version:
            problems.append(f"{name}: source changed; bump code_version (now {version!r})")
        elif row.get("code_version") != version or changed:
            problems.append(f"{name}: {registry.name} is stale; record {version!r} and its hash")
    return problems
