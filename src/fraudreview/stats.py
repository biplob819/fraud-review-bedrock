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


ALPHA = 0.05
BOOTSTRAP_RESAMPLES = 10_000


def clopper_pearson_upper(k: int, n: int, alpha: float = ALPHA) -> float:
    """Exact one-sided (1 - alpha) upper bound for a binomial proportion; 1 when n is 0."""
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if n == 0 or k == n:
        return 1.0
    if k == 0:
        return 1.0 - alpha ** (1.0 / n)
    from scipy.stats import beta

    return float(beta.ppf(1.0 - alpha, k + 1, n - k))


def rate_with_exact_zero(k: int, n: int) -> Rate:
    """Wilson 95%, except that with zero events the upper bound is the exact Clopper-Pearson
    one-sided bound (D-19)."""
    if k == 0 and n > 0:
        return Rate(k=0, n=n, point=0.0, lo=0.0, hi=clopper_pearson_upper(0, n))
    return wilson(k, n)


def percentile(values: list[int] | list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    if not values:
        raise ValueError("percentile of an empty list")
    if not 0 <= q <= 100:
        raise ValueError("q must be within 0..100")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return float(ordered[rank - 1])


def bootstrap_mean(
    values: list[float], seed: int, resamples: int = BOOTSTRAP_RESAMPLES
) -> tuple[float, float, float]:
    """Mean of `values` with a seeded percentile bootstrap 95% interval over the items. The
    bounds are clamped to the point only to absorb floating-point noise (lo <= point <= hi)."""
    import numpy as np

    data = np.asarray(values, dtype="float64")
    if data.size == 0:
        raise ValueError("bootstrap of an empty list")
    rng = np.random.default_rng(seed)
    means = np.empty(resamples)
    for start in range(0, resamples, 1000):  # bounded memory for large inputs
        stop = min(resamples, start + 1000)
        idx = rng.integers(0, data.size, size=(stop - start, data.size))
        means[start:stop] = data[idx].mean(axis=1)
    point = float(data.mean())
    lo, hi = (float(x) for x in np.percentile(means, [2.5, 97.5]))
    return point, min(lo, point), max(hi, point)
