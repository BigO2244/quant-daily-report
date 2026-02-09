from __future__ import annotations

"""Backtest interface for the Charlie Munger sleeve."""

from sleeves.sleeve_charlie_munger import (
    build_signals,
    compute_200w_sma,
    is_entry_signal,
    run_backtest_with_details,
    score_quality,
)


def prepare_data(*_args, **_kwargs):
    """Interface parity with other sleeves; data prep is internal to backtest."""
    return None


def backtest(_signals=None, period: str = "15y", interval: str = "1d"):
    """Interface parity with other sleeves; returns (equity_df, trades_df)."""
    details = run_backtest_with_details(period=period, interval=interval)
    return details.get("equity_df"), details.get("trades_df")


__all__ = [
    "prepare_data",
    "backtest",
    "build_signals",
    "compute_200w_sma",
    "is_entry_signal",
    "run_backtest_with_details",
    "score_quality",
]
