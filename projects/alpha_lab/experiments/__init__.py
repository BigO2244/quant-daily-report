"""Frozen four-lane Alpha Lab experiments.

The package can inspect research inputs and construct signals.  It cannot
submit orders, alter a production portfolio, or inspect a locked challenge
period before every data and provenance gate passes.
"""

from .catalog import LANE_BY_HYPOTHESIS, LANES, DataAsset, ExperimentLane
from .signals import (
    classify_option_trade,
    earnings_revision_score,
    insider_conviction_score,
    options_information_score,
    supply_chain_raw_score,
)

__all__ = [
    "DataAsset",
    "ExperimentLane",
    "LANES",
    "LANE_BY_HYPOTHESIS",
    "classify_option_trade",
    "earnings_revision_score",
    "insider_conviction_score",
    "options_information_score",
    "supply_chain_raw_score",
]
