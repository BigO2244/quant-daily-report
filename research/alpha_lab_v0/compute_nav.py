"""Compute portfolio NAV from ledger, positions, and prices."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def compute_nav_series(
    ledger_path: Path,
    prices: list[dict[str, Any]],
    run_id: str
) -> list[dict[str, Any]]:
    """Attempt to compute NAV series from ledger and prices.
    
    Returns NAV series if prices available, else returns cash-only series.
    """
    if not ledger_path.exists():
        logger.warning("[ALPHA_LAB] Cannot compute NAV without ledger")
        return []
    
    try:
        from paper.ledger import load_ledger
        from paper.positions import rebuild_positions_from_ledger
        
        ledger_df = load_ledger(str(ledger_path))
        if ledger_df.empty:
            logger.warning("[ALPHA_LAB] Ledger is empty, cannot compute NAV")
            return []
        
        # Get unique trade dates
        ledger_df["trade_date"] = pd.to_datetime(ledger_df["trade_date"])
        trade_dates = sorted(ledger_df["trade_date"].unique())
        
        # Build prices lookup
        prices_df = None
        if prices:
            prices_df = pd.DataFrame(prices)
            prices_df["date"] = pd.to_datetime(prices_df["date"])
        
        nav_series = []
        
        for trade_date in trade_dates:
            asof_str = trade_date.strftime("%Y-%m-%d")
            
            # Rebuild positions as of this date
            result = rebuild_positions_from_ledger(ledger_df, asof_str)
            positions_df = result["positions"]
            cash = result["cash"]
            
            # Compute equity value if prices available
            equity_value = 0.0
            if prices_df is not None and not positions_df.empty:
                for _, pos in positions_df.iterrows():
                    ticker = pos["ticker"]
                    shares = pos["shares"]
                    
                    # Find price for this ticker on this date
                    price_row = prices_df[
                        (prices_df["ticker"] == ticker) & 
                        (prices_df["date"] == trade_date)
                    ]
                    
                    if not price_row.empty:
                        price = price_row.iloc[0]["close"]
                        equity_value += shares * price
            
            nav = cash + equity_value if prices_df is not None else None
            
            nav_series.append({
                "date": asof_str,
                "nav": nav,
                "cash": cash,
                "equity_ex_cash": equity_value if prices_df is not None else None
            })
        
        if prices_df is None:
            logger.warning("[ALPHA_LAB] NAV series incomplete: no prices provided")
        else:
            logger.info(f"[ALPHA_LAB] Computed NAV series for {len(nav_series)} dates")
        
        return nav_series
    
    except Exception as e:
        logger.error(f"[ALPHA_LAB] Error computing NAV: {e}")
        return []
