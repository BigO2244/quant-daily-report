print("RUNNING: SLEEVE 2 BACKTEST")


# sleeve2/sleeve2_backtest.py
import pandas as pd
import numpy as np

from sleeve2.sleeve2_engine import (
    load_universe,
    download_prices,
    fetch_trailing_pe_snapshot,
    compute_bucket_zscores,
    compute_momentum,
    build_equity_candidates,
    pick_positions,
    build_target_weights,
    TREASURY_TICKER,
    REB_FREQ,
    TOP_LONGS,
)

def backtest(start: str = "2023-01-01") -> pd.DataFrame:
    univ = load_universe()
    tickers = sorted(univ["ticker"].unique().tolist())
    all_tickers = tickers + [TREASURY_TICKER]

    close = download_prices(all_tickers, start=start)
    close = close.dropna(how="all")

    # Rebalance dates (weekly Friday)
    rebal_dates = close.resample(REB_FREQ).last().index
    rebal_dates = [d for d in rebal_dates if d in close.index]

    # V1: P/E snapshot (today). Used for ranking across buckets consistently.
    pe_snap = fetch_trailing_pe_snapshot(tickers)
    pe_z_df = compute_bucket_zscores(univ, pe_snap)

    weights_hist = []
    for d in rebal_dates:
        hist = close.loc[:d]
        if len(hist) < 40:
            continue

        mom = compute_momentum(hist)
        cands = build_equity_candidates(pe_z_df, mom)
        selected = pick_positions(cands, top_n=TOP_LONGS)
        w = build_target_weights(selected)
        weights_hist.append((d, w))

    # Build daily portfolio returns from weights applied forward until next rebalance
    w_df = pd.DataFrame(
        [
            {"date": d, **w}
            for d, w in weights_hist
        ]
    ).set_index("date").sort_index()

    # Align columns with prices
    w_df = w_df.reindex(columns=close.columns).fillna(0.0)

    # Forward fill weights until next rebalance day
    w_daily = w_df.reindex(close.index).ffill().fillna(0.0)

    # Daily returns
    ret = close.pct_change(fill_method=None).fillna(0.0)
    port_ret = (w_daily * ret).sum(axis=1)

    equity_curve = (1.0 + port_ret).cumprod()
    out = pd.DataFrame({
        "port_ret": port_ret,
        "equity": equity_curve,
    })

    return out


if __name__ == "__main__":
    df = backtest(start="2023-01-01")
    total = df["equity"].iloc[-1] - 1.0
    dd = (df["equity"] / df["equity"].cummax() - 1.0).min()

    print("===== Sleeve 2 Backtest (V1) =====")
    print(f"Final Equity Multiple: {df['equity'].iloc[-1]:.3f}x")
    print(f"Total Return: {total*100:.2f}%")
    print(f"Max Drawdown: {dd*100:.2f}%")
    print("Last 5 rows:")
    print(df.tail())
