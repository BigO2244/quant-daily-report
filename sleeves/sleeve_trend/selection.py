# sleeves/sleeve_trend/selection.py
"""
Sleeve Trend v1 — Cross-Sectional Selection & Weighting
========================================================

This is the SIGNAL ENGINE. It takes the indicator-enriched DataFrame from
prepare_data() and produces differentiated target weights for paper trading.

Design principles:
  1. Score every eligible stock on a multi-factor composite (0-100).
  2. Select top N names that pass trend, quality, and liquidity gates.
  3. Weight by inverse volatility (not equal weight) — lower-vol names
     get more capital, creating a natural risk-parity tilt.
  4. Output a DataFrame that plugs directly into the portfolio allocator
     and the signals JSON writer.

Usage:
    from sleeves.sleeve_trend.backtest import prepare_data
    from sleeves.sleeve_trend.selection import select_and_weight

    signals = prepare_data()
    targets = select_and_weight(signals)
    # targets: DataFrame with [ticker, target_weight, sleeve, score, ...]
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from . import config as cfg

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# CONFIGURATION — all tunable, none hardcoded in logic
# ────────────────────────────────────────────────────────────

# How many names to hold (after filtering)
TOP_N = int(cfg.TOP_LONGS)  # default 3 from config; override here if desired
TOP_N_MAX = 15  # upper bound for safety

# Composite score weights (must sum to 1.0)
W_TREND_STRENGTH = 0.30  # EMA alignment + distance from 200d
W_MOMENTUM = 0.25  # 20-day and 60-day return (cross-sectional rank)
W_ADX = 0.25  # Trend conviction (ADX level)
W_VOLUME = 0.10  # Volume confirmation
W_VOLATILITY = 0.10  # Reward lower vol (stability)

# Gate thresholds — stock must pass ALL gates to be eligible
GATE_ABOVE_200_EMA = True  # Must be above EMA(200)
GATE_EMA_FAST_ABOVE_SLOW = True  # EMA(fast) > EMA(slow)
GATE_ADX_MIN = 15.0  # Minimum ADX (trending, not ranging)
GATE_MIN_PRICE = cfg.MIN_PRICE  # $5 min price
GATE_MIN_AVG_VOLUME = cfg.MIN_AVG_VOLUME  # 100K shares min

# Weighting method
WEIGHT_METHOD = "inverse_vol"  # "inverse_vol" | "score" | "equal"
MAX_SINGLE_WEIGHT = 0.50  # 50% cap per position
MIN_SINGLE_WEIGHT = 0.02  # 2% floor — don't bother with tiny positions

# Inverse-vol parameters
IVOL_LOOKBACK = 20  # Days of returns for realized vol
IVOL_FLOOR_ANNUAL = 0.05  # 5% annualized vol floor (prevents blow-up on low-vol names)
IVOL_CAP_ANNUAL = 0.80  # 80% annualized vol cap

# Sector constraints
MAX_PER_SECTOR = cfg.MAX_POSITIONS_PER_SECTOR  # default 2


# ────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ────────────────────────────────────────────────────────────

def select_and_weight(
    signals: pd.DataFrame,
    asof_date: Optional[pd.Timestamp | str] = None,
    top_n: int = TOP_N,
    weight_method: str = WEIGHT_METHOD,
) -> pd.DataFrame:
    """
    Score, rank, select, and weight the universe for a single date.

    Parameters
    ----------
    signals : DataFrame
        Output of prepare_data() — must have columns:
        date, ticker, close, open, high, low, volume, atr,
        adx, ema_fast, ema_slow, ema_trend, above_trend,
        volume_sma, daily_return, sector, passes_liquidity
    asof_date : str or Timestamp, optional
        Date to score as-of. Defaults to the latest date in signals.
    top_n : int
        Number of positions to select.
    weight_method : str
        "inverse_vol" | "score" | "equal"

    Returns
    -------
    DataFrame with columns:
        ticker, target_weight, sleeve, score, rank,
        sector, adx, realized_vol, reason
    Empty DataFrame if no stocks pass the gates.
    """
    signals = signals.copy()
    signals["date"] = pd.to_datetime(signals["date"])

    if asof_date is None:
        asof_date = signals["date"].max()
    else:
        asof_date = pd.to_datetime(asof_date)

    # ── 1. Snapshot: latest row per ticker as of asof_date ─────
    latest = signals[signals["date"] == asof_date].copy()
    if latest.empty:
        # Fall back to most recent available date
        available = signals[signals["date"] <= asof_date]["date"].max()
        if pd.isna(available):
            logger.warning("[SELECTION] No data available on or before %s", asof_date)
            return _empty_result()
        latest = signals[signals["date"] == available].copy()
        logger.info("[SELECTION] Using fallback date %s (requested %s)", available, asof_date)

    latest = latest.drop_duplicates(subset="ticker", keep="last")
    logger.info("[SELECTION] Universe size: %d tickers on %s", len(latest), asof_date)

    # ── 2. Compute realized vol per ticker (need history) ──────
    vol_map = _compute_realized_vol(signals, asof_date, lookback=IVOL_LOOKBACK)
    latest = latest.merge(
        vol_map, on="ticker", how="left", suffixes=("", "_vol")
    )
    latest["realized_vol"] = latest["realized_vol"].fillna(
        IVOL_CAP_ANNUAL  # unknown vol → conservative
    )

    # ── 3. Compute momentum scores (need history) ─────────────
    mom_map = _compute_momentum(signals, asof_date)
    latest = latest.merge(mom_map, on="ticker", how="left")
    latest["mom_20d"] = latest["mom_20d"].fillna(0.0)
    latest["mom_60d"] = latest["mom_60d"].fillna(0.0)

    # ── 4. Apply gates ─────────────────────────────────────────
    eligible = _apply_gates(latest)
    logger.info(
        "[SELECTION] After gates: %d eligible of %d total",
        len(eligible), len(latest),
    )

    if eligible.empty:
        logger.warning("[SELECTION] No stocks pass gates on %s", asof_date)
        return _empty_result()

    # ── 5. Score (cross-sectional ranks → composite) ───────────
    scored = _score_cross_section(eligible)

    # ── 6. Apply sector constraints ────────────────────────────
    scored = _apply_sector_cap(scored, max_per_sector=MAX_PER_SECTOR)

    # ── 7. Select top N ────────────────────────────────────────
    n = min(top_n, TOP_N_MAX, len(scored))
    selected = scored.head(n).copy()
    logger.info(
        "[SELECTION] Selected %d names. Top: %s",
        len(selected),
        selected["ticker"].tolist(),
    )

    # ── 8. Weight ──────────────────────────────────────────────
    weighted = _assign_weights(selected, method=weight_method)

    # ── 9. Format output ───────────────────────────────────────
    result = weighted[
        ["ticker", "target_weight", "sleeve", "score", "rank",
         "sector", "adx", "realized_vol", "reason"]
    ].copy()

    _log_summary(result)
    return result


# ────────────────────────────────────────────────────────────
# GATES — hard pass/fail filters
# ────────────────────────────────────────────────────────────

def _apply_gates(df: pd.DataFrame) -> pd.DataFrame:
    """Apply hard eligibility gates. Returns filtered DataFrame."""
    mask = pd.Series(True, index=df.index)

    # Price and volume liquidity
    mask &= df["close"] >= GATE_MIN_PRICE
    if "volume_sma" in df.columns:
        mask &= df["volume_sma"] >= GATE_MIN_AVG_VOLUME
    elif "passes_liquidity" in df.columns:
        mask &= df["passes_liquidity"].fillna(False)

    # Trend alignment
    if GATE_ABOVE_200_EMA and "above_trend" in df.columns:
        mask &= df["above_trend"].fillna(False)

    # EMA fast > slow (uptrend)
    if GATE_EMA_FAST_ABOVE_SLOW and "ema_fast" in df.columns and "ema_slow" in df.columns:
        mask &= df["ema_fast"] > df["ema_slow"]

    # Minimum ADX (market is trending)
    if "adx" in df.columns:
        mask &= df["adx"].fillna(0) >= GATE_ADX_MIN

    # Exclude anything with NaN close
    mask &= df["close"].notna() & (df["close"] > 0)

    return df[mask].copy()


# ────────────────────────────────────────────────────────────
# SCORING — cross-sectional rank composite
# ────────────────────────────────────────────────────────────

def _score_cross_section(df: pd.DataFrame) -> pd.DataFrame:
    """
    Score each stock on a 0-100 composite from multiple factors.
    All factor scores are cross-sectional percentile ranks (0-100).
    """
    scored = df.copy()
    n = len(scored)

    if n == 0:
        scored["score"] = 0.0
        scored["rank"] = 0
        return scored

    # Factor 1: Trend strength (distance above 200 EMA, normalized)
    if "ema_trend" in scored.columns and "close" in scored.columns:
        scored["_trend_pct"] = (
            (scored["close"] - scored["ema_trend"]) / scored["ema_trend"]
        ).clip(-0.5, 0.5)
        scored["_trend_rank"] = scored["_trend_pct"].rank(pct=True) * 100
    else:
        scored["_trend_rank"] = 50.0

    # Factor 2: Momentum (blend of 20d and 60d returns)
    if "mom_20d" in scored.columns and "mom_60d" in scored.columns:
        scored["_mom_blend"] = 0.6 * scored["mom_20d"] + 0.4 * scored["mom_60d"]
        scored["_mom_rank"] = scored["_mom_blend"].rank(pct=True) * 100
    else:
        scored["_mom_rank"] = 50.0

    # Factor 3: ADX (trend conviction)
    if "adx" in scored.columns:
        scored["_adx_rank"] = scored["adx"].rank(pct=True) * 100
    else:
        scored["_adx_rank"] = 50.0

    # Factor 4: Volume confirmation (relative volume)
    if "volume_ratio" in scored.columns:
        scored["_vol_rank"] = scored["volume_ratio"].clip(0, 5).rank(pct=True) * 100
    elif "volume_sma" in scored.columns and "volume" in scored.columns:
        scored["_vol_ratio"] = scored["volume"] / scored["volume_sma"].clip(lower=1)
        scored["_vol_rank"] = scored["_vol_ratio"].clip(0, 5).rank(pct=True) * 100
    else:
        scored["_vol_rank"] = 50.0

    # Factor 5: Lower volatility is better (stability)
    if "realized_vol" in scored.columns:
        # Invert: lower vol → higher rank
        scored["_stability_rank"] = (
            scored["realized_vol"].rank(pct=True, ascending=False) * 100
        )
    else:
        scored["_stability_rank"] = 50.0

    # Composite
    scored["score"] = (
        W_TREND_STRENGTH * scored["_trend_rank"]
        + W_MOMENTUM * scored["_mom_rank"]
        + W_ADX * scored["_adx_rank"]
        + W_VOLUME * scored["_vol_rank"]
        + W_VOLATILITY * scored["_stability_rank"]
    ).round(2)

    scored["rank"] = scored["score"].rank(ascending=False, method="first").astype(int)
    scored = scored.sort_values("rank")

    # Clean up temp columns
    scored = scored.drop(
        columns=[c for c in scored.columns if c.startswith("_")],
        errors="ignore",
    )

    return scored


# ────────────────────────────────────────────────────────────
# SECTOR CONSTRAINTS
# ────────────────────────────────────────────────────────────

def _apply_sector_cap(scored: pd.DataFrame, max_per_sector: int) -> pd.DataFrame:
    """Enforce max positions per sector. Already sorted by score desc."""
    if max_per_sector <= 0 or "sector" not in scored.columns:
        return scored

    keep_idx = []
    sector_counts: dict[str, int] = {}

    for idx, row in scored.iterrows():
        sector = str(row.get("sector", "Unknown")).strip()
        if sector in ("", "nan", "None"):
            sector = "Unknown"
        count = sector_counts.get(sector, 0)
        if count < max_per_sector:
            keep_idx.append(idx)
            sector_counts[sector] = count + 1

    result = scored.loc[keep_idx].copy()
    dropped = len(scored) - len(result)
    if dropped > 0:
        logger.info("[SELECTION] Sector cap dropped %d names", dropped)
    return result


# ────────────────────────────────────────────────────────────
# WEIGHTING
# ────────────────────────────────────────────────────────────

def _assign_weights(
    selected: pd.DataFrame,
    method: str = "inverse_vol",
) -> pd.DataFrame:
    """
    Assign target weights to selected names.

    Methods:
        inverse_vol — weight proportional to 1/realized_vol (risk parity tilt)
        score       — weight proportional to composite score
        equal       — 1/N equal weight
    """
    df = selected.copy()
    n = len(df)

    if n == 0:
        df["target_weight"] = 0.0
        df["sleeve"] = "sleeve_trend"
        df["reason"] = "no_selection"
        return df

    if method == "inverse_vol" and "realized_vol" in df.columns:
        # Clip vol to prevent extreme weights
        vol = df["realized_vol"].clip(
            lower=IVOL_FLOOR_ANNUAL,
            upper=IVOL_CAP_ANNUAL,
        )
        raw_w = 1.0 / vol
        df["target_weight"] = raw_w / raw_w.sum()

    elif method == "score" and "score" in df.columns:
        scores = df["score"].clip(lower=1.0)  # floor to prevent zero
        df["target_weight"] = scores / scores.sum()

    else:
        # Equal weight fallback
        df["target_weight"] = 1.0 / n

    # Iterative cap: clip overweight positions, redistribute excess
    # to preserve differentiation among remaining positions.
    df["target_weight"] = _iterative_cap(
        df["target_weight"].values,
        cap=MAX_SINGLE_WEIGHT,
        floor=MIN_SINGLE_WEIGHT,
    )

    df["target_weight"] = df["target_weight"].round(6)
    df["sleeve"] = "sleeve_trend"
    df["reason"] = f"selected_by_{method}"

    return df


def _iterative_cap(
    weights: np.ndarray,
    cap: float,
    floor: float,
    max_iterations: int = 20,
) -> np.ndarray:
    """
    Iteratively cap weights while preserving relative ordering.

    Naive clip-and-renormalize destroys differentiation when N is small.
    This iteratively freezes capped positions and redistributes among
    the rest proportionally.
    """
    w = weights.copy().astype(float)
    n = len(w)
    if n == 0:
        return w

    # Normalize to sum to 1
    s = w.sum()
    if s <= 0:
        return np.full(n, 1.0 / n)
    w = w / s

    # If cap is infeasible for this basket size, keep normalized weights.
    # This preserves current behavior for small-N cases (e.g., equal weight top-5 with 10% cap).
    if cap * n < 1.0 - 1e-12:
        return w

    # Apply floor first
    below_floor = w < floor
    if below_floor.any() and not below_floor.all():
        w[below_floor] = floor

    for _ in range(max_iterations):
        over = w > cap + 1e-9
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        free = ~over & (w > 0)
        if free.sum() == 0:
            break
        # Distribute excess proportionally to uncapped positions
        free_total = w[free].sum()
        if free_total > 0:
            w[free] += excess * (w[free] / free_total)

    # Final drift adjust without re-inflating capped names.
    s = w.sum()
    if s <= 0:
        return np.full(n, 1.0 / n)

    drift = 1.0 - s
    if abs(drift) > 1e-12:
        if drift < 0:
            # Scale down only (cannot violate cap).
            w = w / s
        else:
            under = w < cap - 1e-12
            if under.any():
                under_sum = w[under].sum()
                if under_sum > 1e-12:
                    w[under] += drift * (w[under] / under_sum)
                else:
                    w[under] += drift / under.sum()

    # Numeric safety: never exceed cap.
    w = np.minimum(w, cap)

    return w


# ────────────────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────────────────

def _compute_realized_vol(
    signals: pd.DataFrame,
    asof_date: pd.Timestamp,
    lookback: int = 20,
) -> pd.DataFrame:
    """Compute annualized realized vol per ticker using daily returns."""
    hist = signals[signals["date"] <= asof_date].copy()
    if "daily_return" not in hist.columns:
        hist["daily_return"] = hist.groupby("ticker")["close"].pct_change()

    # Keep last `lookback` days per ticker
    hist = (
        hist.sort_values(["ticker", "date"])
        .groupby("ticker")
        .tail(lookback)
    )

    vol = (
        hist.groupby("ticker")["daily_return"]
        .std()
        .reset_index()
        .rename(columns={"daily_return": "realized_vol"})
    )
    # Annualize
    vol["realized_vol"] = vol["realized_vol"] * np.sqrt(252)
    return vol


def _compute_momentum(
    signals: pd.DataFrame,
    asof_date: pd.Timestamp,
) -> pd.DataFrame:
    """Compute 20-day and 60-day momentum per ticker."""
    hist = signals[signals["date"] <= asof_date].copy()
    hist = hist.sort_values(["ticker", "date"])

    results = []
    for ticker, group in hist.groupby("ticker"):
        if len(group) < 5:
            continue
        close = group["close"].values
        mom_20d = (close[-1] / close[-min(20, len(close))] - 1) if len(close) >= 5 else 0.0
        mom_60d = (close[-1] / close[-min(60, len(close))] - 1) if len(close) >= 20 else 0.0
        results.append({
            "ticker": ticker,
            "mom_20d": mom_20d,
            "mom_60d": mom_60d,
        })

    return pd.DataFrame(results) if results else pd.DataFrame(columns=["ticker", "mom_20d", "mom_60d"])


def _empty_result() -> pd.DataFrame:
    """Return properly typed empty DataFrame."""
    return pd.DataFrame(
        columns=[
            "ticker", "target_weight", "sleeve", "score", "rank",
            "sector", "adx", "realized_vol", "reason",
        ]
    )


def _log_summary(result: pd.DataFrame) -> None:
    """Log a human-readable summary of the selection."""
    if result.empty:
        logger.info("[SELECTION] No positions selected.")
        return

    logger.info("[SELECTION] ── Final Portfolio ──")
    for _, row in result.iterrows():
        logger.info(
            "  %-6s  w=%.1f%%  score=%.1f  adx=%.1f  vol=%.1f%%  sector=%s",
            row["ticker"],
            row["target_weight"] * 100,
            row.get("score", 0),
            row.get("adx", 0),
            row.get("realized_vol", 0) * 100,
            row.get("sector", "?"),
        )

    total_w = result["target_weight"].sum()
    hhi = (result["target_weight"] ** 2).sum()
    logger.info(
        "  Total weight: %.2f%%  |  HHI: %.4f  |  Effective N: %.1f",
        total_w * 100, hhi, 1.0 / hhi if hhi > 0 else 0,
    )
