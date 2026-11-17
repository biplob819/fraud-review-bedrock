"""E2.2: summarise the two-judge faithfulness work that is already in the judge cache, with NO
model calls and no keys. Reads `.fraud/judge-store`: one extraction entry per explanation (its
claims) and one verdict entry per judged claim. Reports claim coverage, per-claim support and
per-explanation scores for each judge that has verdicts, with Wilson intervals, the cost recorded
in the cache, and the verdict mix. Ids never leave this script: only aggregates are written.

    uv run python scripts/judge_cache_summary.py docs/measurements/faithfulness-cache-summary.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from fraudreview.stats import wilson

STORE = Path(".fraud") / "judge-store"


def main(out: Path | None) -> dict[str, object]:
    claims_of: dict[str, list[str]] = {}  # item id -> claim ids
    verdicts: dict[str, dict[str, str]] = defaultdict(dict)  # judge -> claim id -> verdict
    cost = Counter()
    for f in STORE.rglob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        if "claims" in d:
            for c in d["claims"]:
                item = c["id"].rsplit(":explanation:", 1)[0]
                claims_of.setdefault(item, []).append(c["id"])
            cost["extract"] += d.get("cost_usd", 0.0)
        elif isinstance(d.get("verdict"), dict):
            v = d["verdict"]
            verdicts[v["judge"]][v["claim_id"]] = v["verdict"]
            cost[v["judge"]] += d.get("cost_usd", 0.0)
    all_claims = {c for cs in claims_of.values() for c in cs}
    n_items, n_claims = len(claims_of), len(all_claims)
    report: dict[str, object] = {
        "data_label": "simulated",
        "explanations": n_items,
        "claims_extracted": n_claims,
        "claims_per_explanation": round(n_claims / n_items, 2) if n_items else 0,
        "cost_recorded_usd": {k: round(v, 4) for k, v in cost.items()},
        "judges": {},
    }
    for judge, vs in sorted(verdicts.items()):
        judged = {c for c in vs if c in all_claims}
        supported = sum(1 for c in judged if vs[c] == "supported")
        full = [it for it, cs in claims_of.items() if all(c in vs for c in cs)]
        full_ok = sum(1 for it in full if all(vs[c] == "supported" for c in claims_of[it]))
        scores = [
            sum(vs[c] == "supported" for c in claims_of[it]) / len(claims_of[it]) for it in full
        ]
        w = wilson(supported, len(judged)) if judged else None
        wf = wilson(full_ok, len(full)) if full else None
        report["judges"][judge] = {  # type: ignore[index]
            "claims_judged": len(judged),
            "claim_coverage": round(len(judged) / n_claims, 4) if n_claims else 0,
            "verdict_mix": dict(Counter(vs[c] for c in judged)),
            "claims_supported": {
                "k": supported,
                "n": len(judged),
                "point": round(w.point, 4),
                "lo": round(w.lo, 4),
                "hi": round(w.hi, 4),
            }
            if w
            else None,
            "explanations_fully_judged": len(full),
            "explanations_fully_supported": {
                "k": full_ok,
                "n": len(full),
                "point": round(wf.point, 4),
                "lo": round(wf.lo, 4),
                "hi": round(wf.hi, 4),
            }
            if wf
            else None,
            "mean_explanation_score": round(sum(scores) / len(scores), 4) if scores else None,
        }
    if out:
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None), indent=2))
