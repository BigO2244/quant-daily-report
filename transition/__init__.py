"""Caerus shared Transition Engine (V2.1 Part VI).

A pure, deterministic module that converts ``(current holdings, target holdings,
constraints)`` into a ``TransitionPlan`` (keep / reduce / sell / buy / block). It is
used by every execution mode (Shadow dry-run, Paper, Live Pilot) so that the
target-minus-current decision, capital semantics, and rotation/rebudget rules are
identical everywhere — eliminating the July 6, 2026 class of paper-vs-live
divergence.

This package contains no I/O, no broker calls, no environment reads, and no
wall-clock access. All external state (holdings, targets, account snapshot,
policies) is injected as frozen dataclasses.
"""

from transition.engine import (
    AccountSnapshot,
    BLOCK_BUYING_POWER_UNAVAILABLE,
    BLOCK_INSUFFICIENT_BUYING_POWER,
    BLOCK_ROTATION_UNSUPPORTED,
    BLOCK_SELLS_NOT_FILLED,
    BuyIntent,
    CapitalPolicy,
    Holdings,
    ModeConstraints,
    OrderPolicy,
    Position,
    SellIntent,
    TargetPortfolio,
    TargetPosition,
    TransitionPlan,
    compute_transition,
)

__all__ = [
    "AccountSnapshot",
    "BLOCK_BUYING_POWER_UNAVAILABLE",
    "BLOCK_INSUFFICIENT_BUYING_POWER",
    "BLOCK_ROTATION_UNSUPPORTED",
    "BLOCK_SELLS_NOT_FILLED",
    "BuyIntent",
    "CapitalPolicy",
    "Holdings",
    "ModeConstraints",
    "OrderPolicy",
    "Position",
    "SellIntent",
    "TargetPortfolio",
    "TargetPosition",
    "TransitionPlan",
    "compute_transition",
]
