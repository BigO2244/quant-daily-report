"""
Alpha Stack — Shadow Runner
=============================
Runs the Alpha Stack in shadow mode alongside production.

Shadow mode:
  - Computes Alpha Stack signals and portfolio for TODAY.
  - Writes shadow NAV and target book to outputs/alpha_stack_shadow/.
  - Does NOT submit any orders.
  - Does NOT touch the canonical production state.
  - Does NOT import from the production execution path.

Usage (standalone):
    python -m alpha_stack.research.shadow_runner

Usage (from GitHub Actions — separate shadow workflow):
    python scripts/alpha_stack_shadow.py

Outputs:
    outputs/alpha_stack_shadow/
        shadow_nav.csv          — daily shadow NAV
        target_book_<DATE>.json — today's target portfolio
        regime_<DATE>.json      — today's regime context
        diagnostics_<DATE>.json — sleeve diagnostics

PRODUCTION SAFETY: This module is completely isolated.
    It has zero imports from daily_quant_report.py,
    reconciliation.py, paper/, or any production artifact.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from alpha_stack._config_loader import get_flag, get_section
from alpha_stack.datastore.prices import PricesDataStore
from alpha_stack.datastore.macro import MacroDataStore
from alpha_stack.datastore.breadth import BreadthDataStore
from alpha_stack.features.trend import compute_trend_features
from alpha_stack.features.volatility import compute_volatility_features
from alpha_stack.regime.context import RegimeEngine, RegimeContext
from alpha_stack.sleeves.registry import SleeveRegistry
from alpha_stack.portfolio.allocator import AlphaStackAllocator, AllocationResult

logger = logging.getLogger(__name__)


class ShadowRunner:
    """
    Runs Alpha Stack in shadow mode for a single date (default: today).

    Parameters
    ----------
    run_date : date or str, optional
        Date to run as. Defaults to today.
    output_dir : str, optional
        Shadow output directory.
    """

    def __init__(
        self,
        run_date: Optional[date | str] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        research_cfg = get_section("research") or {}
        self._run_date = (
            pd.Timestamp(run_date).date() if run_date
            else date.today()
        )
        self._output_dir = Path(
            output_dir
            or os.environ.get("ALPHA_STACK_SHADOW_DIR", "")
            or research_cfg.get("shadow_output_dir", "outputs/alpha_stack_shadow")
        )
        self._nav_file = self._output_dir / "shadow_nav.csv"

        # Components
        self._prices = PricesDataStore()
        self._macro = MacroDataStore(prices_store=self._prices)
        self._breadth = BreadthDataStore(prices_store=self._prices)
        self._regime_engine = RegimeEngine(
            macro_store=self._macro,
            breadth_store=self._breadth,
        )
        self._registry = SleeveRegistry()
        self._allocator = AlphaStackAllocator()

    # ------------------------------------------------------------------ #
    # Main entry point                                                     #
    # ------------------------------------------------------------------ #

    def run_daily(self) -> dict:
        """
        Run shadow mode for the configured run_date.

        Returns
        -------
        dict with keys: date, regime, target_book, sleeve_budgets, diagnostics
        """
        if not get_flag("ENABLE_ALPHA_STACK", default=False):
            logger.info(
                "[SHADOW] ENABLE_ALPHA_STACK=false. Shadow runner is off. "
                "Enable in alpha_stack.yaml to activate."
            )
            return {"status": "disabled", "date": str(self._run_date)}

        if not get_flag("ENABLE_ALPHA_STACK_SHADOW", default=False):
            logger.info("[SHADOW] ENABLE_ALPHA_STACK_SHADOW=false. Shadow mode disabled.")
            return {"status": "shadow_disabled", "date": str(self._run_date)}

        logger.info("[SHADOW] Running shadow mode for %s", self._run_date)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        date_str = str(self._run_date)

        # 1. Load universe
        universe = self._load_universe()
        tickers = universe["ticker"].tolist()
        sector_map = dict(zip(universe["ticker"], universe.get("sector", ["Unknown"] * len(universe))))

        if not tickers:
            logger.error("[SHADOW] Empty universe; aborting.")
            return {"status": "error", "error": "empty_universe"}

        # 2. Download price history (PIT: end=run_date)
        from datetime import timedelta
        lookback_start = self._run_date - timedelta(days=300)
        prices_df = self._prices.get_prices_multi(tickers, lookback_start, self._run_date)

        if prices_df.empty:
            logger.error("[SHADOW] No price data; aborting.")
            return {"status": "error", "error": "no_price_data"}

        # 3. Compute features
        trend_feats = compute_trend_features(prices_df, self._run_date)
        vol_feats = compute_volatility_features(prices_df, self._run_date)

        if trend_feats.empty:
            logger.warning("[SHADOW] No trend features; skipping.")
            return {"status": "no_features", "date": date_str}

        feats = trend_feats.merge(vol_feats, on="ticker", how="left")
        feats["sector"] = feats["ticker"].map(sector_map).fillna("Unknown")

        # 4. Regime classification
        ctx = self._regime_engine.classify(date_str)

        # 5. Run active sleeves
        active = self._registry.active_sleeves()
        sleeve_outputs = {}
        diagnostics = {}
        for sleeve in active:
            try:
                out = sleeve.run(feats, ctx, risk_budget=1.0, as_of_date=date_str)
                sleeve_outputs[sleeve.name] = out
                diagnostics[sleeve.name] = out.meta
            except Exception as exc:
                logger.warning("[SHADOW] Sleeve %s error: %s", sleeve.name, exc)

        # 6. Allocate
        alloc = self._allocator.allocate(
            sleeve_outputs, ctx, sector_map=sector_map, current_dd=0.0
        )

        # 7. Persist outputs
        result = {
            "status": "ok",
            "date": date_str,
            "regime": ctx.to_dict(),
            "sleeve_budgets": alloc.sleeve_budgets,
            "target_book": (
                alloc.target_book.to_dict(orient="records")
                if not alloc.target_book.empty else []
            ),
            "cash_weight": alloc.cash_weight,
            "gross_exposure": alloc.gross_exposure,
            "notes": alloc.notes,
            "diagnostics": diagnostics,
        }

        self._persist(result, ctx, alloc)
        self._update_nav(alloc, date_str, prices_df)

        logger.info(
            "[SHADOW] Done: regime=%s vol=%s | gross=%.1f%% cash=%.1f%% | %d positions",
            ctx.trend_state.value, ctx.vol_state.value,
            alloc.gross_exposure * 100, alloc.cash_weight * 100,
            len(alloc.target_book),
        )

        return result

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _persist(
        self,
        result: dict,
        ctx: RegimeContext,
        alloc: AllocationResult,
    ) -> None:
        """Write per-date snapshot files."""
        d = str(self._run_date)

        # Target book
        tb_path = self._output_dir / f"target_book_{d}.json"
        with open(tb_path, "w") as fh:
            json.dump(result["target_book"], fh, indent=2)

        # Regime
        reg_path = self._output_dir / f"regime_{d}.json"
        with open(reg_path, "w") as fh:
            json.dump(ctx.to_dict(), fh, indent=2)

        # Diagnostics
        diag_path = self._output_dir / f"diagnostics_{d}.json"
        with open(diag_path, "w") as fh:
            json.dump(result["diagnostics"], fh, indent=2, default=str)

        # Summary
        summary = {
            "date": d,
            "status": result["status"],
            "regime": ctx.to_dict(),
            "sleeve_budgets": alloc.sleeve_budgets,
            "gross_exposure": alloc.gross_exposure,
            "cash_weight": alloc.cash_weight,
            "n_positions": len(alloc.target_book),
            "notes": alloc.notes,
        }
        sum_path = self._output_dir / f"summary_{d}.json"
        with open(sum_path, "w") as fh:
            json.dump(summary, fh, indent=2)

        logger.info("[SHADOW] Outputs written to %s", self._output_dir)

    def _update_nav(
        self,
        alloc: AllocationResult,
        date_str: str,
        prices_df: pd.DataFrame,
    ) -> None:
        """
        Append today's gross exposure to the shadow NAV ledger.

        NOTE: True shadow NAV requires computing P&L vs yesterday's weights.
        This simplified version records the target allocation for monitoring.
        A full mark-to-market shadow NAV would require the previous day's
        target book to be loaded and priced against today's closes.
        """
        record = {
            "date": date_str,
            "gross_exposure": alloc.gross_exposure,
            "cash_weight": alloc.cash_weight,
            "n_positions": len(alloc.target_book),
        }
        # Add sleeve budgets
        for k, v in alloc.sleeve_budgets.items():
            record[f"budget_{k}"] = v

        df = pd.DataFrame([record])
        if self._nav_file.exists():
            existing = pd.read_csv(self._nav_file)
            # Overwrite today's row if it exists
            existing = existing[existing["date"] != date_str]
            df = pd.concat([existing, df], ignore_index=True)

        df.to_csv(self._nav_file, index=False)
        logger.debug("[SHADOW] NAV ledger updated: %s", self._nav_file)

    def _load_universe(self) -> pd.DataFrame:
        try:
            csv_path = (get_section("universe") or {}).get("csv_path", "data/universe.csv")
            df = pd.read_csv(csv_path)
            col_map = {c.lower(): c for c in df.columns}
            if "ticker" not in df.columns and "symbol" in col_map:
                df = df.rename(columns={col_map["symbol"]: "ticker"})
            return df.dropna(subset=["ticker"])
        except Exception as exc:
            logger.error("[SHADOW] Cannot load universe: %s", exc)
            return pd.DataFrame(columns=["ticker"])


# ------------------------------------------------------------------ #
# CLI entry point                                                      #
# ------------------------------------------------------------------ #

def main() -> None:
    """Shadow runner entry point."""
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Alpha Stack Shadow Runner")
    parser.add_argument("--date", help="Run date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--output-dir", help="Output directory override.")
    parser.add_argument("--enable", action="store_true",
                        help="Force-enable Alpha Stack for this run (ignores feature flags). "
                             "FOR TESTING ONLY.")
    args = parser.parse_args()

    if args.enable:
        import alpha_stack._config_loader as cfg_mod
        # Temporarily override flags for this run
        cfg_mod._CACHE = cfg_mod.load_alpha_stack_config()
        if "feature_flags" not in cfg_mod._CACHE:
            cfg_mod._CACHE["feature_flags"] = {}
        cfg_mod._CACHE["feature_flags"]["ENABLE_ALPHA_STACK"] = True
        cfg_mod._CACHE["feature_flags"]["ENABLE_ALPHA_STACK_SHADOW"] = True
        logger.warning("[SHADOW] Feature flags force-enabled via --enable flag. FOR TESTING ONLY.")

    runner = ShadowRunner(
        run_date=args.date,
        output_dir=args.output_dir,
    )
    result = runner.run_daily()

    print(json.dumps({
        "status": result.get("status"),
        "date": result.get("date"),
        "regime": result.get("regime", {}).get("trend_state"),
        "gross_exposure": result.get("gross_exposure"),
        "n_positions": len(result.get("target_book", [])),
    }, indent=2))

    sys.exit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
