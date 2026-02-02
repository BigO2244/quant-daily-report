# paper/trading_calendar.py
from __future__ import annotations

import pandas as pd


def next_trading_day(date_str: str) -> str:
    d = pd.Timestamp(date_str)
    nxt = (d + pd.tseries.offsets.BDay(1)).date()
    return str(nxt)


def prev_trading_day(date_str: str) -> str:
    d = pd.Timestamp(date_str)
    prev = (d - pd.tseries.offsets.BDay(1)).date()
    return str(prev)
