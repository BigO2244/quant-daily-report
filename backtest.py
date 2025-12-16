print("Backtest Version Check: 2025-12-14-Debug-2")

import os
import pandas as pd
import numpy as np

from quant_report import (
    TICKERS,
    download_prices,
    fetch_factor_data,
    build_factor_scores,
    compute_full_signals,
    add_atr,
    MAX_RISK_PCT_PER_TRADE,
)

# ===== Backtest configuration =====

INITIAL_EQUITY = float(os.environ.get("ACCOUNT_EQUITY", "10000"))
MIN_HOLD_DAYS = 3
EXIT_SIGNAL_BUFFER = 5
SIGNAL_CRASH_EXIT_ENABLED = True
SIGNAL_CRASH_DROP = 20
SIGNAL_CRASH_ABS_FLOOR = 55
MAX_HOLD_DAYS = 5
TOP_LONGS = 3
MAX_POSITION_PCT = 0.07
SHORT_THRESHOLD = 20
LONG_THRESHOLD = 75
SHORT_FLOOR_EXIT = 30
LONG_FLOOR_EXIT = 65

MAX_SHORT_POSITIONS = 1
MAX_SHORT_EXPOSURE_PCT = 0.05

TRADE_COST = 0.50

EMERGENCY_STOP_ATR_MULT = 2.25
USE_ENTRY_ATR_FOR_STOP = True
GAP_LABEL_ATR_MULT = 1.0

DEFAULT_RULES = dict(
    LONG_THRESHOLD=LONG_THRESHOLD,
    LONG_FLOOR_EXIT=LONG_FLOOR_EXIT,
    MAX_HOLD_DAYS=MAX_HOLD_DAYS,
)

VOL_BUCKET_RULES = {
    "low": dict(LONG_THRESHOLD=72, LONG_FLOOR_EXIT=64, MAX_HOLD_DAYS=7),
    "medium": dict(LONG_THRESHOLD=75, LONG_FLOOR_EXIT=65, MAX_HOLD_DAYS=5),
    "high": dict(LONG_THRESHOLD=78, LONG_FLOOR_EXIT=66, MAX_HOLD_DAYS=4),
}


class Position:
    def __init__(self, ticker, direction, shares, entry_price, entry_date, entry_atr=None, entry_signal=None):
        self.ticker = ticker
        self.direction = direction
        self.shares = shares
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.entry_atr = entry_atr
        self.entry_signal = entry_signal
        self.hold_days = 0


def prepare_data():
    prices = download_prices(TICKERS, period="1y", interval="1d")
    factor_df = fetch_factor_data(prices)
    scored = build_factor_scores(factor_df)
    signals = compute_full_signals(scored)

    needed = ["final_signal", "momentum_score_v2", "factor_score", "volume_score"]
    if any(c not in signals.columns for c in needed):
        s = signals.copy()
        s["date"] = prices["date"]
        s["ticker"] = prices["ticker"]
        s["close"] = prices["close"]
        s["volume"] = prices["volume"]

        s = s.sort_values(["ticker", "date"])
        s["ret20"] = s.groupby("ticker")["close"].pct_change(20)
        s["ret60"] = s.groupby("ticker")["close"].pct_change(60)
        s["vol20"] = s.groupby("ticker")["volume"].rolling(20).mean().reset_index(level=0, drop=True)

        def rank(x):
            return x.rank(pct=True) * 100

        s["momentum_score_v2"] = s.groupby("date")["ret20"].transform(rank)
        s["factor_score"] = s.groupby("date")["ret60"].transform(rank)
        s["volume_score"] = s.groupby("date")["vol20"].transform(rank)

        s["final_signal"] = (
            0.50 * s["momentum_score_v2"]
            + 0.35 * s["factor_score"]
            + 0.15 * s["volume_score"]
        )

        for c in needed:
            signals[c] = s[c].fillna(50.0)

    prices_atr = add_atr(prices)
    signals_atr = signals.merge(
        prices_atr[["date", "ticker", "atr"]],
        on=["date", "ticker"],
        how="left",
    )

    signals_atr["date"] = pd.to_datetime(signals_atr["date"])
    return signals_atr.sort_values(["date", "ticker"])


def backtest(signals_atr: pd.DataFrame):
    price_table = signals_atr.set_index(["date", "ticker"])
    dates = sorted(signals_atr["date"].unique())

    equity = INITIAL_EQUITY
    equity_curve = []
    trades = []
    positions = {}

    for current_date in dates:
        for pos in positions.values():
            pos.hold_days += 1

        for tkr in list(positions.keys()):
            pos = positions[tkr]
            sig = price_table.loc[(current_date, tkr), "final_signal"]
            if sig < LONG_FLOOR_EXIT or pos.hold_days >= MAX_HOLD_DAYS:
                px = price_table.loc[(current_date, tkr), "close"]
                pnl = pos.shares * (px - pos.entry_price) - 2 * TRADE_COST
                equity += pnl
                trades.append(dict(
                    ticker=tkr,
                    direction=1,
                    entry_date=pos.entry_date,
                    exit_date=current_date,
                    entry_price=pos.entry_price,
                    exit_price=px,
                    pnl=pnl,
                    hold_days=pos.hold_days,
                ))
                positions.pop(tkr)

        today = signals_atr[signals_atr["date"] == current_date].sort_values("final_signal", ascending=False)
        for _, row in today.iterrows():
            if len(positions) >= TOP_LONGS:
                break
            if row["final_signal"] < LONG_THRESHOLD:
                break
            if row["ticker"] in positions:
                continue

            px = row["open"]
            shares = int((equity * MAX_POSITION_PCT) / px)
            if shares > 0:
                positions[row["ticker"]] = Position(row["ticker"], 1, shares, px, current_date)
                equity -= TRADE_COST

        equity_curve.append({"date": current_date, "equity": equity})

    return pd.DataFrame(equity_curve), pd.DataFrame(trades)


def build_daily_sleeve_output(equity_df, trades_df, sleeve_name):
    df = equity_df.copy()
    df["sleeve"] = sleeve_name
    df["daily_return"] = df["equity"].pct_change().fillna(0.0)

    if trades_df.empty:
        df["gross_exposure"] = 0.0
        df["net_exposure"] = 0.0
        df["num_positions"] = 0
        return df

    pos_days = []
    for _, t in trades_df.iterrows():
        for d in pd.date_range(t["entry_date"], t["exit_date"]):
            pos_days.append(dict(
                date=d,
                notional=t["shares"] * t["entry_price"],
                signed=t["shares"] * t["entry_price"],
            ))

    pos_df = pd.DataFrame(pos_days)
    expo = pos_df.groupby("date").sum().reset_index()

    df = df.merge(expo, on="date", how="left").fillna(0.0)
    df["gross_exposure"] = df["notional"] / df["equity"]
    df["net_exposure"] = df["signed"] / df["equity"]
    df["num_positions"] = df["gross_exposure"].gt(0).astype(int)

    return df[
        ["date", "sleeve", "equity", "daily_return", "gross_exposure", "net_exposure", "num_positions"]
    ]


def compute_stats(equity_df, trades_df):
    print("\n===== Backtest Results =====")
    print("Final Equity:", equity_df["equity"].iloc[-1])


def main():
    signals = prepare_data()
    equity_df, trades_df = backtest(signals)
    compute_stats(equity_df, trades_df)


if __name__ == "__main__":
    main()
