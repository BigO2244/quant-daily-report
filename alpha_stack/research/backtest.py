"""
Alpha Stack — Backtest Engine
================================
Point-in-time safe backtest harness for Alpha Stack v1.

Usage:
    from alpha_stack.research.backtest import AlphaStackBacktest
    bt = AlphaStackBacktest()
    result = bt.run(start_date="2022-01-01", end_date="2024-12-31")
    print(result["summary"])

Architecture:
    1. Load universe.
    2. Download price history (PIT-safe).
    3. For each trading date:
        a. Compute trend features.
        b. Compute volatility features.
        c. (Optional) Compute MR signals.
        d. Classify regime.
        e. Run active sleeves.
        f. Allocate portfolio.
        g. Compute daily P&L.
    4. Run attribution.
    5. Persist outputs to outputs/alpha_stack/.

PRODUCTION SAFETY: This module does NOT import from any production
execution path. It uses only Alpha Stack internal modules.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from alpha_stack._config_loader import get_section, get_flag
from alpha_stack.datastore.prices import PricesDataStore
from alpha_stack.datastore.macro import MacroDataStore
from alpha_stack.datastore.breadth import BreadthDataStore
from alpha_stack.features.trend import compute_trend_features
from alpha_stack.features.volatility import compute_volatility_features
from alpha_stack.regime.context import RegimeEngine
from alpha_stack.sleeves.registry import SleeveRegistry
from alpha_stack.portfolio.allocator import AlphaStackAllocator
from alpha_stack.research.attribution import AttributionEngine
from alpha_stack.research.metrics import summarise_performance

logger = logging.getLogger(__name__)


class AlphaStackBacktest:
    """
    Alpha Stack v1 backtest engine.

    Parameters
    ----------
    universe_path : str, optional
        Path to universe CSV. Defaults to data/universe.csv.
    output_dir : str, optional
        Where to write backtest outputs. Defaults to outputs/alpha_stack.
    initial_equity : float
        Starting capital (default 100,000).
    rebalance_frequency : str
        "daily" (default) or "weekly".
    """

    def __init__(
        self,
        universe_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        initial_equity: float = 100_000.0,
        rebalance_frequency: str = "daily",
    ) -> None:
        research_cfg = get_section("research") or {}
        self._universe_path = universe_path or (
            (get_section("universe") or {}).get("csv_path", "data/universe.csv")
        )
        self._output_dir = Path(output_dir or research_cfg.get("output_dir", "outputs/alpha_stack"))
        self._initial_equity = initial_equity
        self._rebalance_freq = rebalance_frequency

        # Data stores
        self._prices = PricesDataStore()
        self._macro = MacroDataStore(prices_store=self._prices)
        self._breadth = BreadthDataStore(prices_store=self._prices)

        # Engine components
        self._regime_engine = RegimeEngine(
            macro_store=self._macro,
            breadth_store=self._breadth,
        )
        self._registry = SleeveRegistry()
        self._allocator = AlphaStackAllocator()
        self._attribution = AttributionEngine()

    # ------------------------------------------------------------------ #
    # Main entry point                                                     #
    # ------------------------------------------------------------------ #

    def run(
        self,
        start_date: date | str,
        end_date: date | str,
        initial_equity: Optional[float] = None,
        verbose: bool = True,
    ) -> dict:
        """
        Run the backtest and return results.

        Parameters
        ----------
        start_date : date or str (YYYY-MM-DD)
        end_date   : date or str (YYYY-MM-DD)
        initial_equity : float, optional
        verbose : bool

        Returns
        -------
        dict with keys:
            summary, nav, returns, regime_history, sleeve_history,
            attribution, output_dir
        """
        if not get_flag("ENABLE_ALPHA_STACK", default=False):
            logger.warning(
                "[BACKTEST] ENABLE_ALPHA_STACK=false. "
                "Set to true in alpha_stack.yaml to run backtests."
            )

        if isinstance(start_date, str):
            start_date = pd.Timestamp(start_date).date()
        if isinstance(end_date, str):
            end_date = pd.Timestamp(end_date).date()

        equity = initial_equity or self._initial_equity
        universe = self._load_universe()
        tickers = universe["ticker"].tolist()
        sector_map = dict(zip(universe["ticker"], universe.get("sector", ["Unknown"] * len(universe))))

        logger.info(
            "[BACKTEST] Starting: %s → %s | %d tickers | initial_equity=%s",
            start_date, end_date, len(tickers), equity,
        )

        # Download full price history (PIT-safe: end=end_date)
        lookback_start = start_date - timedelta(days=300 + 21)  # EMA warmup + momentum
        logger.info("[BACKTEST] Downloading prices %s → %s...", lookback_start, end_date)
        prices_df = self._prices.get_prices_multi(tickers, lookback_start, end_date)

        if prices_df.empty:
            logger.error("[BACKTEST] No price data; aborting.")
            return {"error": "No price data available for the requested range."}

        # Get trading dates in range
        trading_dates = self._get_trading_dates(prices_df, start_date, end_date)
        logger.info("[BACKTEST] %d trading dates in range.", len(trading_dates))

        # Main loop
        nav_series = {}
        returns_series = {}
        regime_records = []
        allocation_history: Dict[str, dict] = {}
        weights_history: Dict[str, Dict[pd.Timestamp, pd.Series]] = {}
        current_weights: pd.Series = pd.Series(dtype=float)
        current_equity = equity

        for i, dt in enumerate(trading_dates):
            dt_str = str(dt)[:10]

            # PIT: only history up to dt
            prices_pit = prices_df[prices_df["date"] <= dt].copy()

            # Feature computation
            trend_feats = compute_trend_features(prices_pit, dt)
            vol_feats = compute_volatility_features(prices_pit, dt)

            if trend_feats.empty:
                logger.debug("[BACKTEST] No trend features on %s; skipping.", dt_str)
                nav_series[dt] = current_equity
                continue

            # Merge features
            feats = trend_feats.merge(vol_feats, on="ticker", how="left")
            # Add sector
            feats["sector"] = feats["ticker"].map(sector_map).fillna("Unknown")

            # Regime classification
            ctx = self._regime_engine.classify(dt_str)
            regime_records.append(ctx.to_dict())

            # Run sleeves
            active = self._registry.active_sleeves()
            sleeve_outputs = {}
            for sleeve in active:
                try:
                    out = sleeve.run(feats, ctx, risk_budget=1.0, as_of_date=dt_str)
                    sleeve_outputs[sleeve.name] = out
                except Exception as exc:
                    logger.warning("[BACKTEST] Sleeve %s error on %s: %s", sleeve.name, dt_str, exc)

            # Allocate
            current_dd = self._compute_drawdown(nav_series, equity)
            alloc = self._allocator.allocate(
                sleeve_outputs, ctx, sector_map=sector_map, current_dd=current_dd
            )

            # Convert allocation to weights dict
            new_weights: pd.Series = pd.Series(dtype=float)
            if not alloc.target_book.empty and "weight" in alloc.target_book.columns:
                tb = alloc.target_book.set_index("ticker")["weight"]
                new_weights = tb[tb > 0]

            allocation_history[dt_str] = alloc.sleeve_budgets

            # Update weights history per sleeve
            if not alloc.target_book.empty:
                for sleeve_name in alloc.sleeve_budgets:
                    sleeve_tickers = alloc.target_book[
                        alloc.target_book.get("sleeve", pd.Series()) == sleeve_name
                    ]
                    if not sleeve_tickers.empty and "weight" in sleeve_tickers.columns:
                        if sleeve_name not in weights_history:
                            weights_history[sleeve_name] = {}
                        weights_history[sleeve_name][dt] = sleeve_tickers.set_index("ticker")["weight"]

            # Compute P&L (if we have previous weights)
            if i > 0 and not current_weights.empty:
                pnl = self._compute_daily_pnl(
                    current_weights, prices_pit, dt, trading_dates[i - 1]
                )
                current_equity *= (1 + pnl)
                returns_series[dt] = pnl
            else:
                returns_series[dt] = 0.0

            nav_series[dt] = current_equity
            current_weights = new_weights

            if verbose and i % 50 == 0:
                logger.info(
                    "[BACKTEST] %s | equity=%.0f | regime=%s | n_pos=%d",
                    dt_str, current_equity, ctx.trend_state.value, len(new_weights),
                )

        # --- Results ---
        nav = pd.Series(nav_series, name="nav")
        returns = pd.Series(returns_series, name="return").fillna(0)

        summary = summarise_performance(nav, returns=returns, label="alpha_stack_backtest")

        # Regime history DataFrame
        regime_df = pd.DataFrame(regime_records)

        # Attribution
        attr_result = {}
        if weights_history:
            wh_dfs = {}
            for sname, wh in weights_history.items():
                if wh:
                    wh_dfs[sname] = pd.DataFrame(wh).T.fillna(0)

            # Price returns
            price_ret = prices_df.pivot(index="date", columns="ticker", values="close").pct_change()
            price_ret.index = pd.to_datetime(price_ret.index)

            if wh_dfs:
                attr_result = self._attribution.full_attribution(
                    wh_dfs, price_ret,
                    regime_history=regime_df,
                )

        # Persist results
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output = {
            "summary": summary,
            "nav": nav,
            "returns": returns,
            "regime_history": regime_df,
            "attribution": attr_result,
            "output_dir": str(self._output_dir),
        }

        self._persist(output, start_date, end_date)
        logger.info("[BACKTEST] Complete. CAGR=%.1f%% Sharpe=%.2f MaxDD=%.1f%%",
                    summary.get("cagr", 0) * 100,
                    summary.get("sharpe", 0),
                    summary.get("max_drawdown", 0) * 100)

        return output

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _load_universe(self) -> pd.DataFrame:
        """Load universe CSV."""
        try:
            df = pd.read_csv(self._universe_path)
            col_map = {c.lower(): c for c in df.columns}
            if "ticker" not in df.columns and "symbol" in col_map:
                df = df.rename(columns={col_map["symbol"]: "ticker"})
            return df.dropna(subset=["ticker"])
        except Exception as exc:
            logger.error("[BACKTEST] Cannot load universe from %s: %s", self._universe_path, exc)
            return pd.DataFrame(columns=["ticker"])

    @staticmethod
    def _get_trading_dates(
        prices_df: pd.DataFrame,
        start_date: date,
        end_date: date,
    ) -> List[pd.Timestamp]:
        """Extract trading dates from price data."""
        dates = pd.to_datetime(prices_df["date"]).unique()
        dates = sorted(d for d in dates
                       if pd.Timestamp(start_date) <= d <= pd.Timestamp(end_date))
        return dates

    @staticmethod
    def _compute_drawdown(nav_series: dict, initial_equity: float) -> float:
        """Compute current drawdown from peak."""
        if not nav_series:
            return 0.0
        nav_vals = list(nav_series.values())
        peak = max(nav_vals + [initial_equity])
        current = nav_vals[-1]
        return max(0.0, (peak - current) / peak)

    def _compute_daily_pnl(
        self,
        weights: pd.Series,
        prices: pd.DataFrame,
        current_date: pd.Timestamp,
        prev_date: pd.Timestamp,
    ) -> float:
        """Compute weighted daily P&L from prev_date to current_date."""
        try:
            prices["date"] = pd.to_datetime(prices["date"])
            snap = prices.groupby("ticker")["close"].last()

            prev = prices[prices["date"] <= pd.Timestamp(prev_date)]
            prev_snap = prev.groupby("ticker")["close"].last()

            tickers = weights.index
            common = tickers.intersection(snap.index).intersection(prev_snap.index)
            if len(common) == 0:
                return 0.0

            w = weights.loc[common]
            r = (snap.loc[common] / prev_snap.loc[common] - 1).fillna(0)
            pnl = float((w * r).sum())
            return pnl
        except Exception as exc:
            logger.debug("[BACKTEST] PnL computation error: %s", exc)
            return 0.0

    def _persist(self, output: dict, start_date, end_date) -> None:
        """Persist key results to disk."""
        try:
            run_id = f"{start_date}_{end_date}"
            run_dir = self._output_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            # Summary JSON
            with open(run_dir / "summary.json", "w") as fh:
                json.dump(output["summary"], fh, indent=2)

            # NAV CSV
            if isinstance(output["nav"], pd.Series):
                output["nav"].to_csv(run_dir / "nav.csv", header=["nav"])

            # Regime CSV
            if isinstance(output["regime_history"], pd.DataFrame):
                output["regime_history"].to_csv(run_dir / "regime_history.csv", index=False)

            logger.info("[BACKTEST] Results persisted to %s", run_dir)
        except Exception as exc:
            logger.warning("[BACKTEST] Could not persist results: %s", exc)
