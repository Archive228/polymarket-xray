"""Check whether an edge is just luck.

Every resolved position is a coin whose win probability equals the entry price (if the market is fair).
The number of wins W is the sum of such coins (a Poisson-binomial distribution). We check how far the
observed number of wins k is from the expected Σp: the exact tail P(W ≥ k) up to 3000 positions,
a normal approximation beyond that. Across buckets – Holm correction for multiple tests.

Wilson interval and z-test – after Dennim89/polymarket-wallet-analyzer analytics/stats.go, edge.go
(MIT, Copyright (c) 2026 Denys), with one change: the test is on the position outcome, not on PnL > 0."""
from __future__ import annotations

import math

import numpy as np

EXACT_MAX = 3000


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def _pb_pmf(ps: np.ndarray) -> np.ndarray:
    dp = np.zeros(len(ps) + 1)
    dp[0] = 1.0
    for i, p in enumerate(ps):
        dp[1:i + 2] = dp[1:i + 2] * (1 - p) + dp[0:i + 1] * p
        dp[0] *= 1 - p
    return dp


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def excess_test(ps, won) -> dict:
    """ps – entry prices (the probability the market "promised"), won – whether the position won."""
    ps = np.clip(np.asarray(ps, dtype=float), 1e-6, 1 - 1e-6)
    won = np.asarray(won, dtype=bool)
    n, k, mu = len(ps), int(won.sum()), float(ps.sum())
    var = float((ps * (1 - ps)).sum())
    if n == 0:
        return {"n": 0}
    if n <= EXACT_MAX:
        pmf = _pb_pmf(ps)
        upper, lower = float(pmf[k:].sum()), float(pmf[:k + 1].sum())
        method = "exact"
    else:
        sd = math.sqrt(var) or 1e-9
        upper, lower = _norm_sf((k - 0.5 - mu) / sd), 1 - _norm_sf((k + 0.5 - mu) / sd)
        method = "normal approximation"
    p_two = min(1.0, 2 * min(upper, lower))
    ci = wilson(k, n)
    return {"n": n, "wins": k, "expected_wins": round(mu, 1), "z": round((k - mu) / math.sqrt(var), 2) if var > 0 else None,
            "hit_rate": round(k / n, 3), "price_avg": round(mu / n, 3), "edge_pp": round((k - mu) / n * 100, 1),
            "hit_rate_ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None, "p_value": round(p_two, 4), "method": method}


def holm(pvals: list[float | None]) -> list[float | None]:
    """Holm correction: the more buckets we test, the stricter the threshold for each."""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    order = sorted(idx, key=lambda i: pvals[i])
    out: list[float | None] = [None] * len(pvals)
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(order) - r) * pvals[i]))
        out[i] = round(run, 4)
    return out


def verdict(t: dict, p_adj: float | None, min_n: int = 30) -> str:
    if not t.get("n") or t["n"] < min_n:
        return "too little data"
    if p_adj is not None and p_adj < 0.05:
        return "beats the price" if t["wins"] > t["expected_wins"] else "loses to the price"
    return "indistinguishable from chance"
