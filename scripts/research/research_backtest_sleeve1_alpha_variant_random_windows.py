#!/usr/bin/env python3
"""Random-window stress test for Sleeve 1 alpha variant."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from research_backtest_sleeve1_alpha_variant import (
    _synthetic_prices,
    run_backtest,
    sleeve1,
)

MAX_END = pd.Timestamp("2025-12-31")


def _load_trading_dates(synthetic: bool) -> pd.DatetimeIndex:
    start = pd.Timestamp("2005-01-01")
    end = MAX_END
    original_download = sleeve1.download_prices

    def _download_prices_full_history(tickers, period="1y", interval="1d"):
        if synthetic:
            return _synthetic_prices(list(tickers), start=start, end=end)
        prices = original_download(tickers=tickers, period="max", interval=interval)
        return prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()

    sleeve1.download_prices = _download_prices_full_history
    try:
        signals = sleeve1.prepare_data()
    finally:
        sleeve1.download_prices = original_download

    dates = pd.DatetimeIndex(pd.to_datetime(signals["date"]).dropna().unique()).sort_values()
    if dates.empty:
        raise RuntimeError("No trading dates available from Sleeve 1 data.")
    return dates


def _sample_windows(
    rng: np.random.Generator,
    trading_dates: pd.DatetimeIndex,
    n_windows: int,
    years: int,
    sample_start_min: pd.Timestamp,
    sample_start_max: pd.Timestamp,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    seen: set[tuple[str, str]] = set()
    max_attempts = 10000
    attempts = 0

    span_days = int((sample_start_max - sample_start_min).days)
    while len(windows) < n_windows and attempts < max_attempts:
        attempts += 1
        sampled_start = sample_start_min + pd.Timedelta(days=int(rng.integers(0, span_days + 1)))

        start_idx = trading_dates.searchsorted(sampled_start)
        if start_idx >= len(trading_dates):
            continue
        start_date = pd.Timestamp(trading_dates[start_idx])

        target_end = start_date + pd.DateOffset(years=years) - pd.Timedelta(days=1)
        if target_end > MAX_END or trading_dates[-1] < target_end:
            continue

        end_idx = trading_dates.searchsorted(target_end, side="right") - 1
        if end_idx < 0:
            continue
        end_date = pd.Timestamp(trading_dates[end_idx])
        if end_date <= start_date:
            continue

        key = (start_date.date().isoformat(), end_date.date().isoformat())
        if key in seen:
            continue
        seen.add(key)
        windows.append((start_date, end_date))

    if len(windows) < n_windows:
        raise RuntimeError(f"Unable to sample {n_windows} windows of {years}y after {max_attempts} attempts")
    return windows


def _window_metrics(
    seed: int,
    windows: list[tuple[pd.Timestamp, pd.Timestamp]],
    synthetic: bool,
    apply_costs: bool,
    cost_bps: float,
) -> pd.DataFrame:
    rows: list[dict] = []
    for idx, (start_date, end_date) in enumerate(windows, start=1):
        summary, _ = run_backtest(
            start=start_date,
            end=end_date,
            synthetic=synthetic,
            apply_costs=apply_costs,
            cost_bps=cost_bps,
        )
        record = summary.iloc[0].to_dict()
        gross_port_cagr = float(record["gross_cagr"])
        net_port_cagr = float(record["net_cagr"])
        spy_cagr = float(record["spy_cagr"])
        rows.append(
            {
                "seed": seed,
                "window_id": idx,
                "start_date": start_date.date().isoformat(),
                "end_date": end_date.date().isoformat(),
                "avg_turnover": float(record["avg_turnover"]),
                "cost_bps": float(record["cost_bps"]),
                "gross_port_total_return": float(record["gross_total_return"]),
                "gross_port_cagr": gross_port_cagr,
                "gross_port_vol": float(record["gross_vol"]),
                "gross_port_sharpe": float(record["gross_sharpe"]),
                "gross_port_max_drawdown": float(record["gross_max_drawdown"]),
                "gross_port_beta_vs_spy": float(record["gross_beta_vs_spy"]),
                "net_port_total_return": float(record["net_total_return"]),
                "net_port_cagr": net_port_cagr,
                "net_port_vol": float(record["net_vol"]),
                "net_port_sharpe": float(record["net_sharpe"]),
                "net_port_max_drawdown": float(record["net_max_drawdown"]),
                "net_port_beta_vs_spy": float(record["net_beta_vs_spy"]),
                "spy_total_return": float(record["spy_total_return"]),
                "spy_cagr": spy_cagr,
                "spy_vol": float(record["spy_vol"]),
                "spy_sharpe": float(record["spy_sharpe"]),
                "spy_max_drawdown": float(record["spy_max_drawdown"]),
                "gross_excess_cagr": gross_port_cagr - spy_cagr,
                "net_excess_cagr": net_port_cagr - spy_cagr,
            }
        )
    return pd.DataFrame(rows)


def _summarize_windows(df_3y: pd.DataFrame, df_5y: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []

    def add_percentiles(df: pd.DataFrame, horizon: str) -> None:
        for metric in ["net_port_cagr", "net_port_max_drawdown", "net_excess_cagr"]:
            s = df[metric]
            rows.append(
                {
                    "section": "percentile",
                    "horizon": horizon,
                    "metric": metric,
                    "p5": float(np.percentile(s, 5)),
                    "p25": float(np.percentile(s, 25)),
                    "p50": float(np.percentile(s, 50)),
                    "p75": float(np.percentile(s, 75)),
                    "p95": float(np.percentile(s, 95)),
                }
            )

    def add_worst(df: pd.DataFrame, horizon: str) -> None:
        worst_excess = df[df["net_excess_cagr"] == df["net_excess_cagr"].min()]
        for _, r in worst_excess.iterrows():
            rows.append(
                {
                    "section": "worst_excess_cagr",
                    "horizon": horizon,
                    "metric": "net_excess_cagr",
                    "window_id": int(r["window_id"]),
                    "start_date": r["start_date"],
                    "end_date": r["end_date"],
                    "value": float(r["net_excess_cagr"]),
                }
            )

        worst_dd = df[df["net_port_max_drawdown"] == df["net_port_max_drawdown"].min()]
        for _, r in worst_dd.iterrows():
            rows.append(
                {
                    "section": "worst_max_drawdown",
                    "horizon": horizon,
                    "metric": "net_port_max_drawdown",
                    "window_id": int(r["window_id"]),
                    "start_date": r["start_date"],
                    "end_date": r["end_date"],
                    "value": float(r["net_port_max_drawdown"]),
                }
            )

    add_percentiles(df_3y, "3y")
    add_percentiles(df_5y, "5y")
    add_worst(df_3y, "3y")
    add_worst(df_5y, "5y")
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n3", type=int, default=10)
    parser.add_argument("--n5", type=int, default=10)
    parser.add_argument("--outdir", default="outputs/research")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--cost-bps", type=float, default=25.0)
    parser.add_argument("--apply-costs", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rng = np.random.default_rng(args.seed)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    trading_dates = _load_trading_dates(synthetic=args.synthetic)

    windows_3y = _sample_windows(
        rng=rng,
        trading_dates=trading_dates,
        n_windows=args.n3,
        years=3,
        sample_start_min=pd.Timestamp("2005-01-01"),
        sample_start_max=pd.Timestamp("2023-12-31"),
    )
    windows_5y = _sample_windows(
        rng=rng,
        trading_dates=trading_dates,
        n_windows=args.n5,
        years=5,
        sample_start_min=pd.Timestamp("2005-01-01"),
        sample_start_max=pd.Timestamp("2021-12-31"),
    )

    df_3y = _window_metrics(
        seed=args.seed,
        windows=windows_3y,
        synthetic=args.synthetic,
        apply_costs=args.apply_costs,
        cost_bps=args.cost_bps,
    )
    df_5y = _window_metrics(
        seed=args.seed,
        windows=windows_5y,
        synthetic=args.synthetic,
        apply_costs=args.apply_costs,
        cost_bps=args.cost_bps,
    )
    summary_df = _summarize_windows(df_3y=df_3y, df_5y=df_5y)

    df_3y.to_csv(outdir / "sleeve1_alpha_random_windows_3y.csv", index=False)
    df_5y.to_csv(outdir / "sleeve1_alpha_random_windows_5y.csv", index=False)
    summary_df.to_csv(outdir / "sleeve1_alpha_random_windows_summary.csv", index=False)


if __name__ == "__main__":
    main()
