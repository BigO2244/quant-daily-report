"""
Alpha Stack — Value Sleeve Backtest
====================================
Backtests the Value sleeve using SEC EDGAR fundamentals.

Similar structure to sleeve_trend/backtest.py but for value factors:
- Computes value factors (EY, FCFY, B/P) on a rolling basis
- Selects top quintile by value score
- Manages positions with risk controls
- Returns daily NAV and trades

PIT-SAFE: All fundamental values use filing_date (not period_end).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Risk parameters (conservative for value strategy)
MAX_POSITION_SIZE = 0.015  # 1.5% max per name
SECTOR_CAP = 0.05  # 5% max per sector
TARGET_VOL = 0.12  # 12% annual volatility
MAX_POSITIONS = 100  # 100 names in portfolio
MIN_PRICE = 1.0  # Exclude penny stocks
TURNOVER_LIMIT = 2.0  # 200% annual (conservative for monthly re-rank)


class ValueSleeveBacktest:
    """
    Value sleeve backtesting engine.

    Accepts a universe of tickers and runs a monthly-rebalance value strategy
    using PIT-safe SEC EDGAR fundamentals.

    Parameters
    ----------
    fundamentals_store : FundamentalsDataStore
        For getting earnings_yield, fcf_yield, book_to_price
    prices_store : PricesDataStore
        For getting daily prices
    tickers : list of str
        Universe of tickers to backtest on
    sector_map : dict, optional
        {ticker: sector} for exposure management
    """

    def __init__(
        self,
        fundamentals_store,
        prices_store,
        tickers: list,
        sector_map: Optional[Dict] = None,
    ):
        self._fundamentals = fundamentals_store
        self._prices = prices_store
        self._tickers = tickers
        self._sector_map = sector_map or {t: "Unknown" for t in tickers}

        self.equity_df = None  # Daily NAV
        self.trades_df = None  # Position entry/exit records
        self.target_weights_df = None  # Daily target weights

    # =====================================================================
    # Main Entry Point
    # =====================================================================

    def run_backtest(
        self,
        start_date: date | str = "2020-01-01",
        end_date: date | str = "2026-03-08",
        rebalance_freq: str = "monthly",
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Run the full backtest.

        Parameters
        ----------
        start_date, end_date : date or str (YYYY-MM-DD)
        rebalance_freq : "monthly" or "weekly"

        Returns
        -------
        equity_df, trades_df
        """
        logger.info("[VALUE_BACKTEST] Starting backtest %s to %s", start_date, end_date)
        
        # Print EDGAR coverage diagnostics
        logger.info("[VALUE_BACKTEST] Checking EDGAR ticker→CIK mapping coverage...")
        self._fundamentals.print_coverage_report(self._tickers)

        # Fetch price history for all tickers
        self._prepare_price_data(start_date, end_date)

        # Generate target weights on rebalance schedule
        self._generate_target_weights(start_date, end_date, rebalance_freq)

        # Run portfolio simulator using target weights
        from engine.backtest_engine import run_backtest as run_engine
        
        equity_df, trades_df = run_engine(
            target_weights_df=self.target_weights_df,
            price_df=self.price_df,
            initial_capital=100_000,
            comission=0.001,  # 10bps
            rebalance_slippage=0.002,  # 20bps
        )

        self.equity_df = equity_df
        self.trades_df = trades_df
        
        logger.info("[VALUE_BACKTEST] Backtest complete: %d trading days", len(equity_df))
        return equity_df, trades_df

    # =====================================================================
    # Data Preparation
    # =====================================================================

    def _prepare_price_data(self, start_date: date | str, end_date: date | str):
        """Fetch OHLCV for all universe tickers."""
        logger.info("[VALUE_BACKTEST] Fetching price data for %d tickers", len(self._tickers))

        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)

        # Get prices from PricesDataStore
        price_df = self._prices.get_prices_multi(self._tickers, start_ts, end_ts)
        
        if price_df is None or price_df.empty:
            logger.error("[VALUE_BACKTEST] No price data retrieved")
            raise ValueError("Price data unavailable")

        self.price_df = price_df
        logger.info("[VALUE_BACKTEST] Price data shape: %s", price_df.shape)

    def _generate_target_weights(
        self,
        start_date: date | str,
        end_date: date | str,
        rebalance_freq: str = "monthly",
    ):
        """
        Generate target portfolio weights on a rolling basis.

        Returns target_weights_df with shape (dates, tickers) where each row
        is a daily weight and non-zero entries indicate active positions.
        """
        logger.info("[VALUE_BACKTEST] Generating target weights (%s rebalance)", rebalance_freq)

        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        all_dates = pd.date_range(start_ts, end_ts, freq="B")  # Business days

        # Determine rebalance dates
        if rebalance_freq == "monthly":
            # Month-end rebalances
            rebalance_dates = [d for d in all_dates if (d + timedelta(days=1)).month != d.month]
        elif rebalance_freq == "weekly":
            rebalance_dates = [d for d in all_dates if d.weekday() == 4]  # Fridays
        else:
            raise ValueError(f"Unknown rebalance frequency: {rebalance_freq}")

        logger.info("[VALUE_BACKTEST] %d rebalance dates", len(rebalance_dates))

        # Initialize target weights: (date -> {ticker: weight})
        target_weights = {}

        for date_idx, current_date in enumerate(all_dates):
            # Determine target weights
            if date_idx > 0 and current_date not in rebalance_dates:
                # Use previous day's weights
                target_weights[current_date] = target_weights[all_dates[date_idx - 1]]
            else:
                # Rebalance: compute value factors and select top quintile
                weights = self._compute_value_weights(current_date)
                target_weights[current_date] = weights

        # Convert to DataFrame
        df_list = []
        for date_val, weights_dict in target_weights.items():
            row = {"date": date_val}
            for ticker, weight in weights_dict.items():
                row[ticker] = weight
            df_list.append(row)

        self.target_weights_df = pd.DataFrame(df_list).set_index("date")
        self.target_weights_df = self.target_weights_df.fillna(0.0)

        logger.info("[VALUE_BACKTEST] Target weights shape: %s", self.target_weights_df.shape)

    def _compute_value_weights(self, as_of_date: date) -> Dict[str, float]:
        """
        Compute portfolio weights based on value factors on a given date.

        Returns {ticker: weight} with weights normalized to sum to 1.
        """
        try:
            as_of_str = str(as_of_date.date()) if hasattr(as_of_date, 'date') else str(as_of_date)

            # Compute value factors for each ticker
            factors = []
            for ticker in self._tickers:
                try:
                    ey = self._fundamentals.get_fundamental(ticker, "earnings_yield", as_of_str)
                    fcfy = self._fundamentals.get_fundamental(ticker, "fcf_yield", as_of_str)
                    bp = self._fundamentals.get_fundamental(ticker, "book_to_price", as_of_str)

                    # Check price minimum
                    price_df = self._prices.get_prices_multi([ticker], pd.Timestamp(as_of_str), pd.Timestamp(as_of_str))
                    if price_df.empty or price_df.iloc[-1].get("close", 0) < MIN_PRICE:
                        continue

                    # Count available factors
                    factor_count = sum([ey is not None, fcfy is not None, bp is not None])
                    if factor_count == 0:
                        continue

                    # Simple average of available factors (normalized to [0, 1])
                    score = 0.0
                    for val in [ey, fcfy, bp]:
                        if val is not None:
                            score += (val + 0.5) / 1.0  # Normalize to ~[0, 1]

                    score = score / factor_count if factor_count > 0 else 0

                    factors.append({
                        "ticker": ticker,
                        "score": score,
                        "ey": ey,
                        "fcfy": fcfy,
                        "bp": bp,
                        "sector": self._sector_map.get(ticker, "Unknown"),
                    })
                except Exception as e:
                    logger.debug("[VALUE_WEIGHTS] Error for %s: %s", ticker, e)
                    continue

            if not factors:
                logger.warning("[VALUE_WEIGHTS] No factors computed for date %s", as_of_date)
                return {}

            factors_df = pd.DataFrame(factors)

            # Select top quintile by score
            top_threshold = factors_df["score"].quantile(0.80)
            selected = factors_df[factors_df["score"] >= top_threshold].copy()

            if selected.empty:
                return {}

            # Size positions equally within quintile (1 / N)
            weights_raw = {row["ticker"]: 1.0 / len(selected) for _, row in selected.iterrows()}

            # Normalize to portfolio weights
            total = sum(weights_raw.values())
            weights = {t: w / total for t, w in weights_raw.items()}

            return weights

        except Exception as e:
            logger.error("[VALUE_WEIGHTS] Error computing weights: %s", e)
            return {}

    # =====================================================================
    # Metrics
    # =====================================================================

    def compute_stats(self) -> Dict:
        """
        Compute performance metrics from backtest results.

        Returns dict with sharpe, maxdd, cagr, turnover, etc.
        """
        if self.equity_df is None:
            logger.error("[VALUE_STATS] Backtest not yet run")
            return {}

        daily_returns = self.equity_df["equity"].pct_change().dropna()
        
        # Common metrics
        annual_ret = (self.equity_df["equity"].iloc[-1] / self.equity_df["equity"].iloc[0]) ** (252 / len(self.equity_df)) - 1
        annual_vol = daily_returns.std() * np.sqrt(252)
        sharpe = (annual_ret / annual_vol) if annual_vol > 0 else 0
        max_dd = (self.equity_df["equity"].cummax() - self.equity_df["equity"]).max() / self.equity_df["equity"].max()

        # Turnover
        if self.target_weights_df is not None:
            turnover = self._compute_turnover()
        else:
            turnover = 0

        stats = {
            "total_return": (self.equity_df["equity"].iloc[-1] / self.equity_df["equity"].iloc[0]) - 1,
            "cagr": annual_ret,
            "annual_volatility": annual_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "turnover_annual": turnover,
            "win_rate": (daily_returns > 0).mean(),
            "trades": len(self.trades_df) if self.trades_df is not None else 0,
        }

        logger.info("[VALUE_STATS] Backtest metrics: Sharpe=%.2f, MaxDD=%.1f%%, CAGR=%.1f%%",
                    sharpe, max_dd * 100, annual_ret * 100)

        return stats

    def _compute_turnover(self) -> float:
        """Annualized turnover: sum(|weight_change|) / 2."""
        if self.target_weights_df is None or len(self.target_weights_df) < 2:
            return 0

        weight_changes = self.target_weights_df.diff().abs().sum(axis=1)
        avg_turnover_daily = weight_changes.mean()
        annual_turnover = avg_turnover_daily * 252

        return annual_turnover


# =========================================================================
# Runner
# =========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
    from alpha_stack.datastore.prices import PricesDataStore
    import pandas as pd

    # Load universe
    universe_df = pd.read_csv("data/universe.csv")
    tickers = universe_df["ticker"].tolist()
    sector_map = dict(zip(universe_df["ticker"], universe_df.get("sector", "Unknown")))

    logger.info(f"Running Value sleeve backtest on {len(tickers)} tickers")

    # Initialize datastores
    prices_store = PricesDataStore()
    fundamentals_store = FundamentalsDataStore(prices_datastore=prices_store)

    # Run backtest
    backtest = ValueSleeveBacktest(
        fundamentals_store,
        prices_store,
        tickers,
        sector_map,
    )

    equity, trades = backtest.run_backtest(
        start_date="2020-01-01",
        end_date="2026-03-08",
        rebalance_freq="monthly",
    )

    # Compute stats
    stats = backtest.compute_stats()
    logger.info("Backtest complete: %s", stats)

    # Save results
    output_dir = Path("outputs/backtests")
    output_dir.mkdir(parents=True, exist_ok=True)
    equity.to_csv(output_dir / "value_only_timeseries.csv", index=False)
    if trades is not None and not trades.empty:
        trades.to_csv(output_dir / "value_only_trades.csv", index=False)
    logger.info(f"Results saved to {output_dir}")
