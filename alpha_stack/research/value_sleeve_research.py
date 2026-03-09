"""
Alpha Stack — Value Sleeve Research
====================================
Compute IC, factor decay, turnover, and comparative performance metrics
for the Value sleeve.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional, Dict, List, Tuple, Any
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class ValueSleeveResearch:
    """Research and validation for Value sleeve."""

    def __init__(self, fundamentals_store, prices_store):
        self.fundamentals = fundamentals_store
        self.prices = prices_store

    @staticmethod
    def compute_information_coefficient(
        factors: pd.DataFrame,
        returns: pd.Series,
        method: str = "spearman"
    ) -> float:
        """
        Compute Information Coefficient (rank correlation between factor and returns).
        
        Parameters
        ----------
        factors : Series or DataFrame column
            Factor values (earnings yield, etc.)
        returns : Series
            Forward returns (same index)
        method : 'spearman' or 'pearson'
        
        Returns
        -------
        float : IC value (correlation)
        """
        # Align indices
        aligned = pd.DataFrame({"factor": factors, "returns": returns}).dropna()
        
        if len(aligned) < 3:
            return np.nan
        
        if method == "spearman":
            # Spearman: correlation of ranks
            factor_ranks = aligned["factor"].rank()
            return_ranks = aligned["returns"].rank()
            corr = factor_ranks.corr(return_ranks)
        else:
            # Pearson: direct correlation
            corr = aligned["factor"].corr(aligned["returns"])
        
        return corr if not np.isnan(corr) else 0.0

    @staticmethod
    def compute_rank_ic(
        factors: pd.Series,
        returns: pd.Series
    ) -> float:
        """
        Compute Rank IC (Spearman correlation, more robust to outliers).
        """
        return ValueSleeveResearch.compute_information_coefficient(
            factors, returns, method="spearman"
        )

    @staticmethod
    def compute_turnover(
        weights_t0: pd.Series,
        weights_t1: pd.Series
    ) -> float:
        """
        Compute portfolio turnover between two dates.
        
        Turnover = sum(|w_t1 - w_t0|) / 2
        """
        diff = (weights_t1 - weights_t0).abs().sum()
        return diff / 2.0

    @staticmethod
    def factor_decay_analysis(
        factor_values: List[float],
        forward_returns: List[float],
        lag_days: List[int]
    ) -> pd.DataFrame:
        """
        Analyze factor predictiveness over different lags.
        
        Parameters
        ----------
        factor_values : list of factor values at date t
        forward_returns : list of corresponding returns
        lag_days : list of lag periods (days)
        
        Returns
        -------
        DataFrame with columns: lag_days, ic_value, t_stat, significant
        """
        results = []
        
        for lag in lag_days:
            # Typically would regress factor[t] against returns[t+lag]
            # Here simplified: correlation between factor and forward return
            if len(factor_values) > lag:
                factor_subset = factor_values[:-lag]
                return_subset = forward_returns[lag:]
                
                ic = ValueSleeveResearch.compute_rank_ic(
                    pd.Series(factor_subset),
                    pd.Series(return_subset)
                )
                results.append({
                    "lag_days": lag,
                    "ic": ic,
                    "significant": abs(ic) > 0.1,  # Rough heuristic
                })
        
        return pd.DataFrame(results)

    @staticmethod
    def regime_conditional_performance(
        sleeve_returns: pd.Series,
        trend_state: pd.Series,
    ) -> Dict[str, Any]:
        """
        Analyze Value sleeve performance conditioned on trend regime.
        
        Parameters
        ----------
        sleeve_returns : Series of daily sleeve returns
        trend_state : Series of trend state ('strong_up', 'weak_up', 'neutral', etc.)
        
        Returns
        -------
        Dict with Sharpe, max_DD, CAGR per regime
        """
        result = {}
        
        for state in trend_state.unique():
            if pd.isna(state):
                continue
            
            mask = trend_state == state
            subset_returns = sleeve_returns[mask]
            
            if len(subset_returns) < 2:
                result[state] = {
                    "count": len(subset_returns),
                    "sharpe": np.nan,
                    "max_dd": np.nan,
                    "mean_ret": np.nan,
                }
                continue
            
            # Compute metrics
            mean_ret = subset_returns.mean() * 252
            vol = subset_returns.std() * np.sqrt(252)
            sharpe = mean_ret / vol if vol > 0 else 0
            
            # Max drawdown
            cum = (1 + subset_returns).cumprod()
            running_max = cum.expanding().max()
            dd = (cum - running_max) / running_max
            max_dd = dd.min()
            
            result[state] = {
                "count": len(subset_returns),
                "sharpe": round(sharpe, 3),
                "max_dd": round(max_dd, 4),
                "mean_ret": round(mean_ret, 4),
                "vol": round(vol, 4),
            }
        
        return result

    @staticmethod
    def sector_concentration(
        candidates: pd.DataFrame,
        sector_col: str = "sector"
    ) -> Dict[str, Any]:
        """
        Analyze sector concentration of Value sleeve candidates.
        
        Returns dict with:
            n_sectors: number of sectors represented
            sector_breakdown: {sector: count, pct}
            hhi: Herfindahl-Hirschman Index (concentration measure)
        """
        if candidates.empty or sector_col not in candidates.columns:
            return {
                "n_sectors": 0,
                "sector_breakdown": {},
                "hhi": np.nan,
            }
        
        sector_counts = candidates[sector_col].value_counts()
        total = len(candidates)
        
        breakdown = {}
        hhi = 0.0
        
        for sector, count in sector_counts.items():
            pct = count / total
            breakdown[sector] = {
                "count": int(count),
                "pct": round(pct * 100, 2),
            }
            hhi += pct ** 2
        
        return {
            "n_sectors": len(breakdown),
            "sector_breakdown": breakdown,
            "hhi": round(hhi, 4),
        }


class ComparativeBacktestAnalysis:
    """Compute metrics for Trend-only, Value-only, and Combined backtests."""

    @staticmethod
    def compute_returns_series(nav: pd.Series) -> pd.Series:
        """Convert NAV series to daily returns."""
        return nav.pct_change().fillna(0)

    @staticmethod
    def compute_benchmark_returns(prices: pd.Series) -> pd.Series:
        """Compute benchmark (SPY) returns."""
        return prices.pct_change().fillna(0)

    @staticmethod
    def performance_summary(
        nav_series: pd.Series,
        benchmark_nav: pd.Series,
        risk_free_rate: float = 0.04
    ) -> Dict[str, float]:
        """
        Compute standard backtest metrics.
        
        Returns dict with: total_return, cagr, sharpe, sortino, max_dd, 
                          ir, beta, alpha, win_rate
        """
        returns = ComparativeBacktestAnalysis.compute_returns_series(nav_series)
        bench_returns = ComparativeBacktestAnalysis.compute_returns_series(benchmark_nav)
        
        # Align
        aligned = pd.DataFrame({
            "strat": returns,
            "bench": bench_returns
        }).dropna()
        
        if len(aligned) < 5:
            return {"error": "Insufficient data"}
        
        strat_ret = aligned["strat"]
        bench_ret = aligned["bench"]
        
        # Total return
        total_ret = (nav_series[-1] / nav_series[0] - 1) * 100
        
        # CAGR
        days = (nav_series.index[-1] - nav_series.index[0]).days
        years = days / 365.25
        cagr = ((nav_series[-1] / nav_series[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
        
        # Sharpe
        mean_ret = strat_ret.mean() * 252
        vol = strat_ret.std() * np.sqrt(252)
        sharpe = (mean_ret - risk_free_rate) / vol if vol > 0 else 0
        
        # Sortino (using 0 as MAR)
        downside_returns = strat_ret[strat_ret < 0]
        downside_vol = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0
        sortino = mean_ret / downside_vol if downside_vol > 0 else 0
        
        # Max drawdown
        cum = (1 + strat_ret).cumprod()
        running_max = cum.expanding().max()
        dd = (cum - running_max) / running_max
        max_dd = dd.min() * 100
        
        # Information Ratio (vs benchmark)
        excess_ret = strat_ret - bench_ret
        ir = excess_ret.mean() * 252 / (excess_ret.std() * np.sqrt(252)) if excess_ret.std() > 0 else 0
        
        # Beta, Alpha
        covariance = aligned.cov().iloc[0, 1]
        bench_var = bench_ret.var()
        beta = covariance / bench_var if bench_var > 0 else 0
        alpha = (mean_ret - risk_free_rate) - beta * (bench_ret.mean() * 252 - risk_free_rate)
        
        # Win rate
        win_rate = (strat_ret > bench_ret).sum() / len(aligned) * 100
        
        return {
            "total_return_pct": round(total_ret, 2),
            "cagr_pct": round(cagr, 2),
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "max_dd_pct": round(max_dd, 2),
            "information_ratio": round(ir, 3),
            "beta": round(beta, 3),
            "alpha_pct": round(alpha * 100, 2),
            "win_rate_pct": round(win_rate, 2),
        }
