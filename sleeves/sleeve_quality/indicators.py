"""
sleeves/sleeve_quality/indicators.py
--------------------------------------
Pure quality signal functions for the Quality sleeve.
No I/O, no side effects — only transforms on DataStore reads.

Quality factors target companies with:
    - High and stable returns on equity (ROE)
    - High gross margins with low volatility (durability)
    - Low accruals ratio (cash-backed earnings)
    - Conservative balance sheet (low debt/equity)
    - Consistent revenue growth

All signals are computed cross-sectionally and Z-score normalized
so they can be composited into a single quality score per ticker.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


# ── Signal functions ──────────────────────────────────────────────────


def roe_signal(roe_series: pd.Series) -> pd.Series:
    """
    Return on Equity signal.

    High ROE = high quality. Winsorized at [-0.5, 2.0] to handle
    financials and distressed names with extreme readings.

    Parameters
    ----------
    roe_series : pd.Series indexed by ticker, values = ROE (float, may be NaN)

    Returns
    -------
    Z-score normalized signal, higher = better quality.
    """
    clipped = roe_series.clip(lower=-0.5, upper=2.0)
    return _zscore(clipped)


def gross_margin_signal(gm_series: pd.Series) -> pd.Series:
    """
    Gross margin signal.

    High, stable gross margin = durable competitive advantage.
    Winsorized at [0, 1] (gross margin is bounded in theory; outliers
    can appear due to EDGAR tag mismatches).

    Parameters
    ----------
    gm_series : pd.Series indexed by ticker, values = gross margin (float)

    Returns
    -------
    Z-score normalized signal, higher = better quality.
    """
    clipped = gm_series.clip(lower=0.0, upper=1.0)
    return _zscore(clipped)


def accruals_signal(accruals_series: pd.Series) -> pd.Series:
    """
    Accruals ratio signal (inverted — low accruals = high quality).

    Accruals = (Net Income - Operating Cash Flow) / Total Assets.
    Low accruals means earnings are backed by real cash flows.

    Parameters
    ----------
    accruals_series : pd.Series indexed by ticker, values = accruals ratio

    Returns
    -------
    Z-score normalized signal, higher = better quality (note: inverted).
    """
    # Invert: lower accruals → higher signal
    return _zscore(-accruals_series)


def debt_equity_signal(de_series: pd.Series) -> pd.Series:
    """
    Debt/equity signal (inverted — low leverage = high quality).

    Winsorized at [0, 5] to prevent highly-leveraged names
    (REITs, banks) from dominating the cross-section.

    Parameters
    ----------
    de_series : pd.Series indexed by ticker, values = debt/equity ratio

    Returns
    -------
    Z-score normalized signal, higher = better quality (note: inverted).
    """
    clipped = de_series.clip(lower=0.0, upper=5.0)
    # Invert: lower leverage → higher signal
    return _zscore(-clipped)


def revenue_growth_signal(
    rev_current: pd.Series,
    rev_prior:   pd.Series,
) -> pd.Series:
    """
    Year-over-year revenue growth signal.

    Winsorized at [-0.5, 2.0] to dampen M&A distortions and
    outlier readings from small-cap tickers.

    Parameters
    ----------
    rev_current : TTM revenue as of as_of_date
    rev_prior   : TTM revenue as of one year prior (PIT correct)

    Returns
    -------
    Z-score normalized signal, higher = better quality.
    """
    # Avoid divide-by-zero and negative-denominator distortions
    mask  = (rev_prior.notna()) & (rev_prior > 0) & (rev_current.notna())
    growth = pd.Series(np.nan, index=rev_current.index)
    growth[mask] = (rev_current[mask] / rev_prior[mask]) - 1.0
    clipped = growth.clip(lower=-0.5, upper=2.0)
    return _zscore(clipped)


# ── Composite scorer ──────────────────────────────────────────────────


SIGNAL_WEIGHTS = {
    'roe':            0.30,   # profitability quality
    'gross_margin':   0.25,   # competitive moat proxy
    'accruals':       0.20,   # earnings quality (cash-backing)
    'debt_equity':    0.15,   # balance sheet conservatism
    'revenue_growth': 0.10,   # growth consistency
}

assert abs(sum(SIGNAL_WEIGHTS.values()) - 1.0) < 1e-6, "Signal weights must sum to 1.0"


def composite_quality_score(signals: dict[str, pd.Series]) -> pd.Series:
    """
    Compute weighted composite quality score across available signals.

    Missing signals (NaN or not present) are handled gracefully:
    weights are renormalized over available signals so partial coverage
    does not zero out the composite.

    Parameters
    ----------
    signals : dict mapping signal name → normalized signal Series (all same index)

    Returns
    -------
    pd.Series of composite quality scores, higher = better quality.
    NaN for tickers with no valid signals.
    """
    if not signals:
        return pd.Series(dtype=float)

    # Stack into DataFrame and compute weighted mean ignoring NaN
    df = pd.DataFrame(signals)

    # Ensure all weights align to available columns
    weights = pd.Series({k: v for k, v in SIGNAL_WEIGHTS.items() if k in df.columns})
    weights = weights / weights.sum()   # renormalize for partial coverage

    # Weighted mean, ignoring NaN (each ticker may have different coverage)
    score = pd.Series(np.nan, index=df.index)
    for ticker in df.index:
        row = df.loc[ticker]
        valid = row.dropna()
        if valid.empty:
            continue
        w = weights[valid.index]
        w = w / w.sum()
        score[ticker] = float((valid * w).sum())

    return score


# ── Helpers ───────────────────────────────────────────────────────────


def _zscore(s: pd.Series) -> pd.Series:
    """Cross-sectional Z-score. Returns NaN for series with zero variance."""
    s_clean = s.dropna()
    if len(s_clean) < 3:
        return pd.Series(np.nan, index=s.index)
    mu  = s_clean.mean()
    std = s_clean.std()
    if std < 1e-10:
        return pd.Series(0.0, index=s.index)
    return (s - mu) / std
