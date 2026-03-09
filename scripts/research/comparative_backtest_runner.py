"""
Comparative Backtest Runner
===========================
Orchestrates backtests for:
1. Trend-only (baseline)
2. Value-only (new)
3. Trend + Value (static 50/50)
4. Trend + Value (allocator-based, if available)

Computes comprehensive metrics and generates validation report.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Backtest parameters
BACKTEST_START = "2020-01-01"
BACKTEST_END = "2026-03-08"
INITIAL_CAPITAL = 100_000
COMMISSION = 0.001  # 10 bps
REBALANCE_SLIPPAGE = 0.002  # 20 bps
RISK_FREE_RATE = 0.04  # 4% annual


class ComparativeBacktestRunner:
    """
    Orchestrates all comparative backtests and metrics computation.

    Parameters
    ----------
    fundamentals_store : FundamentalsDataStore
    prices_store : PricesDataStore
    universe_tickers : list of str
    sector_map : dict, optional
    output_dir : Path or str
        Directory to save results
    """

    def __init__(
        self,
        fundamentals_store,
        prices_store,
        universe_tickers: list,
        sector_map: Optional[Dict] = None,
        output_dir: str = "outputs/backtests",
    ):
        self._fundamentals = fundamentals_store
        self._prices = prices_store
        self._universe = universe_tickers
        self._sector_map = sector_map or {t: "Unknown" for t in universe_tickers}
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Results containers
        self.results = {}  # config -> (equity_df, trades_df, stats)

    # =====================================================================
    # Main Entry Point
    # =====================================================================

    def run_all(self) -> Dict:
        """
        Run all 4 backtest configurations and return aggregated results.

        Returns
        -------
        dict with keys: {trend_only, value_only, combined_static, combined_allocator}
        """
        logger.info("[COMPARATIVE] Starting backtest suite")
        logger.info("[COMPARATIVE] Period: %s to %s", BACKTEST_START, BACKTEST_END)
        logger.info("[COMPARATIVE] Universe: %d tickers", len(self._universe))

        # Configuration 1: Trend-only
        logger.info("[COMPARATIVE] Running Trend-only backtest...")
        self._run_trend_only()

        # Configuration 2: Value-only
        logger.info("[COMPARATIVE] Running Value-only backtest...")
        self._run_value_only()

        # Configuration 3: Trend + Value (static 50/50)
        logger.info("[COMPARATIVE] Running Combined (static) backtest...")
        self._run_combined_static()

        # Configuration 4: Trend + Value (allocator-based, if available)
        logger.info("[COMPARATIVE] Running Combined (allocator) backtest...")
        self._run_combined_allocator()

        # Compute metrics and save results
        logger.info("[COMPARATIVE] Computing metrics...")
        self._compute_metrics()

        # Generate reports
        logger.info("[COMPARATIVE] Generating reports...")
        self._generate_reports()

        logger.info("[COMPARATIVE] Backtest suite complete")
        return self.results

    # =====================================================================
    # Individual Configuration Runners
    # =====================================================================

    def _run_trend_only(self):
        """Run Trend sleeve standalone."""
        try:
            from sleeves.sleeve_trend import backtest as trend_backtest

            logger.info("[TREND] Preparing data...")
            signals = trend_backtest.prepare_data()

            logger.info("[TREND] Running backtest...")
            equity_df, trades_df = trend_backtest.backtest(signals)

            logger.info("[TREND] Computing stats...")
            stats = trend_backtest.compute_stats(equity_df, trades_df)

            self.results["trend_only"] = {
                "equity": equity_df,
                "trades": trades_df,
                "stats": stats,
            }

            logger.info("[TREND] Complete: Sharpe=%.2f, MaxDD=%.1f%%",
                        stats.get("sharpe", 0),
                        stats.get("max_drawdown", 0) * 100)

        except Exception as e:
            logger.error("[TREND] Error: %s", e, exc_info=True)

    def _run_value_only(self):
        """Run Value sleeve standalone."""
        try:
            from sleeves.sleeve_value.backtest import ValueSleeveBacktest

            backtest = ValueSleeveBacktest(
                fundamentals_store=self._fundamentals,
                prices_store=self._prices,
                tickers=self._universe,
                sector_map=self._sector_map,
            )

            equity_df, trades_df = backtest.run_backtest(
                start_date=BACKTEST_START,
                end_date=BACKTEST_END,
                rebalance_freq="monthly",
            )

            stats = backtest.compute_stats()

            self.results["value_only"] = {
                "equity": equity_df,
                "trades": trades_df,
                "stats": stats,
            }

            logger.info("[VALUE] Complete: Sharpe=%.2f, MaxDD=%.1f%%",
                        stats.get("sharpe_ratio", 0),
                        stats.get("max_drawdown", 0) * 100)

        except Exception as e:
            logger.error("[VALUE] Error: %s", e, exc_info=True)

    def _run_combined_static(self):
        """Run Trend + Value with static 50/50 allocation."""
        try:
            # This would require integrating both sleeve outputs via backtest_engine
            # For now, approximate as equal-weighted blend of the two sleeves
            if "trend_only" not in self.results or "value_only" not in self.results:
                logger.warning("[COMBINED_STATIC] Skipping (missing individual results)")
                return

            # Blend the equity curves
            trend_equity = self.results["trend_only"]["equity"].copy()
            value_equity = self.results["value_only"]["equity"].copy()

            # Align dates
            combined_dates = trend_equity.set_index("date").index.intersection(
                value_equity.set_index("date").index
            )

            trend_subset = trend_equity[trend_equity["date"].isin(combined_dates)].set_index("date")
            value_subset = value_equity[value_equity["date"].isin(combined_dates)].set_index("date")

            # Simple 50/50 blend
            combined_nav = (trend_subset["equity"] * 0.5 + value_subset["equity"] * 0.5)
            combined_df = pd.DataFrame({
                "date": combined_dates,
                "equity": combined_nav.values,
            })

            stats = self._compute_stats_from_nav(combined_df)

            self.results["combined_static"] = {
                "equity": combined_df,
                "trades": None,
                "stats": stats,
            }

            logger.info("[COMBINED_STATIC] Complete: Sharpe=%.2f, MaxDD=%.1f%%",
                        stats.get("sharpe_ratio", 0),
                        stats.get("max_drawdown", 0) * 100)

        except Exception as e:
            logger.error("[COMBINED_STATIC] Error: %s", e, exc_info=True)

    def _run_combined_allocator(self):
        """Run Trend + Value with dynamic allocator (if available)."""
        try:
            # Placeholder: use same as static for now
            # In production, would use real allocator module
            if "combined_static" not in self.results:
                logger.warning("[COMBINED_ALLOCATOR] Skipping (missing static result)")
                return

            # For now, copy the static result
            self.results["combined_allocator"] = {
                "equity": self.results["combined_static"]["equity"].copy(),
                "trades": None,
                "stats": self.results["combined_static"]["stats"].copy(),
            }

            logger.info("[COMBINED_ALLOCATOR] Using static allocation (dynamic allocator TBD)")

        except Exception as e:
            logger.error("[COMBINED_ALLOCATOR] Error: %s", e, exc_info=True)

    # =====================================================================
    # Metrics Computation
    # =====================================================================

    def _compute_metrics(self):
        """Compute and store all metrics."""
        metrics_list = []

        for config, result in self.results.items():
            if result is None:
                continue

            equity_df = result.get("equity")
            if equity_df is None:
                continue

            stats = result.get("stats", {})
            metrics_list.append({
                "config": config,
                **stats,
            })

        if metrics_list:
            metrics_df = pd.DataFrame(metrics_list)
            metrics_df.to_csv(self._output_dir / "comparative_metrics_summary.csv", index=False)
            logger.info("[METRICS] Saved summary to comparative_metrics_summary.csv")

    def _compute_stats_from_nav(self, nav_df: pd.DataFrame) -> Dict:
        """Compute stats from a NAV dataframe."""
        if nav_df.empty:
            return {}

        nav_series = nav_df.set_index("date")["equity"]
        daily_returns = nav_series.pct_change().dropna()

        n_days = len(nav_series)
        n_years = n_days / 252

        # Annual metrics
        total_ret = (nav_series.iloc[-1] / nav_series.iloc[0]) - 1
        annual_ret = (nav_series.iloc[-1] / nav_series.iloc[0]) ** (1 / n_years) - 1 if n_years > 0 else 0
        annual_vol = daily_returns.std() * np.sqrt(252)
        sharpe = (annual_ret - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0

        # Downside variance (for Sortino)
        downside_returns = daily_returns[daily_returns < 0]
        downside_vol = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0
        sortino = (annual_ret - RISK_FREE_RATE) / downside_vol if downside_vol > 0 else 0

        # Max drawdown
        cumsum = (1 + daily_returns).cumprod()
        running_max = cumsum.expanding().max()
        drawdown_series = (cumsum - running_max) / running_max
        max_dd = drawdown_series.min()

        # Correlation to SPY (mock benchmark)
        win_rate = (daily_returns > 0).mean()

        return {
            "total_return": total_ret,
            "cagr": annual_ret,
            "annual_volatility": annual_vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "n_days": n_days,
        }

    # =====================================================================
    # Reporting
    # =====================================================================

    def _generate_reports(self):
        """Generate final validation reports."""
        # Summary table
        summary_lines = ["# Comparative Backtest Results\n"]
        summary_lines.append(f"**Period**: {BACKTEST_START} to {BACKTEST_END}\n")
        summary_lines.append(f"**Universe**: {len(self._universe)} tickers\n\n")

        summary_lines.append("## Performance Summary\n\n")
        summary_lines.append("| Config | Sharpe | Sortino | MaxDD | CAGR | Return |\n")
        summary_lines.append("|--------|--------|---------|-------|------|--------|\n")

        for config, result in sorted(self.results.items()):
            if result is None:
                continue
            stats = result.get("stats", {})
            summary_lines.append(
                f"| {config:20} | {stats.get('sharpe_ratio', 0):6.2f} | "
                f"{stats.get('sortino_ratio', 0):7.2f} | {stats.get('max_drawdown', 0):5.1%} | "
                f"{stats.get('cagr', 0):4.1%} | {stats.get('total_return', 0):6.1%} |\n"
            )

        summary_lines.append("\n## Recommendation\n\n")
        summary_lines.append("[To be updated with analysis conclusions]\n")

        report_path = self._output_dir / "COMPARATIVE_BACKTEST_SUMMARY.md"
        with open(report_path, "w") as f:
            f.writelines(summary_lines)

        logger.info("[REPORT] Saved to %s", report_path)


# =========================================================================
# Runner
# =========================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s"
    )

    import pandas as pd
    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
    from alpha_stack.datastore.prices import PricesDataStore

    # Load universe
    universe_df = pd.read_csv("data/universe.csv")
    tickers = universe_df["ticker"].tolist()
    sector_map_series = universe_df.get("sector")
    if sector_map_series is not None:
        sector_map = dict(zip(universe_df["ticker"], sector_map_series))
    else:
        sector_map = None

    logger.info(f"Starting comparative backtest on {len(tickers)} tickers")

    # Initialize
    prices_store = PricesDataStore()
    fundamentals_store = FundamentalsDataStore(prices_datastore=prices_store)

    # Run
    runner = ComparativeBacktestRunner(
        fundamentals_store=fundamentals_store,
        prices_store=prices_store,
        universe_tickers=tickers,
        sector_map=sector_map,
        output_dir="outputs/backtests",
    )

    results = runner.run_all()

    logger.info("Comparative backtest complete")
    print("\n=== RESULTS ===")
    for config, result in results.items():
        if result:
            print(f"{config}: {result.get('stats', {})}")
