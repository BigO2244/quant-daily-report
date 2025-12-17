import os
import pandas as pd
import numpy as np

from core.quant_report import download_prices, fetch_factor_data, add_atr, load_universe_df
from sleeves.sleeve_2.config import (
    UNIVERSE_CSV,
    TOP_LONGS,
    TOP_SHORTS,
    LONG_THRESHOLD,
    LONG_FLOOR_EXIT,
    EXIT_SIGNAL_BUFFER,
    MIN_HOLD_DAYS,
    MAX_HOLD_DAYS_LONG,
    MAX_HOLD_DAYS_SHORT,
    Z_EXTREME,
    Z_ENTRY_LONG,
    PE_CHANGE_20D_MAX_LONG_ENTRY,
    Z_SHORT_EXIT_MEAN_REVERT,
    CASH_PROXY_TICKER,
)
from sleeves.sleeve_2.signals import build_signals
from sleeves.sleeve_2.valuation import fetch_valuation_snapshot

INITIAL_EQUITY = float(os.environ.get("ACCOUNT_EQUITY", "10000"))

def prepare_universe() -> list[str]:
    u = load_universe_df(UNIVERSE_CSV)
    tickers = [t.strip().upper() for t in u["ticker"].tolist()]
    if CASH_PROXY_TICKER not in tickers:
        tickers.append(CASH_PROXY_TICKER)
    return tickers

def _get_price(px_map: dict, date: pd.Timestamp, ticker: str) -> float | None:
    try:
        px = px_map.get((date, ticker))
        if px is None or pd.isna(px):
            return None
        return float(px)
    except Exception:
        return None
        return float(px)
    except Exception:
        return None

def run_backtest(period: str = "1y", interval: str = "1d") -> tuple[pd.DataFrame, pd.DataFrame]:
    print("Sleeve 2 Backtest v1.0")
    print("Preparing data...")

    tickers = prepare_universe()
    prices = download_prices(tickers, period=period, interval=interval)
    prices = add_atr(prices)

    factor_df = fetch_factor_data(prices)
    val = fetch_valuation_snapshot([t for t in tickers if t != CASH_PROXY_TICKER])
    factor_df = factor_df.merge(val, on='ticker', how='left')

    factor_df["date"] = pd.to_datetime(factor_df["date"]).dt.normalize()
    px_map = {(d, t): c for d, t, c in zip(factor_df["date"], factor_df["ticker"], factor_df["close"])}
    sig = build_signals(factor_df)
    sig["date"] = pd.to_datetime(sig["date"]).dt.normalize()
    sig = sig[sig["ticker"] != CASH_PROXY_TICKER].copy()

    calendar = factor_df["date"].drop_duplicates().sort_values()

    cash = INITIAL_EQUITY
    equity = INITIAL_EQUITY
    positions = {}  # ticker -> dict(direction, shares, entry_date, entry_price, hold_days)
    trades = []

    def close_position(ticker: str, date: pd.Timestamp, reason: str):
        nonlocal cash, equity
        pos = positions.get(ticker)
        if not pos:
            return
        px = _get_price(px_map, date, ticker)
        if px is None:
            return

        direction = pos["direction"]
        shares = pos["shares"]
        entry_px = pos["entry_price"]

        pnl = (px - entry_px) * shares * direction
        cash += (px * shares) if direction == 1 else (entry_px * shares + pnl)

        equity = cash  # realized-only accounting (consistent with sleeve 1 today)

        trades.append({
            "ticker": ticker,
            "direction": direction,
            "entry_date": pos["entry_date"],
            "exit_date": date,
            "entry_price": entry_px,
            "exit_price": px,
            "shares": shares,
            "pnl": pnl,
            "hold_days": pos["hold_days"],
            "reason_exit": reason,
        })
        del positions[ticker]

    for date in calendar:
        day_sig = sig[sig["date"] == date].copy()

        for t in list(positions.keys()):
            positions[t]["hold_days"] += 1

        if day_sig.empty:
            continue

        # EXITS
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            row = day_sig[day_sig["ticker"] == ticker]
            if row.empty:
                continue

            hold_days = pos["hold_days"]
            if hold_days < MIN_HOLD_DAYS:
                continue

            z = row["z_pe"].iloc[0]
            score_long = row["score_long"].iloc[0]

            if pos["direction"] == 1:
                if pd.notna(z) and z >= Z_EXTREME:
                    close_position(ticker, date, "valuation_extreme_exit")
                    continue
                if hold_days >= MAX_HOLD_DAYS_LONG:
                    close_position(ticker, date, "max_hold_long")
                    continue
                if pd.notna(score_long) and score_long < (LONG_FLOOR_EXIT - EXIT_SIGNAL_BUFFER):
                    close_position(ticker, date, "score_floor_long")
                    continue
            else:
                if pd.notna(z) and z <= Z_SHORT_EXIT_MEAN_REVERT:
                    close_position(ticker, date, "short_mean_revert")
                    continue
                if hold_days >= MAX_HOLD_DAYS_SHORT:
                    close_position(ticker, date, "max_hold_short")
                    continue

        # ENTRIES
        current_longs = [t for t, p in positions.items() if p["direction"] == 1]
        current_shorts = [t for t, p in positions.items() if p["direction"] == -1]

        long_candidates = day_sig[
            (day_sig["score_long"] >= LONG_THRESHOLD) &
            (day_sig["z_pe"] <= Z_ENTRY_LONG) &
            True  # v1: trend gate disabled (snapshot P/E)
        ].sort_values("score_long", ascending=False)

        short_candidates = day_sig[
            (day_sig["z_pe"] >= Z_EXTREME)
        ].sort_values("score_short", ascending=False)

        def enter_position(ticker: str, direction: int):
            nonlocal cash
            px = _get_price(px_map, date, ticker)
            if px is None or px <= 0:
                return
            target_slots = TOP_LONGS + TOP_SHORTS
            alloc = cash / max(target_slots, 1)
            shares = int(alloc / px)
            if shares <= 0:
                return
            cash -= shares * px
            positions[ticker] = {
                "direction": direction,
                "shares": shares,
                "entry_date": date,
                "entry_price": px,
                "hold_days": 0,
            }

        for _, r in long_candidates.iterrows():
            if len(current_longs) >= TOP_LONGS:
                break
            tkr = r["ticker"]
            if tkr in positions:
                continue
            enter_position(tkr, 1)
            current_longs.append(tkr)

        for _, r in short_candidates.iterrows():
            if len(current_shorts) >= TOP_SHORTS:
                break
            tkr = r["ticker"]
            if tkr in positions:
                continue
            enter_position(tkr, -1)
            current_shorts.append(tkr)

    last_date = calendar.max()
    for t in list(positions.keys()):
        close_position(t, last_date, "end_of_backtest")

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame([{"date": last_date, "equity": equity}])
    return equity_df, trades_df

def main():
    equity_df, trades_df = run_backtest(period="1y", interval="1d")
    initial = INITIAL_EQUITY
    final = float(equity_df["equity"].iloc[-1]) if not equity_df.empty else initial
    total_return = (final / initial) - 1.0

    print("\n===== Sleeve 2 Results (1y) =====")
    print(f"Initial Equity: ${initial:,.2f}")
    print(f"Final Equity:   ${final:,.2f}")
    print(f"Total Return:   {total_return*100:,.2f}%")
    print(f"Number of trades: {len(trades_df)}")

    if not trades_df.empty:
        print("\nPnL by direction:")
        print(trades_df.groupby("direction")["pnl"].sum())
        print("\nPnL by ticker (top 10):")
        print(trades_df.groupby("ticker")["pnl"].sum().sort_values(ascending=False).head(10))
        print("\nExit reasons:")
        print(trades_df["reason_exit"].value_counts().head(10))

if __name__ == "__main__":
    assert __name__.startswith("sleeves.sleeve_2"), "Invalid import context for Sleeve 2"
    main()
