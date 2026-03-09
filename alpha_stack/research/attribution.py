"""
Alpha Stack — Attribution Framework
======================================
Sleeve-level return attribution, turnover, IC monitoring,
regime-conditioned performance, and cost model.

Key outputs:
    sleeve_returns()        — daily P&L per sleeve
    sleeve_turnover()       — daily turnover per sleeve
    sleeve_correlations()   — rolling correlation matrix between sleeve returns
    ic_series()             — Information Coefficient time series
    regime_attribution()    — performance broken down by regime state
    net_returns()           — gross returns minus cost model
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from alpha_stack.research.metrics import (
    sharpe_ratio, max_drawdown, cagr, turnover, information_coefficient,
    summarise_performance,
)
from alpha_stack._config_loader import get_section

logger = logging.getLogger(__name__)


class AttributionEngine:
    """
    Attribution engine for Alpha Stack backtests.

    Parameters
    ----------
    cost_bps : float
        Round-trip cost assumption in basis points (default 25bps).
    commission_bps : float
        Commission assumption (default 1bps).
    """

    def __init__(
        self,
        cost_bps: float = 25.0,
        commission_bps: float = 1.0,
    ) -> None:
        research_cfg = get_section("research") or {}
        cost_cfg = research_cfg.get("cost_model", {})
        self._slippage_bps = float(cost_cfg.get("slippage_bps", cost_bps))
        self._commission_bps = float(cost_cfg.get("commission_bps", commission_bps))

    @property
    def total_cost_bps(self) -> float:
        return self._slippage_bps + self._commission_bps

    # ------------------------------------------------------------------ #
    # Sleeve returns                                                       #
    # ------------------------------------------------------------------ #

    def sleeve_returns(
        self,
        weights_history: Dict[str, pd.DataFrame],
        price_returns: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Compute daily returns per sleeve.

        Parameters
        ----------
        weights_history : dict
            {sleeve_name: DataFrame (wide: index=date, columns=tickers, values=weights)}
        price_returns : DataFrame
            Wide: index=date, columns=tickers, values=daily_return.

        Returns
        -------
        DataFrame: index=date, columns=sleeve_names + ['total']
        """
        sleeve_rets = {}

        for name, weights in weights_history.items():
            weights_df = weights.copy().fillna(0)
            common_cols = weights_df.columns.intersection(price_returns.columns)
            if len(common_cols) == 0:
                logger.warning("[ATTRIBUTION] No overlap for sleeve %s", name)
                continue
            common_idx = weights_df.index.intersection(price_returns.index)
            w = weights_df.loc[common_idx, common_cols]
            r = price_returns.loc[common_idx, common_cols]
            sleeve_rets[name] = (w * r).sum(axis=1)

        if not sleeve_rets:
            return pd.DataFrame()

        df = pd.DataFrame(sleeve_rets)
        df["total"] = df.sum(axis=1)
        return df

    # ------------------------------------------------------------------ #
    # Sleeve turnover                                                      #
    # ------------------------------------------------------------------ #

    def sleeve_turnover(
        self,
        weights_history: Dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """
        Compute daily one-way turnover per sleeve.

        Returns
        -------
        DataFrame: index=date, columns=sleeve_names + ['total']
        """
        sleeve_to = {}
        for name, weights in weights_history.items():
            diffs = weights.fillna(0).diff().abs()
            sleeve_to[name] = diffs.sum(axis=1)

        if not sleeve_to:
            return pd.DataFrame()

        df = pd.DataFrame(sleeve_to)
        df["total"] = df.sum(axis=1)
        return df

    # ------------------------------------------------------------------ #
    # Cost model                                                           #
    # ------------------------------------------------------------------ #

    def apply_costs(
        self,
        gross_returns: pd.Series,
        turnover_series: pd.Series,
    ) -> pd.Series:
        """
        Apply a simple cost model: net_return = gross_return - cost.
        cost = turnover * total_cost_bps / 10000

        Parameters
        ----------
        gross_returns : Series — daily gross returns
        turnover_series : Series — daily one-way turnover

        Returns
        -------
        Series of net returns.
        """
        cost = turnover_series * (self.total_cost_bps / 10000)
        return gross_returns - cost

    # ------------------------------------------------------------------ #
    # Sleeve correlations                                                  #
    # ------------------------------------------------------------------ #

    def sleeve_correlations(
        self,
        sleeve_returns: pd.DataFrame,
        window: int = 126,
    ) -> pd.DataFrame:
        """
        Return the median rolling correlation matrix across sleeves.

        Parameters
        ----------
        sleeve_returns : DataFrame — index=date, columns=sleeves
        window : int — rolling window in days

        Returns
        -------
        DataFrame — correlation matrix (static median over the whole period)
        """
        cols = [c for c in sleeve_returns.columns if c != "total"]
        if len(cols) < 2:
            return pd.DataFrame()
        return sleeve_returns[cols].rolling(window).corr().groupby(level=-1).median()

    # ------------------------------------------------------------------ #
    # IC series                                                            #
    # ------------------------------------------------------------------ #

    def ic_series(
        self,
        scores_history: pd.DataFrame,
        forward_returns: pd.DataFrame,
        forward_days: int = 21,
        sleeve_name: str = "trend",
    ) -> pd.Series:
        """
        Compute Information Coefficient time series for a sleeve.

        Parameters
        ----------
        scores_history : DataFrame
            Wide format (date × ticker) OR long format (date, ticker, score).
        forward_returns : DataFrame
            Same shape — forward price returns.
        forward_days : int
        sleeve_name : str

        Returns
        -------
        Series indexed by date.
        """
        from alpha_stack.research.metrics import rank_ic_series
        return rank_ic_series(scores_history, forward_returns, forward_days)

    # ------------------------------------------------------------------ #
    # Regime-conditioned attribution                                       #
    # ------------------------------------------------------------------ #

    def regime_attribution(
        self,
        returns: pd.Series,
        regime_history: pd.DataFrame,
        dimension: str = "trend_state",
    ) -> pd.DataFrame:
        """
        Break down performance metrics by regime state.

        Parameters
        ----------
        returns : Series — daily strategy returns, indexed by date
        regime_history : DataFrame — output of RegimeEngine.classify_history()
        dimension : str — column in regime_history to group by

        Returns
        -------
        DataFrame indexed by regime state with columns:
            n_days, cagr_approx, sharpe, max_drawdown, avg_daily_return
        """
        regime_history = regime_history.copy()
        regime_history["date"] = pd.to_datetime(regime_history["date"])
        regime_history = regime_history.set_index("date")

        returns.index = pd.to_datetime(returns.index)
        common = returns.index.intersection(regime_history.index)

        if len(common) == 0:
            logger.warning("[ATTRIBUTION] No common dates for regime attribution.")
            return pd.DataFrame()

        merged = pd.DataFrame({"return": returns.loc[common]})
        merged[dimension] = regime_history.loc[common, dimension]

        results = []
        for state, group in merged.groupby(dimension):
            r = group["return"]
            nav = (1 + r).cumprod()
            results.append({
                "regime_state": state,
                "n_days": len(r),
                "avg_daily_return": round(float(r.mean()), 6),
                "sharpe": round(sharpe_ratio(r), 3),
                "max_drawdown": round(max_drawdown(nav), 4),
                "hit_rate": round(float((r > 0).mean()), 3),
            })

        return pd.DataFrame(results).set_index("regime_state")

    # ------------------------------------------------------------------ #
    # Full summary                                                         #
    # ------------------------------------------------------------------ #

    def full_attribution(
        self,
        weights_history: Dict[str, pd.DataFrame],
        price_returns: pd.DataFrame,
        regime_history: Optional[pd.DataFrame] = None,
        benchmark_returns: Optional[pd.Series] = None,
    ) -> dict:
        """
        Compute comprehensive attribution: returns, costs, regime, correlations.

        Returns
        -------
        dict with keys: summary, sleeve_stats, regime_stats, correlations
        """
        s_rets = self.sleeve_returns(weights_history, price_returns)
        s_to = self.sleeve_turnover(weights_history)

        if s_rets.empty:
            return {"error": "No sleeve returns computed."}

        gross_total = s_rets.get("total", pd.Series())
        to_total = s_to.get("total", pd.Series()) if not s_to.empty else pd.Series(0, index=gross_total.index)

        net_total = self.apply_costs(gross_total, to_total)
        nav = (1 + net_total.fillna(0)).cumprod()

        summary = summarise_performance(
            nav, returns=net_total,
            benchmark_returns=benchmark_returns,
            cost_bps=self.total_cost_bps,
            label="alpha_stack",
        )
        summary["avg_turnover"] = round(float(to_total.mean()), 4)
        summary["estimated_cost_drag_bps"] = round(float(to_total.mean()) * self.total_cost_bps, 2)

        # Per-sleeve stats
        sleeve_stats = {}
        for col in [c for c in s_rets.columns if c != "total"]:
            r = s_rets[col].dropna()
            if len(r) < 10:
                continue
            sleeve_nav = (1 + r).cumprod()
            sleeve_stats[col] = {
                "sharpe": round(sharpe_ratio(r), 3),
                "max_drawdown": round(max_drawdown(sleeve_nav), 4),
                "avg_turnover": round(float(s_to.get(col, pd.Series([0])).mean()), 4),
            }

        result = {
            "summary": summary,
            "sleeve_stats": sleeve_stats,
        }

        if regime_history is not None:
            result["regime_stats"] = self.regime_attribution(net_total, regime_history).to_dict()

        if len(s_rets.columns) > 2:
            corr_df = self.sleeve_correlations(s_rets)
            if not corr_df.empty:
                result["correlations"] = corr_df.to_dict()

        return result
