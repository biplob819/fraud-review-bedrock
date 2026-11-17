"""Variant comparison (E2.2, EXP-10, D-36): is run B better than run A, on the same alerts?

Two events per alert, paired by alert id:

- `leakage`: a fraud that was auto-cleared (1) or not (0), per alert (frauds are the only way to
  score 1, so a mean over all alerts counts leaked frauds per alert);
- `auto_clear`: the alert was auto-cleared (1) or not (0).

For each, EvalKit's paired bootstrap (`evalkit.stats.paired`, seeded) gives the interval and
p-value of mean(B - A), and EvalKit's Holm step-down (`evalkit.stats.holm`) corrects the two
tests together. The Holm-corrected interval is the bootstrap interval at level alpha / 2 (the
Bonferroni width, never looser than Holm's step-down). A gap is claimed only when Holm rejects it
and that interval excludes 0; everything else is "no difference". This is deliberately a little
stricter than Holm alone.

`verdict`:
- "worse" whenever leakage rose (a point increase of leaked frauds), whatever the intervals say:
  a safety loss is never waved through as noise;
- otherwise "better" when leakage fell by a claimed gap, or auto-clears rose by a claimed gap;
- "worse" when auto-clears fell by a claimed gap (savings lost);
- otherwise "no difference".

EvalKit's own run-vs-run `evalkit.compare` works on its metrics summaries and sidecars, not on
this project's dispositions, so this module uses EvalKit's statistics directly. No model call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from evalkit.stats import ALPHA, Resamples, holm, interval_from, paired

from fraudreview.schemas import Disposition

METRICS = ("leakage", "auto_clear")
SEED = 20260517
N_RESAMPLES = 10_000


class CompareError(Exception):
    """The two runs do not cover the same alerts, or an alert has no label."""


@dataclass(frozen=True)
class Gap:
    metric: str
    mean_a: float  # per-alert rate in A
    mean_b: float
    diff: float  # B - A
    lo: float  # unadjusted 95% interval of B - A
    hi: float
    p: float  # unadjusted two-sided bootstrap p-value
    holm_lo: float  # interval of B - A at the corrected level alpha / 2
    holm_hi: float
    claimed: bool  # Holm rejects it and its Holm interval excludes 0


@dataclass(frozen=True)
class Comparison:
    names: tuple[str, str]
    n: int
    gaps: tuple[Gap, ...]

    def gap(self, metric: str) -> Gap:
        return next(g for g in self.gaps if g.metric == metric)


def _events(
    dispositions: Sequence[Disposition], labels: Mapping[str, bool], name: str
) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for d in dispositions:
        if d.alert_id in out:
            raise CompareError(f"run {name}: alert {d.alert_id} appears twice")
        if d.alert_id not in labels:
            raise CompareError(f"run {name}: alert {d.alert_id} has no label")
        cleared = d.final == "auto_cleared"
        out[d.alert_id] = (float(cleared and labels[d.alert_id]), float(cleared))
    return out


def compare_runs(
    a: Sequence[Disposition],
    b: Sequence[Disposition],
    labels: Mapping[str, bool],
    names: tuple[str, str] = ("a", "b"),
    *,
    seed: int = SEED,
    n_resamples: int = N_RESAMPLES,
) -> Comparison:
    """Compare B with A on the same alerts: `labels` maps alert id to fraud (True) or not."""
    ea, eb = _events(a, labels, names[0]), _events(b, labels, names[1])
    if set(ea) != set(eb):
        raise CompareError(
            f"the runs cover different alerts ({len(set(ea) ^ set(eb))} not in both)"
        )
    if not ea:
        raise CompareError("no alerts to compare")
    ids = sorted(ea)
    rs = Resamples(len(ids), n_resamples=n_resamples, seed=seed)
    tests: dict[str, tuple[list[float], list[float]]] = {
        m: ([ea[i][k] for i in ids], [eb[i][k] for i in ids]) for k, m in enumerate(METRICS)
    }
    raw = {m: paired(vb, va, rs) for m, (va, vb) in tests.items()}  # mean(B - A)
    rejected = holm({m: p for m, (_, p) in raw.items()})
    gaps = []
    for m in METRICS:
        va, vb = tests[m]
        iv, p = raw[m]
        level = ALPHA / len(METRICS)  # Bonferroni width: never looser than Holm
        boot = rs.means([y - x for x, y in zip(va, vb, strict=True)])
        adj = interval_from(iv.point, boot, level)
        claimed = rejected[m] and (adj.lo > 0 or adj.hi < 0)
        gaps.append(
            Gap(
                metric=m,
                mean_a=sum(va) / len(va),
                mean_b=sum(vb) / len(vb),
                diff=iv.point,
                lo=iv.lo,
                hi=iv.hi,
                p=p,
                holm_lo=adj.lo,
                holm_hi=adj.hi,
                claimed=claimed,
            )
        )
    return Comparison(names, len(ids), tuple(gaps))


Verdict = Literal["better", "worse", "no difference", "safer, less automation"]


def verdict(result: Comparison) -> Verdict:
    leak, clear = result.gap("leakage"), result.gap("auto_clear")
    if leak.diff > 0:  # leakage rose: always worse, claimed or not
        return "worse"
    if leak.claimed:  # leakage fell
        return "better"
    if clear.claimed:
        if clear.diff < 0 and leak.diff < 0:  # fewer leaks (not a claimed gap), fewer auto-clears
            return "safer, less automation"
        return "better" if clear.diff > 0 else "worse"
    return "no difference"


def load_dispositions(path: Path) -> list[Disposition]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Disposition.model_validate_json(x) for x in lines if x.strip()]


def load_labels(path: Path) -> dict[str, bool]:
    rows = (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return {r["alert_id"]: bool(r["label"]) for r in rows}


def render(result: Comparison) -> str:
    """The comparison as a short plain-text table."""
    lines = [f"{result.names[1]} vs {result.names[0]} on {result.n} alerts: {verdict(result)}"]
    for g in result.gaps:
        lines.append(
            f"  {g.metric}: {g.mean_a:.3f} -> {g.mean_b:.3f}, diff {g.diff:+.3f} "
            f"[{g.lo:+.3f}, {g.hi:+.3f}], Holm [{g.holm_lo:+.3f}, {g.holm_hi:+.3f}], "
            f"p {g.p:.4f}, {'claimed' if g.claimed else 'not claimed'}"
        )
    return "\n".join(lines)
