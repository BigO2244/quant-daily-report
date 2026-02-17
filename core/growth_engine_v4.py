"""Growth Engine v4 policy helpers.

Locked rules:
- Weekly rebalance on Monday market open.
- Daily stop checks, exits next open.
- Long-only, no leverage/short/inverse instruments.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from core.earnings_signal import trailing_eps_acceleration, forward_revision_trend

INCEPTION_DATE = pd.Timestamp("2026-02-23")
MAX_POSITION_WEIGHT = 0.20
PER_POSITION_STOP_PCT = -0.15
CIRCUIT_BREAKER_DRAWDOWN = -0.25
ALLOWED_EXPOSURE_UNDER_CIRCUIT_BREAKER = (0.50, 0.60)


@dataclass(frozen=True)
class ExitSignal:
    ticker: str
    reason: str
    execute_on: str


def is_monday_rebalance(trade_date: str | dt.date | pd.Timestamp) -> bool:
    d = pd.Timestamp(trade_date)
    return d.weekday() == 0


def next_open_date(trade_date: str | dt.date | pd.Timestamp) -> str:
    d = pd.Timestamp(trade_date)
    nxt = d + pd.offsets.BDay(1)
    return nxt.strftime("%Y-%m-%d")


def evaluate_stop_exits(
    positions: pd.DataFrame,
    prices: pd.DataFrame,
    asof_date: str,
) -> list[ExitSignal]:
    """Generate stop-driven exits to execute on next open.

    Expected columns:
    - positions: ticker, entry_price
    - prices: ticker, close, sma_100
    """
    if positions is None or positions.empty or prices is None or prices.empty:
        return []

    merged = positions.merge(prices, on="ticker", how="inner")
    out: list[ExitSignal] = []
    for _, row in merged.iterrows():
        ticker = str(row["ticker"]).upper()
        entry = float(row.get("entry_price") or 0.0)
        close = float(row.get("close") or 0.0)
        sma_100 = float(row.get("sma_100") or 0.0)
        if entry <= 0 or close <= 0:
            continue

        ret = (close / entry) - 1.0
        eps_score, eps_neg = trailing_eps_acceleration(ticker)
        rev_score, rev_neg = forward_revision_trend(ticker)
        _ = eps_score, rev_score

        if ret <= PER_POSITION_STOP_PCT:
            out.append(ExitSignal(ticker=ticker, reason="hard_stop_-15pct", execute_on=next_open_date(asof_date)))
        elif sma_100 > 0 and close < sma_100:
            out.append(ExitSignal(ticker=ticker, reason="break_100d_sma", execute_on=next_open_date(asof_date)))
        elif eps_neg or rev_neg:
            out.append(ExitSignal(ticker=ticker, reason="earnings_accel_negative", execute_on=next_open_date(asof_date)))

    return out
