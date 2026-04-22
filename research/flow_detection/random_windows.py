from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .backtest import FlowBacktestConfig, run_strategy_backtest


@dataclass(frozen=True)
class WindowSample:
    horizon_years: int
    start_date: str
    end_date: str


def sample_randomized_windows(
    trading_dates: pd.Index | list[pd.Timestamp],
    *,
    horizon_years: int,
    num_samples: int,
    seed: int = 7,
) -> list[WindowSample]:
    dates = pd.DatetimeIndex(pd.to_datetime(list(trading_dates))).sort_values().unique()
    valid: list[WindowSample] = []
    for dt in dates:
        target_end = dt + pd.DateOffset(years=horizon_years)
        end_candidates = dates[(dates >= target_end - pd.Timedelta(days=10)) & (dates <= target_end + pd.Timedelta(days=10))]
        if len(end_candidates) == 0:
            end_candidates = dates[dates <= target_end]
        if len(end_candidates) == 0:
            continue
        end_dt = end_candidates[-1]
        if end_dt <= dt:
            continue
        valid.append(WindowSample(horizon_years=horizon_years, start_date=str(dt.date()), end_date=str(end_dt.date())))
    if not valid:
        return []
    rng = random.Random(seed + horizon_years)
    if len(valid) >= num_samples:
        return rng.sample(valid, num_samples)
    return [rng.choice(valid) for _ in range(num_samples)]


def run_randomized_window_analysis(
    signals: pd.DataFrame,
    *,
    config: FlowBacktestConfig,
    window_years: Iterable[int],
    num_sims: int,
    seed: int = 7,
) -> tuple[pd.DataFrame, dict]:
    signals = signals.copy()
    signals["date"] = pd.to_datetime(signals["date"])
    trading_dates = pd.DatetimeIndex(signals["date"].unique()).sort_values()
    signal_index = signals.sort_values(["date", "ticker"]).set_index("date")
    returns_matrix = signals.pivot(index="date", columns="ticker", values="close").sort_index().pct_change().shift(-1)
    records: list[dict] = []
    for horizon in window_years:
        samples = sample_randomized_windows(trading_dates, horizon_years=int(horizon), num_samples=num_sims, seed=seed)
        for sim_idx, sample in enumerate(samples, start=1):
            window_signals = signal_index.loc[pd.Timestamp(sample.start_date): pd.Timestamp(sample.end_date)].reset_index()
            window_returns = returns_matrix.loc[pd.Timestamp(sample.start_date): pd.Timestamp(sample.end_date)]
            baseline = run_strategy_backtest(
                window_signals,
                strategy="baseline",
                config=config,
                start_date=sample.start_date,
                end_date=sample.end_date,
                returns_matrix=window_returns,
            )["summary"]
            flow = run_strategy_backtest(
                window_signals,
                strategy="flow_filtered",
                config=config,
                start_date=sample.start_date,
                end_date=sample.end_date,
                returns_matrix=window_returns,
            )["summary"]
            records.append(
                {
                    "horizon_years": int(horizon),
                    "simulation_id": sim_idx,
                    "start_date": sample.start_date,
                    "end_date": sample.end_date,
                    "baseline_cagr": baseline.get("cagr"),
                    "baseline_sharpe": baseline.get("sharpe"),
                    "baseline_max_drawdown": baseline.get("max_drawdown"),
                    "baseline_annualised_vol": baseline.get("annualised_vol"),
                    "baseline_avg_turnover": baseline.get("avg_turnover"),
                    "baseline_excess_return_vs_spy": baseline.get("excess_return_vs_spy"),
                    "flow_cagr": flow.get("cagr"),
                    "flow_sharpe": flow.get("sharpe"),
                    "flow_max_drawdown": flow.get("max_drawdown"),
                    "flow_annualised_vol": flow.get("annualised_vol"),
                    "flow_avg_turnover": flow.get("avg_turnover"),
                    "flow_excess_return_vs_spy": flow.get("excess_return_vs_spy"),
                    "baseline_beats_spy": _beats_spy(baseline),
                    "flow_beats_spy": _beats_spy(flow),
                    "flow_beats_baseline": _flow_beats_baseline(flow, baseline),
                }
            )
    results = pd.DataFrame(records)
    return results, summarize_randomized_windows(results)


def summarize_randomized_windows(results: pd.DataFrame) -> dict:
    if results.empty:
        return {"windows": []}
    windows: list[dict] = []
    for horizon, block in results.groupby("horizon_years"):
        windows.append(
            {
                "horizon_years": int(horizon),
                "simulations": int(len(block)),
                "baseline": _metric_summary(block, prefix="baseline"),
                "flow_filtered": _metric_summary(block, prefix="flow"),
                "pct_windows_beating_benchmark_baseline": round(float(block["baseline_beats_spy"].mean()), 4),
                "pct_windows_beating_benchmark_flow": round(float(block["flow_beats_spy"].mean()), 4),
                "pct_windows_flow_beats_baseline": round(float(block["flow_beats_baseline"].mean()), 4),
            }
        )
    return {"windows": windows}


def _metric_summary(block: pd.DataFrame, *, prefix: str) -> dict:
    out = {}
    for metric in ("cagr", "annualised_vol", "sharpe", "max_drawdown", "avg_turnover", "excess_return_vs_spy"):
        col = f"{prefix}_{metric}"
        if col not in block:
            continue
        series = pd.to_numeric(block[col], errors="coerce").dropna()
        out[metric] = {
            "mean": round(float(series.mean()), 6) if not series.empty else None,
            "median": round(float(series.median()), 6) if not series.empty else None,
            "p25": round(float(series.quantile(0.25)), 6) if not series.empty else None,
            "p75": round(float(series.quantile(0.75)), 6) if not series.empty else None,
        }
    return out


def _beats_spy(summary: dict) -> bool:
    value = summary.get("excess_return_vs_spy")
    return bool(value is not None and value > 0)


def _flow_beats_baseline(flow: dict, baseline: dict) -> bool:
    flow_sharpe = flow.get("sharpe")
    base_sharpe = baseline.get("sharpe")
    return bool(flow_sharpe is not None and base_sharpe is not None and flow_sharpe > base_sharpe)
