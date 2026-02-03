# sleeve2/sleeve2_backtest.py
from __future__ import annotations

import pandas as pd

from sleeve2.sleeve2_engine import (
    load_universe,
    download_prices,
    fetch_trailing_pe_snapshot,
    compute_bucket_zscores,
    compute_momentum,
    build_equity_candidates,
    pick_positions,
    build_target_weights,
)


def backtest(start: str = "2023-01-01") -> pd.DataFrame:
    """Very simple Sleeve 2 V1 backtest (signal -> equal weight -> daily equity curve)."""
    univ = load_universe()
    tickers = univ["ticker"].tolist()

    close = download_prices(tickers, start=start)
    close = close.sort_index()

    pe = fetch_trailing_pe_snapshot(tickers)
    pe_z = compute_bucket_zscores(univ, pe)

    # momentum computed on last available date; in V1 we select once and hold
    mom = compute_momentum(close)

    cands = build_equity_candidates(pe_z, mom)
    selected = pick_positions(cands)
    weights = build_target_weights(selected)

    if not weights:
        raise RuntimeError(
            "No positions selected (empty weights). Check filters / data availability."
        )

    ret = close[selected].pct_change(fill_method=None).fillna(0.0)
    w = pd.Series(weights)
    port_ret = ret.mul(w, axis=1).sum(axis=1)
    equity = (1.0 + port_ret).cumprod()

    out = pd.DataFrame({"date": equity.index, "equity": equity.values}).set_index(
        "date"
    )
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
