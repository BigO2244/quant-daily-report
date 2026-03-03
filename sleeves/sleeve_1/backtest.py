# ============================================================
# SLEEVE 1 — LOCKED
# Do not modify trading logic or parameters without:
#   1) creating a new versioned sleeve folder, OR
#   2) explicit "bug fix only" justification
# ============================================================

"""
Sleeve 1 backtest.

IMPORTANT: No import-time side effects (prints/logging).
The CLI help path (-h/--help) may import modules; keep it clean.
"""

BACKTEST_VERSION = "2025-12-14-Debug-2"

assert __name__.startswith("sleeves.sleeve_1"), "Invalid import context for Sleeve 1"

import os  # noqa: E402
import pandas as pd  # noqa: E402

from core.quant_report import (  # noqa: E402
    TICKERS,
    download_prices,
    fetch_factor_data,
    build_factor_scores,
    compute_full_signals,
    add_atr,
)

# ===== Backtest configuration (sourced from config.py) =====
# Values are unchanged — this is a structural extraction only.

from sleeves.sleeve_1 import config as s1_cfg  # noqa: E402

INITIAL_EQUITY           = s1_cfg.INITIAL_EQUITY
MIN_HOLD_DAYS            = s1_cfg.MIN_HOLD_DAYS
EXIT_SIGNAL_BUFFER       = s1_cfg.EXIT_SIGNAL_BUFFER
SIGNAL_CRASH_EXIT_ENABLED = s1_cfg.SIGNAL_CRASH_EXIT_ENABLED
SIGNAL_CRASH_DROP        = s1_cfg.SIGNAL_CRASH_DROP
SIGNAL_CRASH_ABS_FLOOR   = s1_cfg.SIGNAL_CRASH_ABS_FLOOR
MAX_HOLD_DAYS            = s1_cfg.MAX_HOLD_DAYS
TOP_LONGS                = s1_cfg.TOP_LONGS
MAX_POSITION_PCT         = s1_cfg.MAX_POSITION_PCT
SHORT_THRESHOLD          = s1_cfg.SHORT_THRESHOLD
LONG_THRESHOLD           = s1_cfg.LONG_THRESHOLD
SHORT_FLOOR_EXIT         = s1_cfg.SHORT_FLOOR_EXIT
LONG_FLOOR_EXIT          = s1_cfg.LONG_FLOOR_EXIT
MAX_SHORT_POSITIONS      = s1_cfg.MAX_SHORT_POSITIONS
MAX_SHORT_EXPOSURE_PCT   = s1_cfg.MAX_SHORT_EXPOSURE_PCT
TRADE_COST               = s1_cfg.TRADE_COST
EMERGENCY_STOP_ATR_MULT  = s1_cfg.EMERGENCY_STOP_ATR_MULT
USE_ENTRY_ATR_FOR_STOP   = s1_cfg.USE_ENTRY_ATR_FOR_STOP
GAP_LABEL_ATR_MULT       = s1_cfg.GAP_LABEL_ATR_MULT
DEFAULT_RULES            = s1_cfg.DEFAULT_RULES
VOL_BUCKET_RULES         = s1_cfg.VOL_BUCKET_RULES


class Position:
    def __init__(
        self,
        ticker,
        direction,
        shares,
        entry_price,
        entry_date,
        entry_atr=None,
        entry_signal=None,
    ):
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
        s["vol20"] = (
            s.groupby("ticker")["volume"]
            .rolling(20)
            .mean()
            .reset_index(level=0, drop=True)
        )

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
                trades.append(
                    dict(
                        ticker=tkr,
                        direction=1,
                        entry_date=pos.entry_date,
                        exit_date=current_date,
                        entry_price=pos.entry_price,
                        exit_price=px,
                        pnl=pnl,
                        hold_days=pos.hold_days,
                    )
                )
                positions.pop(tkr)

        today = signals_atr[signals_atr["date"] == current_date].sort_values(
            "final_signal", ascending=False
        )
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
                positions[row["ticker"]] = Position(
                    row["ticker"], 1, shares, px, current_date
                )
                equity -= TRADE_COST

        equity_curve.append({"date": current_date, "equity": equity})

    return pd.DataFrame(equity_curve), pd.DataFrame(trades)


def build_daily_sleeve_output(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    sleeve_name: str,
) -> pd.DataFrame:
    """
    Canonical daily sleeve output for reporting / aggregation.
    """

    df = equity_df.copy()
    df["sleeve"] = sleeve_name
    df["daily_return"] = df["equity"].pct_change().fillna(0.0)

    # Defaults if no trades
    df["gross_exposure"] = 0.0
    df["net_exposure"] = 0.0
    df["num_positions"] = 0

    if trades_df is None or trades_df.empty:
        return df[
            [
                "date",
                "sleeve",
                "equity",
                "daily_return",
                "gross_exposure",
                "net_exposure",
                "num_positions",
            ]
        ]

    # Build daily position exposure from trades
    rows = []
    for _, t in trades_df.iterrows():
        entry = pd.to_datetime(t["entry_date"])
        exit_ = pd.to_datetime(t["exit_date"])
        notional = abs(t.get("shares", 0) * t["entry_price"])
        signed = t.get("shares", 0) * t["entry_price"]

        for d in pd.date_range(entry, exit_):
            rows.append(
                {
                    "date": d,
                    "gross": notional,
                    "net": signed,
                }
            )

    expo = (
        pd.DataFrame(rows)
        .groupby("date", as_index=False)
        .agg(gross=("gross", "sum"), net=("net", "sum"))
    )

    df = df.merge(expo, on="date", how="left").fillna(0.0)
    df["gross_exposure"] = df["gross"] / df["equity"]
    df["net_exposure"] = df["net"] / df["equity"]
    df["num_positions"] = (df["gross"] > 0).astype(int)

    return df[
        [
            "date",
            "sleeve",
            "equity",
            "daily_return",
            "gross_exposure",
            "net_exposure",
            "num_positions",
        ]
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
