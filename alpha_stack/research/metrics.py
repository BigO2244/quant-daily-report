"""
Alpha Stack — Research Metrics
=================================
Core quantitative metrics for backtest and attribution evaluation.

Functions:
    sharpe_ratio()          — annualised Sharpe
    sortino_ratio()         — annualised Sortino
    max_drawdown()          — maximum drawdown over a period
    calmar_ratio()          — CAGR / max drawdown
    information_coefficient() — Spearman rank IC between scores and forward returns
    rank_ic_series()        — rolling IC over time
    turnover()              — average daily turnover
    cagr()                  — compound annual growth rate
    hit_rate()              — fraction of positive return periods
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
try:
    from scipy import stats as _scipy_stats  # type: ignore
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False
    _scipy_stats = None  # type: ignore

logger = logging.getLogger(__name__)


def sharpe_ratio(
    returns: pd.Series,
    risk_free: float = 0.0,
    annualise: bool = True,
    periods_per_year: int = 252,
) -> float:
    """Annualised Sharpe ratio."""
    excess = returns - risk_free / periods_per_year
    mu = excess.mean()
    sigma = excess.std(ddof=1)
    if sigma == 0 or pd.isna(sigma):
        return 0.0
    raw = mu / sigma
    return float(raw * np.sqrt(periods_per_year) if annualise else raw)


def sortino_ratio(
    returns: pd.Series,
    risk_free: float = 0.0,
    annualise: bool = True,
    periods_per_year: int = 252,
) -> float:
    """Annualised Sortino ratio (downside deviation)."""
    excess = returns - risk_free / periods_per_year
    mu = excess.mean()
    downside = excess[excess < 0]
    sigma_d = downside.std(ddof=1) if len(downside) > 1 else 0.0
    if sigma_d == 0 or pd.isna(sigma_d):
        return 0.0
    raw = mu / sigma_d
    return float(raw * np.sqrt(periods_per_year) if annualise else raw)


def max_drawdown(nav: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown.

    Parameters
    ----------
    nav : Series
        NAV or cumulative return series.

    Returns
    -------
    float (negative, e.g. -0.15 for 15% drawdown)
    """
    peak = nav.cummax()
    dd = (nav - peak) / peak
    return float(dd.min())


def drawdown_series(nav: pd.Series) -> pd.Series:
    """Return the full drawdown time series."""
    peak = nav.cummax()
    return (nav - peak) / peak


def calmar_ratio(nav: pd.Series, periods_per_year: int = 252) -> float:
    """CAGR / abs(max_drawdown)."""
    cagr_val = cagr(nav, periods_per_year)
    mdd = abs(max_drawdown(nav))
    if mdd == 0:
        return float("inf")
    return cagr_val / mdd


def cagr(nav: pd.Series, periods_per_year: int = 252) -> float:
    """Compound annual growth rate."""
    if len(nav) < 2:
        return 0.0
    total = float(nav.iloc[-1] / nav.iloc[0])
    n_years = len(nav) / periods_per_year
    if n_years <= 0 or total <= 0:
        return 0.0
    return float(total ** (1 / n_years) - 1)


def hit_rate(returns: pd.Series) -> float:
    """Fraction of positive return periods."""
    if len(returns) == 0:
        return 0.0
    return float((returns > 0).mean())


def information_coefficient(
    scores: pd.Series,
    forward_returns: pd.Series,
    method: str = "spearman",
) -> float:
    """
    Information coefficient: rank correlation between scores and forward returns.

    Parameters
    ----------
    scores : Series indexed by ticker
    forward_returns : Series indexed by ticker (same index)
    method : str — "spearman" (rank IC) or "pearson"

    Returns
    -------
    float IC in [-1, 1]
    """
    common = scores.index.intersection(forward_returns.index)
    if len(common) < 5:
        return float("nan")

    s = scores.loc[common].fillna(0)
    r = forward_returns.loc[common].fillna(0)

    if method == "spearman":
        if _HAS_SCIPY:
            ic, _ = _scipy_stats.spearmanr(s, r)
        else:
            # Pure numpy Spearman: rank then Pearson
            def _rank(x: pd.Series) -> np.ndarray:
                return pd.Series(x).rank().values
            ic = float(np.corrcoef(_rank(s), _rank(r))[0, 1])
    else:
        if _HAS_SCIPY:
            ic, _ = _scipy_stats.pearsonr(s, r)
        else:
            ic = float(np.corrcoef(s.values, r.values)[0, 1])

    return float(ic) if not np.isnan(ic) else float("nan")


def rank_ic_series(
    scores_history: pd.DataFrame,
    returns_history: pd.DataFrame,
    forward_days: int = 21,
) -> pd.Series:
    """
    Compute rolling IC time series.

    Parameters
    ----------
    scores_history : DataFrame
        Columns = [date, ticker, score]. Or wide format with tickers as columns.
    returns_history : DataFrame
        Same shape — [date, ticker, return] or wide.
    forward_days : int
        Forward return horizon.

    Returns
    -------
    Series of IC values indexed by date.
    """
    # Expect wide format: index=date, columns=tickers
    if "date" in scores_history.columns:
        scores_history = scores_history.pivot(index="date", columns="ticker", values="score")
    if "date" in returns_history.columns:
        returns_history = returns_history.pivot(index="date", columns="ticker", values="return")

    scores_history.index = pd.to_datetime(scores_history.index)
    returns_history.index = pd.to_datetime(returns_history.index)

    common_dates = scores_history.index.intersection(
        returns_history.index[forward_days:]
        if len(returns_history) > forward_days else returns_history.index
    )

    ic_vals = {}
    for d in common_dates:
        try:
            scores = scores_history.loc[d].dropna()
            # Forward return: return over next forward_days
            fwd_idx = returns_history.index.get_loc(d) + forward_days
            if fwd_idx >= len(returns_history):
                continue
            fwd_date = returns_history.index[fwd_idx]
            fwd_ret = returns_history.loc[fwd_date].dropna()
            ic = information_coefficient(scores, fwd_ret)
            ic_vals[d] = ic
        except Exception:
            pass

    return pd.Series(ic_vals, name="IC")


def turnover(
    weights_history: pd.DataFrame,
) -> float:
    """
    Average daily one-way turnover.

    Parameters
    ----------
    weights_history : DataFrame
        Wide format: index=date, columns=tickers, values=weights.

    Returns
    -------
    float — average fraction of portfolio turned over per day.
    """
    diffs = weights_history.fillna(0).diff().abs()
    daily_to = diffs.sum(axis=1)
    return float(daily_to.mean()) if len(daily_to) > 1 else 0.0


def annualised_vol(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualised volatility of a return series."""
    return float(returns.std(ddof=1) * np.sqrt(periods_per_year))


def summarise_performance(
    nav: pd.Series,
    returns: Optional[pd.Series] = None,
    benchmark_returns: Optional[pd.Series] = None,
    cost_bps: float = 25.0,
    label: str = "strategy",
) -> dict:
    """
    Compute a full performance summary dict.

    Parameters
    ----------
    nav : Series
        NAV time series.
    returns : Series, optional
        If not provided, derived from nav.
    benchmark_returns : Series, optional
        Benchmark return series for alpha/beta.
    cost_bps : float
        Assumed cost per unit of turnover (not applied here — use in backtest).
    label : str

    Returns
    -------
    dict
    """
    if returns is None:
        returns = nav.pct_change().dropna()

    n_years = len(returns) / 252
    result = {
        "label": label,
        "n_days": len(returns),
        "n_years": round(n_years, 2),
        "cagr": round(cagr(nav), 4),
        "sharpe": round(sharpe_ratio(returns), 3),
        "sortino": round(sortino_ratio(returns), 3),
        "max_drawdown": round(max_drawdown(nav), 4),
        "calmar": round(calmar_ratio(nav), 3),
        "hit_rate": round(hit_rate(returns), 3),
        "annualised_vol": round(annualised_vol(returns), 4),
        "cost_bps_assumption": cost_bps,
    }

    if benchmark_returns is not None:
        common = returns.index.intersection(benchmark_returns.index)
        if len(common) > 30:
            r = returns.loc[common]
            b = benchmark_returns.loc[common]
            cov = np.cov(r, b)
            beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else 1.0
            alpha = float(r.mean() - beta * b.mean()) * 252
            result["beta"] = round(beta, 3)
            result["alpha_annualised"] = round(alpha, 4)

    return result
