"""Concentrated-alpha construction transform.

Takes the engine's regime-aware COMBINED target book (the union of the active
sleeves' weights, already produced by ``core.portfolio_alloc.PortfolioAllocator``)
and concentrates it to the top-N highest-conviction names, conviction-weighted.

This deliberately does NOT re-decide which sleeve is right for the regime or how
names are scored -- that is the engine's job and stays upstream. It only replaces the
"pad out to a broad book" behavior with "hold the few names scoring highest," sized in
proportion to their conviction and capped so a single strong name may run up to
``max_position_weight`` (default 0.50). Whatever cannot be deployed under the cap falls
to cash rather than being spread back across more names.

Conviction proxy: the combined ``target_weight`` from the allocator. That weight already
encodes each name's composite score times its regime-driven sleeve budget, so ranking by
it selects the names the engine is most confident in for the current regime.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_TOP_N = 5
DEFAULT_MAX_POSITION_WEIGHT = 0.50
DEFAULT_TARGET_CASH_WEIGHT = 0.05
_EPS = 1e-12


def _capped_waterfill(convictions: list[float], budget: float, cap: float) -> list[float]:
    """Allocate ``budget`` across names in proportion to conviction, capping each at ``cap``.

    Overflow from capped names is redistributed to the remaining uncapped names (still in
    conviction proportion). Terminates when either everything the budget allows is placed
    or every name has hit the cap (remaining budget then stays undeployed -> cash).
    """
    n = len(convictions)
    alloc = [0.0] * n
    capped = [False] * n
    remaining = float(budget)
    for _ in range(n + 1):
        pool_idx = [i for i in range(n) if not capped[i]]
        pool_conv = sum(convictions[i] for i in pool_idx)
        if not pool_idx or pool_conv <= _EPS or remaining <= _EPS:
            break
        newly_capped = False
        placed = 0.0
        for i in pool_idx:
            share = remaining * (convictions[i] / pool_conv)
            target = alloc[i] + share
            if target >= cap - _EPS:
                placed += cap - alloc[i]
                alloc[i] = cap
                capped[i] = True
                newly_capped = True
            else:
                alloc[i] = target
                placed += share
        remaining = max(0.0, remaining - placed)
        if not newly_capped:
            break
    return alloc


def concentrate_targets(
    weights: pd.DataFrame,
    *,
    top_n: int = DEFAULT_TOP_N,
    max_position_weight: float = DEFAULT_MAX_POSITION_WEIGHT,
    target_cash_weight: float = DEFAULT_TARGET_CASH_WEIGHT,
) -> pd.DataFrame:
    """Return the concentrated top-N book (same columns as input, CASH row dropped).

    Args:
        weights: combined target frame with at least ``ticker`` and ``target_weight``;
            a ``sleeve`` column is preserved if present.
        top_n: number of highest-conviction names to hold (e.g. 3-5).
        max_position_weight: per-name ceiling; a strong name may run up to this (0.50).
        target_cash_weight: cash held back before capping (deployed budget = 1 - this).
    """
    if int(top_n) <= 0:
        raise ValueError("top_n must be > 0")
    if not (0.0 < float(max_position_weight) <= 1.0):
        raise ValueError("max_position_weight must be in (0, 1]")
    if not (0.0 <= float(target_cash_weight) < 1.0):
        raise ValueError("target_cash_weight must be in [0, 1)")

    if weights is None or weights.empty or "ticker" not in weights.columns or "target_weight" not in weights.columns:
        return weights.iloc[0:0].copy() if weights is not None else pd.DataFrame(columns=["ticker", "sleeve", "target_weight"])

    df = weights.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["target_weight"] = pd.to_numeric(df["target_weight"], errors="coerce").fillna(0.0)
    df = df[(df["ticker"] != "CASH") & (df["target_weight"] > 0.0)].copy()
    if df.empty:
        return df

    # top_n must be reachable under the cap: cannot deploy the full invested budget into
    # fewer names than ceil(invested / cap). Keep the requested top_n but never fewer than
    # that floor, so we don't strand cash purely from too-tight a name count.
    invested = 1.0 - float(target_cash_weight)
    min_names_for_budget = max(1, -(-int(invested / float(max_position_weight) + 1 - _EPS)))  # ceil
    effective_n = max(int(top_n), min_names_for_budget)

    df = df.sort_values(["target_weight", "ticker"], ascending=[False, True]).head(effective_n).reset_index(drop=True)
    convictions = df["target_weight"].astype(float).tolist()
    alloc = _capped_waterfill(convictions, budget=invested, cap=float(max_position_weight))
    df["target_weight"] = [round(w, 10) for w in alloc]
    df = df[df["target_weight"] > _EPS].reset_index(drop=True)
    return df
