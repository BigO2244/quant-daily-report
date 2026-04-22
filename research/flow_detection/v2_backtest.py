from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .backtest import FlowBacktestConfig, run_strategy_backtest


@dataclass(frozen=True)
class FlowBacktestV2Config(FlowBacktestConfig):
    exit_trim_fraction: float = 1.0


def run_strategy_backtest_v2(
    signals: pd.DataFrame,
    *,
    strategy: str,
    config: FlowBacktestV2Config,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    if strategy == "baseline":
        return run_strategy_backtest(signals, strategy="baseline", config=config, start_date=start_date, end_date=end_date)

    frame = signals.copy()
    if strategy == "participation_entry":
        frame = frame.copy()
        frame["flow_active"] = frame["participation_entry_signal"]
        return run_strategy_backtest(frame, strategy="flow_filtered", config=config, start_date=start_date, end_date=end_date)

    if strategy == "regime_conditional_participation":
        frame = frame.copy()
        frame["flow_active"] = frame["regime_conditional_entry_signal"]
        return run_strategy_backtest(frame, strategy="flow_filtered", config=config, start_date=start_date, end_date=end_date)

    if strategy == "participation_exit":
        return _run_exit_overlay(frame, config=config, start_date=start_date, end_date=end_date)

    raise ValueError(f"Unknown strategy: {strategy}")


def _run_exit_overlay(
    signals: pd.DataFrame,
    *,
    config: FlowBacktestV2Config,
    start_date: str | None,
    end_date: str | None,
) -> dict:
    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if start_date:
        frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    frame = frame.sort_values(["date", "ticker"]).reset_index(drop=True)
    returns_matrix = frame.pivot(index="date", columns="ticker", values="close").sort_index().pct_change().shift(-1)
    trading_dates = list(returns_matrix.index[:-1])

    prev_weights = pd.Series(dtype=float)
    nav = 1.0
    nav_records = []
    daily_records = []
    weight_records = []
    holdings_spells: dict[str, pd.Timestamp] = {}
    holding_durations: list[int] = []

    for dt in trading_dates:
        daily = frame[frame["date"] == dt].sort_values("momentum_score", ascending=False)
        candidates = daily[daily["signal_ready_v2"] & daily["momentum_score"].notna()].copy()
        baseline = candidates.head(config.top_n).copy()
        trimmed = baseline[~baseline["exhaustion_flow"]].copy()
        if len(trimmed) < config.top_n and config.fallback_behavior == "baseline_fill":
            refill = candidates[~candidates["ticker"].isin(set(trimmed["ticker"]))].head(config.top_n - len(trimmed))
            trimmed = pd.concat([trimmed, refill], ignore_index=True)
        if trimmed.empty:
            new_weights = pd.Series(dtype=float)
        else:
            new_weights = pd.Series(1.0 / len(trimmed), index=trimmed["ticker"].astype(str), dtype=float)

        turnover_value = _compute_turnover(prev_weights, new_weights)
        next_returns = returns_matrix.loc[dt]
        gross_return = float(next_returns.reindex(new_weights.index).fillna(0.0).mul(new_weights).sum()) if not new_weights.empty else 0.0
        net_return = gross_return - turnover_value * (config.transaction_cost_bps / 10000.0)
        nav *= (1.0 + net_return)

        _update_holding_spells(dt, prev_weights, new_weights, holdings_spells, holding_durations)

        daily_records.append(
            {
                "date": dt,
                "strategy": "participation_exit",
                "gross_return": gross_return,
                "net_return": net_return,
                "turnover": turnover_value,
                "holdings_count": int(len(new_weights)),
                "trimmed_count": int(len(baseline) - len(trimmed)),
            }
        )
        nav_records.append({"date": dt, "nav": nav})
        weight_records.append(new_weights.rename(dt))
        prev_weights = new_weights

    _close_remaining_spells(trading_dates[-1] if trading_dates else None, holdings_spells, holding_durations)
    nav_df = pd.DataFrame(nav_records)
    daily_df = pd.DataFrame(daily_records)
    weights_df = pd.DataFrame(weight_records).fillna(0.0) if weight_records else pd.DataFrame()

    nav_series = pd.Series(nav_df["nav"].values, index=pd.to_datetime(nav_df["date"]), name="nav") if not nav_df.empty else pd.Series(dtype=float)
    return_series = pd.Series(daily_df["net_return"].values, index=pd.to_datetime(daily_df["date"]), name="return") if not daily_df.empty else pd.Series(dtype=float)
    benchmark_returns = pd.Series(returns_matrix["SPY"].values, index=returns_matrix.index, name="SPY") if "SPY" in returns_matrix.columns else None
    if benchmark_returns is not None and not daily_df.empty:
        benchmark_returns = benchmark_returns.reindex(pd.to_datetime(daily_df["date"]))
    from alpha_stack.research.metrics import summarise_performance, turnover as avg_turnover

    summary = summarise_performance(nav_series, returns=return_series, benchmark_returns=benchmark_returns, label="participation_exit") if not nav_series.empty else {}
    summary.update(
        {
            "strategy": "participation_exit",
            "top_n": int(config.top_n),
            "transaction_cost_bps": float(config.transaction_cost_bps),
            "fallback_behavior": config.fallback_behavior,
            "avg_turnover": round(float(avg_turnover(weights_df)) if not weights_df.empty else 0.0, 6),
            "avg_holding_period_days": round(sum(holding_durations) / len(holding_durations), 2) if holding_durations else None,
            "cumulative_return": round(float(nav_series.iloc[-1] - 1.0), 6) if not nav_series.empty else None,
        }
    )
    if benchmark_returns is not None and not benchmark_returns.dropna().empty:
        benchmark_nav = (1.0 + benchmark_returns.fillna(0.0)).cumprod()
        summary["benchmark_cumulative_return"] = round(float(benchmark_nav.iloc[-1] - 1.0), 6)
        summary["excess_return_vs_spy"] = round(float(summary["cumulative_return"] - summary["benchmark_cumulative_return"]), 6) if summary.get("cumulative_return") is not None else None
    return {"summary": summary, "nav": nav_df, "daily": daily_df, "weights": weights_df}


def _compute_turnover(prev_weights: pd.Series, new_weights: pd.Series) -> float:
    all_names = prev_weights.index.union(new_weights.index)
    return float((new_weights.reindex(all_names, fill_value=0.0) - prev_weights.reindex(all_names, fill_value=0.0)).abs().sum())


def _update_holding_spells(
    dt: pd.Timestamp,
    prev_weights: pd.Series,
    new_weights: pd.Series,
    holdings_spells: dict[str, pd.Timestamp],
    holding_durations: list[int],
) -> None:
    prev_names = set(prev_weights.index)
    new_names = set(new_weights.index)
    for ticker in new_names - prev_names:
        holdings_spells[ticker] = dt
    for ticker in prev_names - new_names:
        start = holdings_spells.pop(ticker, None)
        if start is not None:
            holding_durations.append(int((dt - start).days))


def _close_remaining_spells(last_date: pd.Timestamp | None, holdings_spells: dict[str, pd.Timestamp], holding_durations: list[int]) -> None:
    if last_date is None:
        return
    for ticker, start in list(holdings_spells.items()):
        holding_durations.append(int((last_date - start).days))
        holdings_spells.pop(ticker, None)
