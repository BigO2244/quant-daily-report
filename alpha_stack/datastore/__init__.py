"""
Alpha Stack DataStore
=====================
Point-in-time oriented data access layer.

All DataStore implementations must honour as-of semantics:
data returned must represent what was *known* at the as_of_date,
not what is known today.

Available stores:
    PricesDataStore       — OHLCV price history (yfinance, PIT-safe for prices)
    MacroDataStore        — Macro time-series (VIX, yield proxy, credit proxy)
    BreadthDataStore      — Market breadth metrics (% above MA, A/D proxy)
"""

from alpha_stack.datastore.base import DataStoreBase
from alpha_stack.datastore.prices import PricesDataStore
from alpha_stack.datastore.macro import MacroDataStore
from alpha_stack.datastore.breadth import BreadthDataStore

__all__ = [
    "DataStoreBase",
    "PricesDataStore",
    "MacroDataStore",
    "BreadthDataStore",
]
