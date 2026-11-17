"""EXP-10: variant comparison with EvalKit's paired bootstrap and Holm correction. Synthetic paired
runs; no network, no model."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from fraudreview import compare as c
from fraudreview.schemas import Disposition

RES = 2000


def alert(i: int) -> str:
    return f"a-abc{i:05x}de{i:05x}f"  # never 12 digits in a row


def disp(i: int, final: str) -> Disposition:
    stage = 1 if final == "auto_cleared" else 2
    return Disposition(
        alert_id=alert(i),
        final=final,  # type: ignore[arg-type]
        decided_at_stage=stage,  # type: ignore[arg-type]
        decision_ids=tuple(f"{alert(i)}-s{s}" for s in range(1, stage + 1)),
        queue={"auto_cleared": None, "hold": "priority", "escalate": "standard"}[final],  # type: ignore[arg-type]
    )


def run(cleared: set[int], n: int) -> list[Disposition]:
    return [disp(i, "auto_cleared" if i in cleared else "escalate") for i in range(n)]


def compare(a: set[int], b: set[int], frauds: set[int], n: int = 100) -> c.Comparison:
    labels = {alert(i): i in frauds for i in range(n)}
    return c.compare_runs(run(a, n), run(b, n), labels, ("a", "b"), n_resamples=RES)


def test_a_real_gap_is_claimed() -> None:
    """B auto-clears 40 more legitimate alerts and leaks nothing: auto_clear up, claimed."""
    r = compare(set(range(10, 30)), set(range(10, 70)), frauds=set(range(0, 10)))
    g = r.gap("auto_clear")
    assert g.claimed and g.diff == pytest.approx(0.40) and g.lo > 0
    assert not r.gap("leakage").claimed  # no leakage in either run: nothing to claim
    assert c.verdict(r) == "better"
    swapped = compare(set(range(10, 70)), set(range(10, 30)), frauds=set(range(0, 10)))
    assert swapped.gap("auto_clear").claimed and c.verdict(swapped) == "worse"  # savings lost


def test_a_leakage_fall_is_claimed_and_better() -> None:
    r = compare(set(range(0, 12)), set(), frauds=set(range(0, 12)))
    assert r.gap("leakage").claimed and r.gap("leakage").diff < 0
    assert c.verdict(r) == "better"


def test_noise_is_not_claimed() -> None:
    rng = random.Random(7)
    a = {i for i in range(200) if rng.random() < 0.3}
    b = set(a)
    for i in rng.sample(range(200), 12):  # a few alerts flip in both directions
        b ^= {i}
    r = compare(a, b, frauds=set(), n=200)
    assert not any(g.claimed for g in r.gaps)
    assert c.verdict(r) == "no difference"
    same = compare(a, a, frauds=set(), n=200)
    assert same.gap("auto_clear").lo == same.gap("auto_clear").hi == 0
    assert c.verdict(same) == "no difference"


def test_holm_blocks_one_of_two_borderline_gaps() -> None:
    """A leaks 6 frauds that B does not and B clears 16 more legitimate alerts. Both gaps have an
    uncorrected p below 0.05 and an uncorrected interval that excludes 0; only the stronger one
    survives the Holm-corrected interval."""
    r = compare(set(range(0, 6)), set(range(6, 22)), frauds=set(range(0, 6)))
    leak, clear = r.gap("leakage"), r.gap("auto_clear")
    assert leak.hi < 0 and leak.p < 0.01
    assert clear.lo > 0 and clear.p < 0.05  # significant on its own ...
    assert clear.holm_lo <= 0  # ... but not once corrected
    assert leak.claimed and not clear.claimed
    assert c.verdict(r) == "better"  # the claimed leakage fall decides it


def test_a_leakage_increase_is_always_worse() -> None:
    """One extra leaked fraud is worse even though the interval is far from a claim, and even
    when auto-clears rose by a claimed gap."""
    one = compare(set(), {0}, frauds={0})
    assert not one.gap("leakage").claimed
    assert c.verdict(one) == "worse"
    many = compare(set(), set(range(1, 60)) | {0}, frauds={0})
    assert many.gap("auto_clear").claimed and many.gap("auto_clear").diff > 0
    assert many.gap("leakage").diff > 0 and not many.gap("leakage").claimed
    assert c.verdict(many) == "worse"


def test_the_runs_must_cover_the_same_alerts() -> None:
    labels = {alert(i): False for i in range(5)}
    with pytest.raises(c.CompareError, match="different alerts"):
        c.compare_runs(run(set(), 4), run(set(), 5), labels)
    with pytest.raises(c.CompareError, match="no label"):
        c.compare_runs(run(set(), 5), run(set(), 5), {alert(0): False})
    with pytest.raises(c.CompareError, match="twice"):
        c.compare_runs([disp(0, "hold")] * 2, run(set(), 1), labels)
    with pytest.raises(c.CompareError, match="no alerts"):
        c.compare_runs([], [], labels)


def test_the_result_is_seeded() -> None:
    a, b = set(range(0, 20)), set(range(0, 35))
    assert compare(a, b, set()) == compare(a, b, set())


def test_files_round_trip_and_render(tmp_path: Path) -> None:
    runs = run({0, 1}, 3)
    p = tmp_path / "dispositions.jsonl"
    p.write_text("\n".join(d.model_dump_json() for d in runs) + "\n", encoding="utf-8")
    assert c.load_dispositions(p) == runs
    q = tmp_path / "truth.jsonl"
    q.write_text(
        "\n".join(f'{{"alert_id": "{alert(i)}", "label": {str(i == 0).lower()}}}' for i in range(3))
        + "\n",
        encoding="utf-8",
    )
    labels = c.load_labels(q)
    assert labels == {alert(0): True, alert(1): False, alert(2): False}
    text = c.render(c.compare_runs(runs, runs, labels, n_resamples=RES))
    assert "no difference" in text and "leakage" in text and "auto_clear" in text


def test_fewer_leaks_with_a_claimed_automation_loss_is_safer_not_worse():
    """The night rule: leakage fell (not a claimed gap) and auto-clear fell (claimed)."""
    rng = random.Random(3)
    n = 300
    labels = {alert(i): i % 7 == 0 for i in range(n)}
    leak = 7  # a fraud cleared before, not after
    before = {i for i in range(n) if i % 7 and rng.random() < 0.40} | {leak}  # frauds: i % 7 == 0
    after = {i for i in before if rng.random() < 0.35 and i != leak}

    def runs(cleared: set[int]) -> list[Disposition]:
        return [disp(i, "auto_cleared" if i in cleared else "escalate") for i in range(n)]

    r = c.compare_runs(runs(before), runs(after), labels)
    assert r.gap("auto_clear").claimed and r.gap("auto_clear").diff < 0
    assert not r.gap("leakage").claimed and r.gap("leakage").diff < 0
    assert c.verdict(r) == "safer, less automation"
