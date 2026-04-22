from __future__ import annotations

import pandas as pd

from .random_windows import sample_randomized_windows
from .v2_backtest import FlowBacktestV2Config, run_strategy_backtest_v2


def run_randomized_window_suite_v2(
    signals: pd.DataFrame,
    *,
    config: FlowBacktestV2Config,
    window_years: list[int],
    num_sims: int,
    seed: int = 7,
) -> tuple[pd.DataFrame, dict]:
    signals = signals.copy()
    signals["date"] = pd.to_datetime(signals["date"])
    trading_dates = pd.DatetimeIndex(signals["date"].unique()).sort_values()
    strategies = ["baseline", "participation_entry", "participation_exit", "regime_conditional_participation"]
    records = []
    for horizon in window_years:
        samples = sample_randomized_windows(trading_dates, horizon_years=int(horizon), num_samples=num_sims, seed=seed)
        for sim_idx, sample in enumerate(samples, start=1):
            summaries = {
                strategy: run_strategy_backtest_v2(
                    signals,
                    strategy=strategy,
                    config=config,
                    start_date=sample.start_date,
                    end_date=sample.end_date,
                )["summary"]
                for strategy in strategies
            }
            row = {
                "horizon_years": int(horizon),
                "simulation_id": sim_idx,
                "start_date": sample.start_date,
                "end_date": sample.end_date,
            }
            for strategy, summary in summaries.items():
                prefix = strategy
                for metric in ("cagr", "annualised_vol", "sharpe", "max_drawdown", "avg_turnover", "excess_return_vs_spy"):
                    row[f"{prefix}_{metric}"] = summary.get(metric)
                row[f"{prefix}_beats_baseline"] = _beats(summary.get("sharpe"), summaries["baseline"].get("sharpe"))
                row[f"{prefix}_beats_spy"] = bool(summary.get("excess_return_vs_spy") is not None and summary.get("excess_return_vs_spy") > 0)
            records.append(row)
    results = pd.DataFrame(records)
    return results, summarize_randomized_window_suite_v2(results)


def summarize_randomized_window_suite_v2(results: pd.DataFrame) -> dict:
    if results.empty:
        return {"windows": []}
    strategies = ["baseline", "participation_entry", "participation_exit", "regime_conditional_participation"]
    windows = []
    for horizon, block in results.groupby("horizon_years"):
        window_summary = {"horizon_years": int(horizon), "simulations": int(len(block)), "strategies": {}}
        for strategy in strategies:
            window_summary["strategies"][strategy] = _metric_summary(block, strategy)
            window_summary["strategies"][strategy]["pct_windows_beating_spy"] = round(float(block[f"{strategy}_beats_spy"].mean()), 4)
            if strategy != "baseline":
                window_summary["strategies"][strategy]["pct_windows_beating_baseline"] = round(float(block[f"{strategy}_beats_baseline"].mean()), 4)
        windows.append(window_summary)
    return {"windows": windows}


def _metric_summary(block: pd.DataFrame, strategy: str) -> dict:
    out = {}
    for metric in ("cagr", "annualised_vol", "sharpe", "max_drawdown", "avg_turnover", "excess_return_vs_spy"):
        series = pd.to_numeric(block[f"{strategy}_{metric}"], errors="coerce").dropna()
        out[metric] = {
            "mean": round(float(series.mean()), 6) if not series.empty else None,
            "median": round(float(series.median()), 6) if not series.empty else None,
            "p25": round(float(series.quantile(0.25)), 6) if not series.empty else None,
            "p75": round(float(series.quantile(0.75)), 6) if not series.empty else None,
        }
    return out


def _beats(value: float | None, baseline: float | None) -> bool:
    return bool(value is not None and baseline is not None and value > baseline)
