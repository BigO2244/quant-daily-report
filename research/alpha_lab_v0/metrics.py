"""Metrics computation: drawdowns, turnover, returns."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def compute_returns_and_drawdowns(
    nav_series: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compute daily returns and drawdown metrics.
    
    Returns:
        - List of daily metrics dicts with date, return, drawdown
        - Summary dict with max_dd, max_dd_duration, worst_drawdowns
    """
    if not nav_series:
        return [], {}
    
    df = pd.DataFrame(nav_series)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    
    # Filter to only rows with valid NAV
    df = df[df["nav"].notna()].copy()
    
    if df.empty or len(df) < 2:
        logger.warning("[ALPHA_LAB] Insufficient NAV data for metrics")
        return [], {}
    
    # Compute returns
    df["return"] = df["nav"].pct_change()
    
    # Compute running max and drawdown
    df["running_max"] = df["nav"].cummax()
    df["drawdown"] = (df["nav"] - df["running_max"]) / df["running_max"]
    
    # Max drawdown
    max_dd = df["drawdown"].min()
    
    # Find drawdown periods
    drawdowns = find_drawdown_periods(df)
    
    # Convert to daily metrics list
    daily_metrics = []
    for _, row in df.iterrows():
        daily_metrics.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "return": row["return"] if pd.notna(row["return"]) else None,
            "drawdown": row["drawdown"],
            "turnover": None  # Will be computed separately if needed
        })
    
    summary = {
        "max_drawdown": float(max_dd) if pd.notna(max_dd) else None,
        "worst_drawdowns": drawdowns[:5],  # Top 5 worst
        "sharpe": compute_sharpe_ratio(df["return"]),
        "volatility": df["return"].std() * np.sqrt(252) if len(df) > 1 else None,
        "total_return": (df["nav"].iloc[-1] / df["nav"].iloc[0] - 1) if len(df) > 0 else None
    }
    
    return daily_metrics, summary


def find_drawdown_periods(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Identify distinct drawdown periods."""
    drawdowns = []
    
    in_drawdown = False
    dd_start = None
    dd_start_val = None
    dd_trough = None
    dd_trough_val = None
    dd_depth = 0.0
    
    for idx, row in df.iterrows():
        nav = row["nav"]
        running_max = row["running_max"]
        dd = row["drawdown"]
        date = row["date"]
        
        if nav < running_max and not in_drawdown:
            # Start of new drawdown
            in_drawdown = True
            dd_start = date
            dd_start_val = running_max
            dd_trough = date
            dd_trough_val = nav
            dd_depth = dd
        
        elif in_drawdown:
            if dd < dd_depth:
                # New trough
                dd_trough = date
                dd_trough_val = nav
                dd_depth = dd
            
            if nav >= running_max:
                # Recovery
                drawdowns.append({
                    "start": dd_start.strftime("%Y-%m-%d"),
                    "trough": dd_trough.strftime("%Y-%m-%d"),
                    "recovery": date.strftime("%Y-%m-%d"),
                    "depth": float(dd_depth),
                    "duration_days": (date - dd_start).days
                })
                in_drawdown = False
    
    # Handle ongoing drawdown
    if in_drawdown:
        drawdowns.append({
            "start": dd_start.strftime("%Y-%m-%d"),
            "trough": dd_trough.strftime("%Y-%m-%d"),
            "recovery": "Ongoing",
            "depth": float(dd_depth),
            "duration_days": (df["date"].iloc[-1] - dd_start).days
        })
    
    # Sort by depth
    drawdowns.sort(key=lambda x: x["depth"])
    
    return drawdowns


def compute_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02) -> float | None:
    """Compute annualized Sharpe ratio."""
    if len(returns) < 2:
        return None
    
    returns_clean = returns.dropna()
    if len(returns_clean) == 0:
        return None
    
    excess_returns = returns_clean - (risk_free_rate / 252)
    sharpe = excess_returns.mean() / returns_clean.std() * np.sqrt(252)
    
    return float(sharpe) if pd.notna(sharpe) else None


def compute_turnover(
    trades: list[dict[str, Any]],
    nav_series: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Compute daily turnover as fraction of NAV.
    
    Turnover = sum(abs(trade_value)) / NAV
    """
    if not trades or not nav_series:
        return []
    
    trades_df = pd.DataFrame(trades)
    nav_df = pd.DataFrame(nav_series)
    
    trades_df["trade_date"] = pd.to_datetime(trades_df["trade_date"])
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    
    # Group trades by date
    daily_turnover = []
    
    for date, group in trades_df.groupby("trade_date"):
        # Sum absolute notional
        notional = 0.0
        for _, trade in group.iterrows():
            if pd.notna(trade.get("fill_price")) and pd.notna(trade.get("qty")):
                notional += abs(trade["qty"] * trade["fill_price"])
        
        # Find NAV for this date
        nav_row = nav_df[nav_df["date"] == date]
        if not nav_row.empty and pd.notna(nav_row.iloc[0]["nav"]):
            nav = nav_row.iloc[0]["nav"]
            turnover = notional / nav if nav > 0 else 0.0
            
            daily_turnover.append({
                "date": date.strftime("%Y-%m-%d"),
                "turnover": turnover
            })
    
    return daily_turnover
