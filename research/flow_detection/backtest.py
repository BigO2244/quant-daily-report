from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from alpha_stack.research.metrics import summarise_performance, turnover as avg_turnover


StrategyKind = Literal["baseline", "flow_filtered"]


@dataclass(frozen=True)
class FlowBacktestConfig:
    top_n: int = 10
    transaction_cost_bps: float = 10.0
    fallback_behavior: str = "baseline_fill"
    use_efficiency_filter: bool = False


def run_strategy_backtest(
    signals: pd.DataFrame,
    *,
    strategy: StrategyKind,
    config: FlowBacktestConfig,
    start_date: str | None = None,
    end_date: str | None = None,
    returns_matrix: pd.DataFrame | None = None,
) -> dict:
    if signals.empty:
        return _empty_backtest(strategy)

    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if start_date:
        frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    frame = frame.sort_values(["date", "ticker"]).reset_index(drop=True)

    local_returns = returns_matrix
    if local_returns is None:
        local_returns = frame.pivot(index="date", columns="ticker", values="close").sort_index().pct_change().shift(-1)
    if start_date:
        local_returns = local_returns[local_returns.index >= pd.Timestamp(start_date)]
    if end_date:
        local_returns = local_returns[local_returns.index <= pd.Timestamp(end_date)]
    trading_dates = list(local_returns.index[:-1])

    prev_weights = pd.Series(dtype=float)
    nav = 1.0
    nav_records: list[dict] = []
    daily_records: list[dict] = []
    weight_records: list[pd.Series] = []
    holdings_spells: dict[str, pd.Timestamp] = {}
    holding_durations: list[int] = []

    for dt in trading_dates:
        daily = frame[frame["date"] == dt]
        new_weights, selection_meta = _select_weights(daily, strategy=strategy, config=config)
        turnover_value = _compute_turnover(prev_weights, new_weights)
        next_returns = local_returns.loc[dt]
        gross_return = float(next_returns.reindex(new_weights.index).fillna(0.0).mul(new_weights).sum()) if not new_weights.empty else 0.0
        net_return = gross_return - turnover_value * (config.transaction_cost_bps / 10000.0)
        nav *= (1.0 + net_return)

        _update_holding_spells(
            dt=dt,
            prev_weights=prev_weights,
            new_weights=new_weights,
            holdings_spells=holdings_spells,
            holding_durations=holding_durations,
        )

        daily_records.append(
            {
                "date": dt,
                "strategy": strategy,
                "gross_return": gross_return,
                "net_return": net_return,
                "turnover": turnover_value,
                "holdings_count": int(len(new_weights)),
                "flow_selected_count": int(selection_meta["flow_selected_count"]),
                "selected_count_before_fallback": int(selection_meta["selected_count_before_fallback"]),
            }
        )
        nav_records.append({"date": dt, "nav": nav})
        weight_records.append(new_weights.rename(dt))
        prev_weights = new_weights

    if prev_weights is not None:
        _close_remaining_spells(trading_dates[-1] if trading_dates else None, holdings_spells, holding_durations)

    nav_df = pd.DataFrame(nav_records)
    daily_df = pd.DataFrame(daily_records)
    weights_df = pd.DataFrame(weight_records).fillna(0.0) if weight_records else pd.DataFrame()

    benchmark_returns = None
    if "SPY" in local_returns.columns:
        benchmark_returns = pd.Series(local_returns["SPY"].values, index=local_returns.index, name="SPY")
        if not daily_df.empty:
            benchmark_returns = benchmark_returns.reindex(pd.to_datetime(daily_df["date"]))
        benchmark_returns = pd.to_numeric(benchmark_returns, errors="coerce")
    nav_series = pd.Series(nav_df["nav"].values, index=pd.to_datetime(nav_df["date"]), name="nav") if not nav_df.empty else pd.Series(dtype=float)
    return_series = pd.Series(daily_df["net_return"].values, index=pd.to_datetime(daily_df["date"]), name="return") if not daily_df.empty else pd.Series(dtype=float)
    summary = summarise_performance(nav_series, returns=return_series, benchmark_returns=benchmark_returns, label=strategy) if not nav_series.empty else {}
    summary.update(
        {
            "strategy": strategy,
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

    return {
        "summary": summary,
        "nav": nav_df,
        "daily": daily_df,
        "weights": weights_df,
    }


def _select_weights(daily: pd.DataFrame, *, strategy: StrategyKind, config: FlowBacktestConfig) -> tuple[pd.Series, dict]:
    candidates = daily[daily["signal_ready"] & daily["momentum_score"].notna()].sort_values("momentum_score", ascending=False)
    if candidates.empty:
        return pd.Series(dtype=float), {"flow_selected_count": 0, "selected_count_before_fallback": 0}

    selected = candidates.head(config.top_n)
    flow_count = 0
    selected_before_fallback = len(selected)
    if strategy == "flow_filtered":
        flow_col = "flow_active_v1_1" if config.use_efficiency_filter else "flow_active"
        flow_candidates = candidates[candidates[flow_col]].head(config.top_n)
        selected = flow_candidates.copy()
        flow_count = len(selected)
        selected_before_fallback = len(selected)
        if len(selected) < config.top_n and config.fallback_behavior == "baseline_fill":
            remaining = candidates[~candidates["ticker"].isin(set(selected["ticker"]))].head(config.top_n - len(selected))
            selected = pd.concat([selected, remaining], ignore_index=True)

    if selected.empty:
        return pd.Series(dtype=float), {"flow_selected_count": flow_count, "selected_count_before_fallback": selected_before_fallback}
    weight = 1.0 / len(selected)
    weights = pd.Series(weight, index=selected["ticker"].astype(str), dtype=float)
    return weights, {
        "flow_selected_count": flow_count,
        "selected_count_before_fallback": selected_before_fallback,
    }


def _compute_turnover(prev_weights: pd.Series, new_weights: pd.Series) -> float:
    if prev_weights.empty and new_weights.empty:
        return 0.0
    all_names = prev_weights.index.union(new_weights.index)
    return float((new_weights.reindex(all_names, fill_value=0.0) - prev_weights.reindex(all_names, fill_value=0.0)).abs().sum())


def _update_holding_spells(
    *,
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


def _empty_backtest(strategy: str) -> dict:
    return {
        "summary": {"strategy": strategy, "error": "empty_signal_frame"},
        "nav": pd.DataFrame(columns=["date", "nav"]),
        "daily": pd.DataFrame(columns=["date", "net_return", "turnover"]),
        "weights": pd.DataFrame(),
    }
