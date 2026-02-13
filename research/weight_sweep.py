"""Simple momentum/value weight sweep research harness."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from core.quant_report import download_prices


def run_weight_sweep(start: str, end: str) -> pd.DataFrame:
    tickers = ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLK"]
    px = download_prices(tickers, period="5y", interval="1d")
    px = px[(px["date"] >= start) & (px["date"] <= end)].copy()
    rets = px.pivot(index="date", columns="ticker", values="close").pct_change(fill_method=None).dropna(how="all")

    rows = []
    for w_mom in np.round(np.arange(0.0, 1.0001, 0.05), 2):
        w_val = round(1.0 - w_mom, 2)
        signal = w_mom * rets.rolling(21).mean() + w_val * (-rets.rolling(5).mean())
        weights = signal.rank(axis=1, pct=True)
        weights = weights.div(weights.sum(axis=1), axis=0).fillna(0.0)
        port = (weights.shift(1) * rets).sum(axis=1).fillna(0.0)
        eq = (1.0 + port).cumprod()
        drawdown = eq / eq.cummax() - 1.0
        vol = float(port.std() * np.sqrt(252)) if len(port) > 2 else 0.0
        sharpe = float((port.mean() / port.std()) * np.sqrt(252)) if len(port) > 10 and port.std() > 0 else 0.0
        turnover = float(weights.diff().abs().sum(axis=1).mean())
        rows.append({
            "w_mom": w_mom,
            "w_val": w_val,
            "cumulative_return": float(eq.iloc[-1] - 1.0) if not eq.empty else 0.0,
            "max_drawdown": float(drawdown.min()) if not drawdown.empty else 0.0,
            "vol": vol,
            "sharpe": sharpe,
            "turnover": turnover,
            "hit_rate": float((port > 0).mean()) if not port.empty else 0.0,
        })
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()
    out_dir = Path("outputs/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"weight_sweep_{args.start}_{args.end}.csv"
    run_weight_sweep(args.start, args.end).to_csv(out_path, index=False)
    print(out_path)


if __name__ == "__main__":
    main()
