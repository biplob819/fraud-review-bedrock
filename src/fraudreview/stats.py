"""Small statistics helpers (D-19). E2.1 adds the exact bound and the bootstrap."""

from __future__ import annotations

import math

from fraudreview.schemas import Rate

Z95 = 1.959963984540054  # two-sided 95% normal quantile


def wilson(k: int, n: int) -> Rate:
    """k successes out of n as a `Rate` with its Wilson 95% interval (0 to 1 when n is 0)."""
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if n == 0:
        return Rate(k=0, n=0, point=0.0, lo=0.0, hi=1.0)
    p = k / n
    z2 = Z95 * Z95
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    lo = 0.0 if k == 0 else min(p, max(0.0, centre - half))
    hi = 1.0 if k == n else max(p, min(1.0, centre + half))
    return Rate(k=k, n=n, point=p, lo=lo, hi=hi)
