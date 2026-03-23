#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_PATH = REPO_ROOT / ".env"
if ENV_PATH.exists():
    with ENV_PATH.open("r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from core.quant_report import download_prices, load_universe_df
from macro_store import MacroStore
from regime.regime_engine import RegimeEngine


INDICATOR_COLS = [
    "spy_vs_50d",
    "spy_vs_50d_ewm",
    "spy_vs_200d",
    "spy_vs_200d_ewm",
    "ma50_vs_ma200",
    "ma50_vs_ma200_ewm",
    "spy_ret_63d",
    "spy_ret_63d_ewm",
    "vix",
    "vix_ewm",
    "vix_vs_ma63",
    "vix_vs_ma63_ewm",
    "vix_spike_10d",
    "pct_above_200d",
    "pct_above_200d_ewm",
    "pct_above_50d",
    "pct_above_50d_ewm",
    "adv_decline_cum",
    "adv_decline_cum_ewm",
    "yield_curve_2s10s",
    "yield_curve_2s10s_ewm",
    "hy_oas",
    "hy_oas_ewm",
    "fed_funds",
    "fed_funds_ewm",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose live regime inputs and outputs")
    parser.add_argument("--period", default="2y", help="yfinance lookback period for price downloads")
    parser.add_argument("--interval", default="1d", help="yfinance interval")
    parser.add_argument("--window", type=int, default=252, help="Trailing business days to summarize")
    return parser.parse_args()


def _print_header(title: str) -> None:
    print(title)
    print("-" * len(title))


def _print_macro_freshness() -> None:
    store = MacroStore(REPO_ROOT / "data" / "macro")
    _print_header("Macro Coverage")
    for series_id in ["T10Y2Y", "BAMLH0A0HYM2", "DFF"]:
        coverage = store.coverage(series_id)
        latest = store.get_latest(series_id)
        print(
            f"{series_id}: first={coverage['first']} last={coverage['last']} "
            f"n_obs={coverage['n_obs']} latest={latest}"
        )


def _download_market_data(period: str, interval: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    universe = load_universe_df(REPO_ROOT / "data" / "universe.csv")
    tickers = universe["ticker"].tolist() + ["SPY", "^VIX"]
    prices = download_prices(tickers, period=period, interval=interval)
    spy = prices.loc[prices["ticker"] == "SPY", ["date", "close"]].copy()
    vix = prices.loc[prices["ticker"] == "^VIX", ["date", "close"]].copy()
    uni = prices.loc[
        ~prices["ticker"].isin(["SPY", "^VIX"]),
        ["date", "ticker", "close"],
    ].copy()
    return prices, spy, vix, uni


def _print_price_coverage(prices: pd.DataFrame, spy: pd.DataFrame, vix: pd.DataFrame, universe: pd.DataFrame) -> None:
    _print_header("Price Coverage")
    print(
        f"all_prices: rows={len(prices)} tickers={prices['ticker'].nunique()} "
        f"start={prices['date'].min().date()} end={prices['date'].max().date()}"
    )
    print(
        f"spy_rows={len(spy)} spy_last={spy['date'].max().date()} "
        f"vix_rows={len(vix)} vix_last={vix['date'].max().date()} "
        f"universe_rows={len(universe)} universe_tickers={universe['ticker'].nunique()} "
        f"universe_last={universe['date'].max().date()}"
    )


def _print_latest_snapshot(result) -> None:
    latest_states = result.states.iloc[-1]
    latest_indicators = result.indicators.iloc[-1]

    _print_header("Latest States")
    print(latest_states.to_string())

    _print_header("Latest Indicators")
    for col in INDICATOR_COLS:
        print(f"{col}={latest_indicators.get(col)}")


def _print_recent_counts(result, window: int) -> None:
    states = result.states.tail(window)

    _print_header(f"Composite Counts Last {len(states)} Rows")
    print(states["composite_regime"].value_counts().to_string())

    _print_header(f"Dimension Counts Last {len(states)} Rows")
    for col in ["trend_state", "volatility_state", "breadth_state", "macro_state"]:
        print(f"[{col}]")
        print(states[col].value_counts().to_string())


def _print_tail(result, spy: pd.DataFrame, vix: pd.DataFrame) -> None:
    tail_cols = [
        "spy_vs_50d",
        "spy_vs_200d",
        "ma50_vs_ma200",
        "spy_ret_63d",
        "vix",
        "pct_above_200d",
        "yield_curve_2s10s",
        "spy_vs_50d_ewm",
        "vix_ewm",
        "pct_above_200d_ewm",
        "yield_curve_2s10s_ewm",
    ]
    _print_header("Input Tails")
    print("SPY")
    print(spy.tail(3).to_string(index=False))
    print("VIX")
    print(vix.tail(3).to_string(index=False))

    _print_header("Indicator Tail")
    print(result.indicators[tail_cols].tail(5).to_string())

    _print_header("State Tail")
    print(result.states.tail(5).to_string())


def main() -> None:
    args = parse_args()
    _print_macro_freshness()
    prices, spy, vix, universe = _download_market_data(args.period, args.interval)
    _print_price_coverage(prices, spy, vix, universe)

    result = RegimeEngine().run(
        spy_prices=spy,
        vix_prices=vix,
        universe_prices=universe,
    )
    _print_latest_snapshot(result)
    _print_recent_counts(result, args.window)
    _print_tail(result, spy, vix)


if __name__ == "__main__":
    main()
