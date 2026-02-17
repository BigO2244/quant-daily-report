"""Earnings acceleration signal interface (stubbed for Growth Engine v4).

This module intentionally returns neutral defaults until an external
provider is configured.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EarningsSignalResult:
    score: float
    flag_negative: bool
    source: str = "stub"


class EarningsSignalProvider:
    """Interface for future earnings/revision signal providers."""

    def trailing_eps_acceleration(self, symbol: str) -> EarningsSignalResult:
        return EarningsSignalResult(score=0.0, flag_negative=False, source="stub")

    def forward_revision_trend(self, symbol: str) -> EarningsSignalResult:
        return EarningsSignalResult(score=0.0, flag_negative=False, source="stub")


_DEFAULT_PROVIDER = EarningsSignalProvider()


def trailing_eps_acceleration(symbol: str) -> tuple[float, bool]:
    """Return (score, flag_negative) for trailing EPS acceleration."""
    res = _DEFAULT_PROVIDER.trailing_eps_acceleration(symbol)
    return float(res.score), bool(res.flag_negative)


def forward_revision_trend(symbol: str) -> tuple[float, bool]:
    """Return (score, flag_negative) for forward revision trend."""
    res = _DEFAULT_PROVIDER.forward_revision_trend(symbol)
    return float(res.score), bool(res.flag_negative)
