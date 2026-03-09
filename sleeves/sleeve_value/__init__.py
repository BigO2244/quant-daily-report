"""
Alpha Stack — Sleeve Value (Value factor-based sleeve)
======================================================
Monthly-rebalance value strategy using PIT-safe SEC EDGAR fundamentals.
"""

from .backtest import ValueSleeveBacktest

__all__ = ["ValueSleeveBacktest"]
