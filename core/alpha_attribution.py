import logging
from typing import Optional

import numpy as np
import pandas as pd

from core.quant_report import download_prices

logger = logging.getLogger(__name__)


def compute_returns_from_equity(equity: pd.Series) -> pd.Series:
    series = pd.Series(equity).dropna()
    if series.empty:
        return pd.Series(dtype=float)
    series = series.sort_index()
    returns = series.pct_change(fill_method=None).dropna()
    return returns


def max_drawdown(equity: pd.Series) -> float:
    series = pd.Series(equity).dropna()
    if series.empty:
        return 0.0
    series = series.sort_index()
    peak = series.cummax()
    drawdown = (series / peak) - 1.0
    return float(drawdown.min())


def regress_alpha_beta(port_ret: pd.Series, bench_ret: pd.Series) -> dict:
    aligned = pd.concat([port_ret, bench_ret], axis=1, join="inner").dropna()
    if aligned.shape[0] < 2:
        return {"beta": None, "alpha_daily": None, "alpha_annual": None}
    slope, intercept = np.polyfit(
        aligned.iloc[:, 1].values, aligned.iloc[:, 0].values, 1
    )
    alpha_daily = float(intercept)
    beta = float(slope)
    return {
        "beta": beta,
        "alpha_daily": alpha_daily,
        "alpha_annual": alpha_daily * 252,
    }


def calc_alpha_stats(
    port_equity: pd.Series,
    bench_prices: pd.Series,
    window: int = 63,
) -> dict:
    port_series = pd.Series(port_equity).dropna().sort_index()
    bench_series = pd.Series(bench_prices).dropna().sort_index()

    out = {
        "port_cum_return": None,
        "bench_cum_return": None,
        "excess_cum_return": None,
        "beta_since": None,
        "alpha_ann_since": None,
        "beta_63d": None,
        "alpha_ann_63d": None,
        "tracking_error_ann": None,
        "info_ratio": None,
        "corr": None,
        "mdd_port": None,
        "mdd_bench": None,
        "n_days": 0,
        "rolling_63d_reason": None,
        "reason": None,
    }

    if port_series.empty or bench_series.empty:
        out["reason"] = "empty_port_or_benchmark_series"
        return out

    common_index = port_series.index.intersection(bench_series.index)
    if common_index.empty:
        out["reason"] = "no_overlapping_dates"
        return out

    port_series = port_series.loc[common_index]
    bench_series = bench_series.loc[common_index]

    if len(common_index) < 2:
        out["reason"] = "insufficient_aligned_history"
        return out

    out["port_cum_return"] = float(port_series.iloc[-1] / port_series.iloc[0] - 1.0)
    out["bench_cum_return"] = float(bench_series.iloc[-1] / bench_series.iloc[0] - 1.0)
    out["excess_cum_return"] = out["port_cum_return"] - out["bench_cum_return"]

    port_ret = compute_returns_from_equity(port_series)
    bench_ret = compute_returns_from_equity(bench_series)
    aligned = pd.concat([port_ret, bench_ret], axis=1, join="inner").dropna()
    aligned.columns = ["port_ret", "bench_ret"]
    out["n_days"] = int(len(aligned))

    if aligned.empty:
        out["reason"] = "no_aligned_return_history"
        return out

    excess = aligned["port_ret"] - aligned["bench_ret"]
    excess_std = float(excess.std()) if len(excess) > 1 else 0.0
    out["tracking_error_ann"] = excess_std * np.sqrt(252) if excess_std > 0 else 0.0
    out["info_ratio"] = (
        float(excess.mean()) / excess_std * np.sqrt(252) if excess_std > 0 else None
    )
    out["corr"] = float(aligned["port_ret"].corr(aligned["bench_ret"]))

    since = regress_alpha_beta(aligned["port_ret"], aligned["bench_ret"])
    out["beta_since"] = since["beta"]
    out["alpha_ann_since"] = since["alpha_annual"]

    if len(aligned) >= window:
        recent = aligned.tail(window)
        recent_reg = regress_alpha_beta(recent["port_ret"], recent["bench_ret"])
        out["beta_63d"] = recent_reg["beta"]
        out["alpha_ann_63d"] = recent_reg["alpha_annual"]
    else:
        out["rolling_63d_reason"] = f"insufficient_data_for_{window}d_window"

    out["mdd_port"] = max_drawdown(port_series)
    out["mdd_bench"] = max_drawdown(bench_series)

    return out


def load_benchmark_prices(
    ticker: str = "SPY",
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
    offline_fixture: bool = False,
) -> pd.Series:
    if offline_fixture:
        try:
            df = pd.read_csv("tests/fixtures/spy_prices.csv")
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            series = pd.Series(df["close"].values, index=df["date"], name=ticker)
            return series.dropna()
        except Exception as exc:
            logger.warning("[WARN] Offline benchmark fixture load failed: %s", exc)
            return pd.Series(dtype=float)

    try:
        prices = download_prices([ticker], period="5y", interval="1d")
    except Exception as exc:
        logger.warning("[WARN] Benchmark fetch failed for %s: %s", ticker, exc)
        return pd.Series(dtype=float)

    if prices.empty or prices["close"].isna().all():
        logger.warning("[WARN] Benchmark fetch returned empty data for %s", ticker)
        return pd.Series(dtype=float)

    df = prices[prices["ticker"] == ticker].copy()
    if df.empty:
        logger.warning("[WARN] Benchmark fetch returned no rows for %s", ticker)
        return pd.Series(dtype=float)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    if start is not None:
        df = df[df["date"] >= pd.to_datetime(start)]
    if end is not None:
        df = df[df["date"] <= pd.to_datetime(end)]

    series = pd.Series(df["close"].values, index=df["date"], name=ticker)
    if series.dropna().empty:
        logger.warning(
            "[WARN] Benchmark series is empty after filtering for %s", ticker
        )
        return pd.Series(dtype=float)

    return series.dropna()
