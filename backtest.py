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
    MAX_POSITION_PCT,
)

# ===== Backtest configuration =====

INITIAL_EQUITY = float(os.environ.get("ACCOUNT_EQUITY", "10000"))
MIN_HOLD_DAYS = 3        # do not exit before this
EXIT_SIGNAL_BUFFER = 5   # signal must drop this much below floor to exit
MAX_HOLD_DAYS = 5        # max holding period per position (~weekly)
TOP_LONGS = 3            # max number of long positions
SHORT_THRESHOLD = 20     # final_signal threshold for shorts
LONG_THRESHOLD = 75      # final_signal threshold for longs
SHORT_FLOOR_EXIT = 30    # exit short if signal rebounds above this
LONG_FLOOR_EXIT = 65     # exit long if signal falls below this
TRADE_COST = 0.5         # per-trade cost assumption (placeholder)

BACKTEST_VERSION = "2025-12-14-Debug-2"


def prepare_universe() -> list[str]:
    if TICKERS:
        return [t.strip().upper() for t in TICKERS]
    # fallback to data/universe.csv via quant_report helper
    from quant_report import load_universe_df
    u = load_universe_df("data/universe.csv")
    return u["ticker"].tolist()


def run_backtest(period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    print(f"Backtest Version Check: {BACKTEST_VERSION}")
    print("Preparing data...")

    tickers = prepare_universe()
    prices = download_prices(tickers, period=period, interval=interval)

    # optionally compute ATR if needed downstream
    prices = add_atr(prices)

    # Factor pipeline (your real logic lives inside these)
    factor_df = fetch_factor_data(prices)
    scored = build_factor_scores(factor_df)
    signals = compute_full_signals(scored)

    # If your signals already include 'final_signal' per ticker per date, keep it.
    # Otherwise, default to a simple placeholder so the script runs.
    if "final_signal" not in signals.columns:
        signals = signals.copy()
        signals["final_signal"] = 50.0

    # Pivot close prices wide for daily equity tracking
    px = prices.pivot_table(index="date", columns="ticker", values="close").sort_index()
    dates = px.index

    equity = INITIAL_EQUITY
    equity_curve = []

    # Minimal placeholder strategy: do nothing but track equity.
    # (Your existing trade simulator likely replaces this.)
    for dt in dates:
        equity_curve.append({"date": dt, "equity": equity})

    out = pd.DataFrame(equity_curve).set_index("date")
    return out


def summarize(eq: pd.DataFrame) -> None:
    init = float(eq["equity"].iloc[0])
    final = float(eq["equity"].iloc[-1])
    total_return = final / init - 1.0
    dd = (eq["equity"] / eq["equity"].cummax() - 1.0).min()

    print("\n===== Backtest Results (1y) =====")
    print(f"Initial Equity: ${init:,.2f}")
    print(f"Final Equity:   ${final:,.2f}")
    print(f"Total Return:   {total_return*100:.2f}%")
    print(f"Max Drawdown:   {dd*100:.2f}%")


if __name__ == "__main__":
    print("Running backtest...")
    print(
        "DEBUG CONFIG:",
        {
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
        },
    )

    eq = run_backtest(period="1y", interval="1d")
    summarize(eq)
