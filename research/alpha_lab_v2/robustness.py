from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import StrategySpec, prepare_backtest_inputs, run_backtest_prepared


@dataclass(frozen=True)
class WindowSample:
    horizon_years: int
    start_date: str
    end_date: str


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
    best_single_change_name: str,
) -> tuple[pd.DataFrame, dict]:
    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[(frame["date"] >= pd.Timestamp(start_date)) & (frame["date"] <= pd.Timestamp(end_date))]
    trading_dates = pd.DatetimeIndex(frame["date"].unique()).sort_values()
    records = []
    for years in window_years:
        samples = sample_randomized_windows(trading_dates, horizon_years=years, num_samples=num_sims, seed=seed)
        for sim_idx, sample in enumerate(samples, start=1):
            window_frame, window_returns, window_dates = prepare_backtest_inputs(
                frame,
                start_date=sample.start_date,
                end_date=sample.end_date,
            )
            summaries = {
                spec.name: run_backtest_prepared(window_frame, window_returns, window_dates, spec)["summary"]
                for spec in specs
            }
            baseline = summaries[baseline_name]
            best_single = summaries[best_single_change_name]
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
                        "beats_best_single_change": bool(summary.get("sharpe") is not None and best_single.get("sharpe") is not None and summary.get("sharpe") > best_single.get("sharpe")),
                    }
                )
    results = pd.DataFrame(records)
    return results, summarize_randomized_windows(results)


def sample_randomized_windows(
    trading_dates: pd.Index | list[pd.Timestamp],
    *,
    horizon_years: int,
    num_samples: int,
    seed: int,
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

    rng = np.random.default_rng(seed + int(horizon_years))
    if len(valid) >= num_samples:
        indices = rng.choice(len(valid), size=num_samples, replace=False)
    else:
        indices = rng.choice(len(valid), size=num_samples, replace=True)
    return [valid[int(idx)] for idx in indices]


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
                    "pct_windows_beating_best_single_change": round(float(yr_block["beats_best_single_change"].mean()), 4),
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
