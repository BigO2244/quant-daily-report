from __future__ import annotations

import pandas as pd

from research.flow_detection.random_windows import sample_randomized_windows

from .engine import StrategySpec, run_backtest


def run_randomized_windows(
    signals: pd.DataFrame,
    *,
    specs: list[StrategySpec],
    start_date: str,
    end_date: str,
    window_years: list[int],
    num_sims: int,
    seed: int,
    baseline_name: str,
) -> tuple[pd.DataFrame, dict]:
    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[(frame["date"] >= pd.Timestamp(start_date)) & (frame["date"] <= pd.Timestamp(end_date))]
    trading_dates = pd.DatetimeIndex(frame["date"].unique()).sort_values()
    records = []
    for years in window_years:
        samples = sample_randomized_windows(trading_dates, horizon_years=years, num_samples=num_sims, seed=seed)
        for sim_idx, sample in enumerate(samples, start=1):
            summaries = {
                spec.name: run_backtest(frame, spec, start_date=sample.start_date, end_date=sample.end_date)["summary"]
                for spec in specs
            }
            baseline = summaries[baseline_name]
            for spec in specs:
                summary = summaries[spec.name]
                records.append(
                    {
                        "strategy": spec.name,
                        "hypothesis_id": spec.hypothesis_id,
                        "horizon_years": years,
                        "simulation_id": sim_idx,
                        "start_date": sample.start_date,
                        "end_date": sample.end_date,
                        "cagr": summary.get("cagr"),
                        "annualised_vol": summary.get("annualised_vol"),
                        "sharpe": summary.get("sharpe"),
                        "max_drawdown": summary.get("max_drawdown"),
                        "excess_return_vs_spy": summary.get("excess_return_vs_spy"),
                        "beats_spy": bool(summary.get("excess_return_vs_spy") is not None and summary.get("excess_return_vs_spy") > 0),
                        "beats_baseline": bool(summary.get("sharpe") is not None and baseline.get("sharpe") is not None and summary.get("sharpe") > baseline.get("sharpe")),
                    }
                )
    results = pd.DataFrame(records)
    return results, summarize_randomized_windows(results)


def summarize_randomized_windows(results: pd.DataFrame) -> dict:
    if results.empty:
        return {"strategies": []}
    strategies = []
    for (strategy, hypothesis_id), block in results.groupby(["strategy", "hypothesis_id"]):
        windows = []
        for years, yr_block in block.groupby("horizon_years"):
            windows.append(
                {
                    "horizon_years": int(years),
                    "simulations": int(len(yr_block)),
                    "cagr": _metric_summary(yr_block["cagr"]),
                    "sharpe": _metric_summary(yr_block["sharpe"]),
                    "max_drawdown": _metric_summary(yr_block["max_drawdown"]),
                    "pct_windows_beating_spy": round(float(yr_block["beats_spy"].mean()), 4),
                    "pct_windows_beating_baseline": round(float(yr_block["beats_baseline"].mean()), 4),
                }
            )
        strategies.append({"strategy": strategy, "hypothesis_id": hypothesis_id, "windows": windows})
    return {"strategies": strategies}


def _metric_summary(series: pd.Series) -> dict:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return {
        "mean": round(float(values.mean()), 6) if not values.empty else None,
        "median": round(float(values.median()), 6) if not values.empty else None,
        "p25": round(float(values.quantile(0.25)), 6) if not values.empty else None,
        "p75": round(float(values.quantile(0.75)), 6) if not values.empty else None,
    }
