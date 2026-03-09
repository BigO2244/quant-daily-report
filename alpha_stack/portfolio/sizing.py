"""
Alpha Stack — Position Sizing Utilities
==========================================
Shared sizing helpers used by sleeves and the allocator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def inverse_vol_weights(
    vols: pd.Series,
    vol_floor: float = 0.10,
    vol_cap: float = 0.60,
    position_cap: float = 0.10,
    position_floor: float = 0.02,
) -> pd.Series:
    """
    Compute inverse-volatility weights with floor/ceiling.

    w_i ∝ 1 / clip(vol_i, vol_floor, vol_cap)

    Parameters
    ----------
    vols : Series indexed by ticker
    vol_floor : float — minimum annualised vol used in denominator
    vol_cap   : float — maximum annualised vol used in denominator
    position_cap   : float — maximum weight per position
    position_floor : float — minimum weight per position

    Returns
    -------
    Series of normalised weights.
    """
    clipped = vols.clip(lower=vol_floor, upper=vol_cap)
    raw = 1.0 / clipped
    raw = raw / raw.sum()
    return _iterative_cap(raw, cap=position_cap, floor=position_floor)


def equal_weights(
    n: int,
    position_cap: float = 0.10,
) -> np.ndarray:
    """Return equal weights array for n positions."""
    w = np.full(n, 1.0 / n)
    return np.minimum(w, position_cap)


def score_proportional_weights(
    scores: pd.Series,
    position_cap: float = 0.10,
    position_floor: float = 0.02,
) -> pd.Series:
    """Weight proportional to score."""
    pos_scores = scores.clip(lower=0.01)
    raw = pos_scores / pos_scores.sum()
    return _iterative_cap(raw, cap=position_cap, floor=position_floor)


def _iterative_cap(
    weights: pd.Series,
    cap: float,
    floor: float,
    max_iter: int = 20,
) -> pd.Series:
    """Iteratively cap weights while preserving relative ordering."""
    w = weights.values.copy().astype(float)
    n = len(w)
    if n == 0:
        return weights

    s = w.sum()
    if s <= 0:
        return pd.Series(1.0 / n, index=weights.index)
    w = w / s

    if cap * n < 1.0 - 1e-12:
        return pd.Series(w, index=weights.index)

    below = w < floor
    if below.any() and not below.all():
        w[below] = floor

    for _ in range(max_iter):
        over = w > cap + 1e-9
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        free = ~over & (w > 0)
        if not free.any():
            break
        free_total = w[free].sum()
        if free_total > 0:
            w[free] += excess * (w[free] / free_total)

    s = w.sum()
    if s > 0:
        w = w / s

    return pd.Series(np.minimum(w, cap), index=weights.index)
