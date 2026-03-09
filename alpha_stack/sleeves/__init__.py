"""
Alpha Stack — Sleeve Layer
===========================
Formal sleeve contract and implementations.

All sleeves implement SleeveBase and are registered in SleeveRegistry.
Only sleeves with enabled=true in config (and corresponding feature flags)
are active at runtime.
"""

from alpha_stack.sleeves.base import SleeveBase, SleeveOutput, HoldState
from alpha_stack.sleeves.registry import SleeveRegistry

__all__ = ["SleeveBase", "SleeveOutput", "HoldState", "SleeveRegistry"]
