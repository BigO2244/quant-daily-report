# ============================================================
# SLEEVE 1 — LOCKED
# Do not modify trading logic or parameters without:
#   1) creating a new versioned sleeve folder, OR
#   2) explicit "bug fix only" justification
# ============================================================

print("Backtest Version Check: 2025-12-14-Debug-2")

assert __name__.startswith("sleeves.sleeve_1"), "Invalid import context for Sleeve 1"

import os
import pandas as pd
import numpy as np

from core.quant_report import (
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
MIN_HOLD_DAYS = 3         # do not exit before this
EXIT_SIGNAL_BUFFER = 5    # signal must drop this much below floor to exit
SIGNAL_CRASH_EXIT_ENABLED = True # Early-exit override (signal crash)
SIGNAL_CRASH_DROP = 20          # exit if signal drops >= 20 points from entry
SIGNAL_CRASH_ABS_FLOOR = 55     # AND signal is now below this (prevents noise exits)
MAX_HOLD_DAYS = 5         # max holding period per position (~weekly)
TOP_LONGS = 3             # max number of long positions
MAX_POSITION_PCT = 0.07
SHORT_THRESHOLD = 20      # final_signal threshold for shorts (not used in this long-only version)
LONG_THRESHOLD = 75       # final_signal threshold for longs
SHORT_FLOOR_EXIT = 30     # exit short if signal rebounds above this
LONG_FLOOR_EXIT = 65      # exit long if signal falls below this

MAX_SHORT_POSITIONS = 1
MAX_SHORT_EXPOSURE_PCT = 0.05  # 5% of equity

TRADE_COST = 0.50         # per side (open or close), in dollars

# ===== Emergency stop (price shock protection) =====
EMERGENCY_STOP_ATR_MULT = 2.25    # exits immediately on large adverse move
USE_ENTRY_ATR_FOR_STOP = True   # use ATR at entry for first few days
GAP_LABEL_ATR_MULT = 1.0  # if open is >= 1.0 ATR beyond stop, label as gap


# ===== Vol bucket rules =====
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
    """
    Pull ~1y of data, compute signals & ATR, and assign a volatility bucket
    per ticker based on ATR as % of price.
    """
    prices = download_prices(TICKERS, period="1y", interval="1d")
    factor_df = fetch_factor_data(prices)
    scored = build_factor_scores(factor_df)
    signals = compute_full_signals(scored)

    # -----------------------------------------------------------------
    # Compatibility shim: if quant_report's pipeline doesn't produce the
    # expected score columns yet, compute a simple-but-real signal so the
    # Sleeve-1 backtest engine can trade.
    #
    # Signal recipe (0-100):
    #   momentum_score_v2 = cross-sectional percentile rank of 20d return
    #   factor_score      = cross-sectional percentile rank of 60d return
    #   volume_score      = cross-sectional percentile rank of 20d avg volume
    #   final_signal      = 0.50*mom + 0.35*factor + 0.15*volume
    # -----------------------------------------------------------------
    needed = ['final_signal','momentum_score_v2','factor_score','volume_score']
    missing = [c for c in needed if c not in signals.columns]

    if missing:
        s = signals.copy()

        # Ensure core fields exist
        if 'date' not in s.columns and 'date' in prices.columns:
            s['date'] = prices['date']
        if 'ticker' not in s.columns and 'ticker' in prices.columns:
            s['ticker'] = prices['ticker']
        if 'close' not in s.columns and 'close' in prices.columns:
            s['close'] = prices['close']
        if 'volume' not in s.columns and 'volume' in prices.columns:
            s['volume'] = prices['volume']

        s['date'] = pd.to_datetime(s['date'])
        s = s.sort_values(['ticker','date'])

        # 20d / 60d returns by ticker
        s['ret20'] = s.groupby('ticker')['close'].pct_change(20, fill_method=None)
        s['ret60'] = s.groupby('ticker')['close'].pct_change(60, fill_method=None)

        # 20d avg volume by ticker
        s['vol20'] = s.groupby('ticker')['volume'].rolling(20).mean().reset_index(level=0, drop=True)

        # Cross-sectional ranks per day -> 0..100
        def pct_rank(x):
            return x.rank(pct=True) * 100.0

        s['momentum_score_v2'] = s.groupby('date')['ret20'].transform(pct_rank)
        s['factor_score'] = s.groupby('date')['ret60'].transform(pct_rank)
        s['volume_score'] = s.groupby('date')['vol20'].transform(pct_rank)

        # Weighted signal
        s['final_signal'] = (
            0.50 * s['momentum_score_v2'] +
            0.35 * s['factor_score'] +
            0.15 * s['volume_score']
        )

        # Fill early NaNs with neutral 50s
        for c in needed:
            s[c] = s[c].astype(float).fillna(50.0)

        # Keep original columns + required ones
        for c in needed:
            signals[c] = s[c].values

    prices_atr = add_atr(prices)

    signals_atr = signals.merge(
        prices_atr[["date", "ticker", "atr"]],
        on=["date", "ticker"],
        how="left",
    )

    # Ensure date is datetime and sorted
    signals_atr["date"] = pd.to_datetime(signals_atr["date"])
    signals_atr = signals_atr.sort_values(["date", "ticker"])

    # ATR % median per ticker => vol bucket
    vol_df = (
        signals_atr.groupby("ticker", as_index=False)
        .agg(
            atr_med=("atr", "median"),
            close_med=("close", "median"),
        )
    )
    vol_df["atr_pct_med"] = vol_df["atr_med"] / vol_df["close_med"]

    def vol_bucket_fn(v):
        if pd.isna(v):
            return "medium"
        if v < 0.015:
            return "low"
        if v < 0.03:
            return "medium"
        return "high"

    vol_df["vol_bucket"] = vol_df["atr_pct_med"].apply(vol_bucket_fn)
    vol_map = dict(zip(vol_df["ticker"], vol_df["vol_bucket"]))
    signals_atr["vol_bucket"] = signals_atr["ticker"].map(vol_map)

    return signals_atr


def backtest(signals_atr: pd.DataFrame):
    # Index for fast access
    price_table = (
        signals_atr.set_index(["date", "ticker"])[
            [
                "open",
                "close",
                "atr",
                "final_signal",
                "momentum_score_v2",
                "factor_score",
                "volume_score",
                "vol_bucket",
            ]
        ]
        .sort_index()
    )

    dates = sorted(signals_atr["date"].unique())
    equity = INITIAL_EQUITY
    equity_curve = []
    trades = []
    positions = {}  # ticker -> Position

    def todays_open(ticker, current_date):
        try:
            return price_table.loc[(current_date, ticker), "open"]
        except Exception:
            return np.nan

    def todays_close(ticker, current_date):
        try:
            return price_table.loc[(current_date, ticker), "close"]
        except Exception:
            return np.nan

    for current_date in dates:
        # 1) Update holding days
        for pos in positions.values():
            pos.hold_days += 1

        # 2) Exit logic
        to_close = []
        for tkr, pos in list(positions.items()):
            try:
                sig = price_table.loc[(current_date, tkr), "final_signal"]
            except Exception:
                sig = np.nan

            rules = DEFAULT_RULES
            try:
                vb = price_table.loc[(current_date, tkr), "vol_bucket"]
                rules = VOL_BUCKET_RULES.get(vb, DEFAULT_RULES)
            except Exception:
                pass

            # --- Emergency ATR shock stop (ignores MIN_HOLD_DAYS)
            px_open = todays_open(tkr, current_date)
            if pd.isna(px_open):
                px_open = todays_close(tkr, current_date)
            
            atr_now = price_table.loc[(current_date, tkr), "atr"] if (current_date, tkr) in price_table.index else np.nan
            atr_ref = pos.entry_atr if (USE_ENTRY_ATR_FOR_STOP and pos.entry_atr and not pd.isna(pos.entry_atr)) else atr_now
            
            if not pd.isna(px_open) and not pd.isna(atr_ref):
                stop_level = pos.entry_price - (EMERGENCY_STOP_ATR_MULT * atr_ref)  # long only
                if px_open <= stop_level:
                    pnl = pos.direction * pos.shares * (px_open - pos.entry_price) - 2 * TRADE_COST
                    equity += pnl

                    # classify whether we gapped through the stop
                    gap_amt = stop_level - px_open  # >0 means open is below stop
                    gap_flag = (gap_amt / atr_ref) >= GAP_LABEL_ATR_MULT if (atr_ref is not None and not pd.isna(atr_ref)) else False
                    reason_exit = "emergency_atr_stop_gap" if gap_flag else "emergency_atr_stop"
                    
                    trades.append(dict(
                        ticker=tkr,
                        direction=pos.direction,
                        entry_date=pos.entry_date,
                        exit_date=current_date,
                        entry_price=pos.entry_price,
                        exit_price=px_open,
                        pnl=pnl,
                        hold_days=pos.hold_days,
                        reason_exit=reason_exit,
                    ))

            
                    to_close.append(tkr)
                    continue
            # --- Signal crash override (ignores MIN_HOLD_DAYS)
            if SIGNAL_CRASH_EXIT_ENABLED and pos.direction == 1 and pos.entry_signal is not None and not pd.isna(sig):
                if (pos.entry_signal - sig) >= SIGNAL_CRASH_DROP and sig <= SIGNAL_CRASH_ABS_FLOOR:
                    px = todays_open(tkr, current_date)
                    if pd.isna(px):
                        px = todays_close(tkr, current_date)
                    if not pd.isna(px):
                        pnl = pos.direction * pos.shares * (px - pos.entry_price) - 2 * TRADE_COST
                        equity += pnl
                        trades.append(dict(
                            ticker=tkr,
                            direction=pos.direction,
                            entry_date=pos.entry_date,
                            exit_date=current_date,
                            entry_price=pos.entry_price,
                            exit_price=px,
                            pnl=pnl,
                            hold_days=pos.hold_days,
                            reason_exit="signal_crash_override",
                        ))
                        to_close.append(tkr)
                        continue

            # Minimum hold to avoid early churn
            if pos.hold_days < MIN_HOLD_DAYS:
                continue

            # Adaptive exit floor (room for winners)
            dynamic_long_floor = rules["LONG_FLOOR_EXIT"] - EXIT_SIGNAL_BUFFER

            exit_now = False
            if pos.direction == 1:
                if (not pd.isna(sig) and sig < dynamic_long_floor) or pos.hold_days >= rules["MAX_HOLD_DAYS"]:
                    exit_now = True
            else:
                if (not pd.isna(sig) and sig > SHORT_FLOOR_EXIT) or pos.hold_days >= MAX_HOLD_DAYS:
                    exit_now = True

            if exit_now:
                px = todays_open(tkr, current_date)
                if pd.isna(px):
                    px = todays_close(tkr, current_date)
                if pd.isna(px):
                    continue

                pnl = pos.direction * pos.shares * (px - pos.entry_price) - 2 * TRADE_COST
                equity += pnl

                trades.append(
                    dict(
                        ticker=tkr,
                        direction=pos.direction,
                        entry_date=pos.entry_date,
                        exit_date=current_date,
                        entry_price=pos.entry_price,
                        exit_price=px,
                        pnl=pnl,
                        hold_days=pos.hold_days,
                    )
                )
                to_close.append(tkr)

        for tkr in to_close:
            positions.pop(tkr, None)

        # 3) Entry logic (longs only in this version)
        today_rows = signals_atr[signals_atr["date"] == current_date].copy()
        today_rows = today_rows.sort_values("final_signal", ascending=False)

        current_longs = [p for p in positions.values() if p.direction == 1]

        for _, row in today_rows.iterrows():
            if len(current_longs) >= TOP_LONGS:
                break

            if row["final_signal"] < LONG_THRESHOLD:
                break

            tkr = row["ticker"]
            if tkr in positions:
                continue

            px = todays_open(tkr, current_date)
            if pd.isna(px):
                continue

            atr = row["atr"] if not pd.isna(row["atr"]) else px * 0.01
            stop_distance = 1.5 * atr
            risk_per_share = max(stop_distance, 0.01)

            risk_capital = equity * MAX_RISK_PCT_PER_TRADE
            max_pos_capital = equity * MAX_POSITION_PCT

            shares_from_risk = risk_capital / risk_per_share
            shares_from_capital = max_pos_capital / px
            shares = int(max(0, min(shares_from_risk, shares_from_capital)))

            if shares <= 0:
                continue

            positions[tkr] = Position(tkr, 1, shares, px, current_date, entry_atr=atr, entry_signal=row["final_signal"])
            current_longs.append(positions[tkr])

            # open cost
            equity -= TRADE_COST

        equity_curve.append(dict(date=current_date, equity=equity))

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades)

    if not trades_df.empty:
        trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])
        trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])

        # If hold_days wasn't captured (older runs), compute it
        if "hold_days" not in trades_df.columns or trades_df["hold_days"].isna().any():
            trades_df["hold_days"] = (trades_df["exit_date"] - trades_df["entry_date"]).dt.days

    return equity_df, trades_df

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
            ["date", "sleeve", "equity", "daily_return", "gross_exposure", "net_exposure", "num_positions"]
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
        ["date", "sleeve", "equity", "daily_return", "gross_exposure", "net_exposure", "num_positions"]
    ]

def compute_stats(equity_df: pd.DataFrame, trades_df: pd.DataFrame):
    # ---- REQUIRED DEBUG OUTPUT #1
    DEBUG_CONFIG = {
        "INITIAL_EQUITY": INITIAL_EQUITY,
        "MIN_HOLD_DAYS": MIN_HOLD_DAYS,
        "EXIT_SIGNAL_BUFFER": EXIT_SIGNAL_BUFFER,
        "MAX_HOLD_DAYS": MAX_HOLD_DAYS,
        "TOP_LONGS": TOP_LONGS,
        "SHORT_THRESHOLD": SHORT_THRESHOLD,
        "LONG_THRESHOLD": LONG_THRESHOLD,
        "SHORT_FLOOR_EXIT": SHORT_FLOOR_EXIT,
        "LONG_FLOOR_EXIT": LONG_FLOOR_EXIT,
        "MAX_RISK_PCT_PER_TRADE": MAX_RISK_PCT_PER_TRADE,
        "MAX_POSITION_PCT": MAX_POSITION_PCT,
        "TRADE_COST": TRADE_COST,
    }
    print("\nDEBUG CONFIG:", DEBUG_CONFIG)

    print("\n===== Backtest Results (1y) =====")
    print(f"Initial Equity: ${INITIAL_EQUITY:,.2f}")
    final_equity = equity_df["equity"].iloc[-1] if not equity_df.empty else INITIAL_EQUITY
    print(f"Final Equity:   ${final_equity:,.2f}")
    total_return = (final_equity / INITIAL_EQUITY - 1) * 100
    print(f"Total Return:   {total_return:.2f}%")

    equity_df = equity_df.copy()
    equity_df["peak"] = equity_df["equity"].cummax()
    equity_df["drawdown"] = equity_df["equity"] / equity_df["peak"] - 1
    max_dd = equity_df["drawdown"].min() * 100 if not equity_df.empty else 0
    print(f"Max Drawdown:   {max_dd:.2f}%")
    print("")

    if trades_df.empty:
        print("No trades executed.")
        print("\nShort-hold losers (hold_days <= 3): NONE (no trades)")
        return

    print(f"Number of trades: {len(trades_df)}")
    win_rate = (trades_df["pnl"] > 0).mean() * 100
    print(f"Win rate:         {win_rate:.2f}%")
    print(f"Avg PnL per trade: ${trades_df['pnl'].mean():.2f}")
    print("")

    # PnL by direction
    pnl_by_dir = trades_df.groupby("direction")["pnl"].sum()
    print("PnL by direction:")
    for d, pnl in pnl_by_dir.items():
        name = "Longs" if d == 1 else "Shorts"
        sub = trades_df[trades_df["direction"] == d]
        wr = (sub["pnl"] > 0).mean() * 100
        print(f"  {name}: {pnl:.2f} over {len(sub)} trades (win rate {wr:.1f}%)")
    print("")

    pnl_by_ticker = trades_df.groupby("ticker")["pnl"].sum().sort_values(ascending=False)
    print("PnL by ticker:")
    print(pnl_by_ticker)
    print("")

    # Holding stats
    tdf = trades_df.copy()
    if "hold_days" not in tdf.columns:
        tdf["hold_days"] = (tdf["exit_date"] - tdf["entry_date"]).dt.days

    print("Avg holding days:", tdf["hold_days"].mean())
    print("PnL by holding bucket:")
    print(
        tdf.groupby(
            pd.cut(tdf["hold_days"], [0, 1, 3, 5, 10, 999]),
            observed=False,
        ).agg(n=("pnl", "count"), pnl=("pnl", "sum"))
    )
    print("")
    print("Sample trades (head):")
    print(tdf.head())

   # ---- REQUIRED DEBUG OUTPUT #2 (improved)
    tdf = trades_df.copy()
    if "hold_days" not in tdf.columns:
        tdf["hold_days"] = (tdf["exit_date"] - tdf["entry_date"]).dt.days
    
    losers = tdf[(tdf["hold_days"] <= 3) & (tdf["pnl"] < 0)].copy()
    
    if losers.empty:
        print("\nShort-hold losers (hold_days <= 3): NONE")
    else:
        losers = losers.sort_values(by=["pnl", "hold_days"], ascending=[True, True]).copy()
        losers["entry_date"] = pd.to_datetime(losers["entry_date"]).dt.strftime("%Y-%m-%d")
        losers["exit_date"] = pd.to_datetime(losers["exit_date"]).dt.strftime("%Y-%m-%d")
    
        # Add reason_exit if present (super useful for validating stop behavior)
        cols = ["ticker", "entry_date", "exit_date", "hold_days", "entry_price", "exit_price", "pnl"]
        if "reason_exit" in losers.columns:
            cols.append("reason_exit")
    
        print("\nShort-hold losers (hold_days <= 3):")
        print(losers[cols].to_string(index=False))



def main():
    print("Preparing data...")
    signals_atr = prepare_data()
    print("Running backtest...")
    equity_df, trades_df = backtest(signals_atr)
    compute_stats(equity_df, trades_df)


if __name__ == "__main__":
    main()
