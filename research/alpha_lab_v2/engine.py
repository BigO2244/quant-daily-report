from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from alpha_stack.research.metrics import hit_rate, summarise_performance, turnover as avg_turnover


@dataclass(frozen=True)
class StrategySpec:
    name: str
    hypothesis_id: str
    description: str
    top_n: int
    rebalance_mode: str = "daily"
    transaction_cost_bps: float = 10.0
    use_rank_decay_exit: bool = False
    exit_rank_multiple: float = 2.0


def run_backtest(signals: pd.DataFrame, spec: StrategySpec, *, start_date: str, end_date: str) -> dict:
    frame, returns_matrix, trading_dates = prepare_backtest_inputs(signals, start_date=start_date, end_date=end_date)
    return run_backtest_prepared(frame, returns_matrix, trading_dates, spec)


def build_target_snapshot(
    signals: pd.DataFrame,
    spec: StrategySpec,
    *,
    trade_date: str,
    start_date: str | None = None,
) -> dict:
    if signals.empty:
        return {
            "trade_date": trade_date,
            "effective_trade_date": None,
            "weights": pd.Series(dtype=float),
            "expected_turnover": 0.0,
            "estimated_holding_period_days": None,
            "holding_period_by_ticker": {},
            "rank_table": pd.DataFrame(),
        }

    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if start_date is not None:
        frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    frame = frame[frame["date"] <= pd.Timestamp(trade_date)].sort_values(["date", "ticker"]).reset_index(drop=True)
    if frame.empty:
        return {
            "trade_date": trade_date,
            "effective_trade_date": None,
            "weights": pd.Series(dtype=float),
            "expected_turnover": 0.0,
            "estimated_holding_period_days": None,
            "holding_period_by_ticker": {},
            "rank_table": pd.DataFrame(),
        }

    trading_dates = pd.DatetimeIndex(frame["date"].unique()).sort_values()
    effective_trade_date = trading_dates[-1]
    prev_weights = pd.Series(dtype=float)
    active_positions: dict[str, pd.Timestamp] = {}
    completed_holding_periods: list[int] = []
    expected_turnover = 0.0

    for dt in trading_dates:
        daily = frame[frame["date"] == dt].copy()
        if _should_rebalance(dt, spec.rebalance_mode):
            new_weights = _select_portfolio(daily, spec, active_positions)
        else:
            new_weights = prev_weights.copy()
        turnover_value = _turnover(prev_weights, new_weights)
        _update_holding_periods(dt, prev_weights, new_weights, active_positions, completed_holding_periods)
        if dt == effective_trade_date:
            expected_turnover = turnover_value
        prev_weights = new_weights

    daily = frame[frame["date"] == effective_trade_date].copy()
    rank_table = daily[daily["signal_ready"]].sort_values("momentum_score", ascending=False).copy()
    selected = set(prev_weights.index)
    rank_table["is_selected"] = rank_table["ticker"].astype(str).isin(selected)
    rank_table = rank_table[["ticker", "momentum_score", "momentum_rank", "is_selected"]].reset_index(drop=True)

    holding_period_by_ticker = {
        ticker: int((effective_trade_date - start).days)
        for ticker, start in active_positions.items()
    }
    estimated_holding_period = (
        round(sum(holding_period_by_ticker.values()) / len(holding_period_by_ticker), 2)
        if holding_period_by_ticker
        else None
    )
    return {
        "trade_date": trade_date,
        "effective_trade_date": str(effective_trade_date.date()),
        "weights": prev_weights.copy(),
        "expected_turnover": round(float(expected_turnover), 6),
        "estimated_holding_period_days": estimated_holding_period,
        "holding_period_by_ticker": holding_period_by_ticker,
        "rank_table": rank_table,
    }


def prepare_backtest_inputs(signals: pd.DataFrame, *, start_date: str, end_date: str) -> tuple[pd.DataFrame, pd.DataFrame, list[pd.Timestamp]]:
    if signals.empty:
        return pd.DataFrame(), pd.DataFrame(), []
    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[(frame["date"] >= pd.Timestamp(start_date)) & (frame["date"] <= pd.Timestamp(end_date))].copy()
    frame = frame.sort_values(["date", "ticker"]).reset_index(drop=True)
    returns_matrix = frame.pivot(index="date", columns="ticker", values="close").sort_index().pct_change().shift(-1)
    trading_dates = list(returns_matrix.index[:-1])
    return frame, returns_matrix, trading_dates


def run_backtest_prepared(frame: pd.DataFrame, returns_matrix: pd.DataFrame, trading_dates: list[pd.Timestamp], spec: StrategySpec) -> dict:
    if frame.empty or returns_matrix.empty or not trading_dates:
        return _empty_result(spec)

    prev_weights = pd.Series(dtype=float)
    nav = 1.0
    nav_records: list[dict] = []
    daily_records: list[dict] = []
    weight_records: list[pd.Series] = []
    active_positions: dict[str, pd.Timestamp] = {}
    holding_periods: list[int] = []

    for dt in trading_dates:
        daily = frame[frame["date"] == dt].copy()
        if _should_rebalance(dt, spec.rebalance_mode):
            new_weights = _select_portfolio(daily, spec, active_positions)
        else:
            new_weights = prev_weights.copy()

        turnover_value = _turnover(prev_weights, new_weights)
        next_returns = returns_matrix.loc[dt]
        portfolio_return = float(next_returns.reindex(new_weights.index).fillna(0.0).mul(new_weights).sum()) if not new_weights.empty else 0.0
        net_return = portfolio_return - turnover_value * (spec.transaction_cost_bps / 10000.0)
        nav *= 1.0 + net_return

        _update_holding_periods(dt, prev_weights, new_weights, active_positions, holding_periods)

        daily_records.append(
            {
                "date": dt,
                "strategy": spec.name,
                "gross_return": portfolio_return,
                "net_return": net_return,
                "turnover": turnover_value,
                "holdings_count": int(len(new_weights[new_weights > 0])),
            }
        )
        nav_records.append({"date": dt, "nav": nav})
        weight_records.append(new_weights.rename(dt))
        prev_weights = new_weights

    _close_remaining_holdings(trading_dates[-1] if trading_dates else None, active_positions, holding_periods)
    nav_df = pd.DataFrame(nav_records)
    daily_df = pd.DataFrame(daily_records)
    weights_df = pd.DataFrame(weight_records).fillna(0.0) if weight_records else pd.DataFrame()

    nav_series = pd.Series(nav_df["nav"].values, index=pd.to_datetime(nav_df["date"]), name="nav") if not nav_df.empty else pd.Series(dtype=float)
    return_series = pd.Series(daily_df["net_return"].values, index=pd.to_datetime(daily_df["date"]), name="return") if not daily_df.empty else pd.Series(dtype=float)
    benchmark_returns = None
    if "SPY" in returns_matrix.columns:
        benchmark_returns = pd.Series(returns_matrix["SPY"].values, index=returns_matrix.index, name="SPY")
        benchmark_returns = benchmark_returns.reindex(return_series.index)
        benchmark_returns = pd.to_numeric(benchmark_returns, errors="coerce")

    summary = summarise_performance(nav_series, returns=return_series, benchmark_returns=benchmark_returns, label=spec.name) if not nav_series.empty else {}
    summary.update(
        {
            "strategy": spec.name,
            "hypothesis_id": spec.hypothesis_id,
            "description": spec.description,
            "top_n": int(spec.top_n),
            "rebalance_mode": spec.rebalance_mode,
            "transaction_cost_bps": float(spec.transaction_cost_bps),
            "use_rank_decay_exit": bool(spec.use_rank_decay_exit),
            "exit_rank_multiple": float(spec.exit_rank_multiple),
            "avg_turnover": round(float(avg_turnover(weights_df)) if not weights_df.empty else 0.0, 6),
            "avg_holding_period_days": round(sum(holding_periods) / len(holding_periods), 2) if holding_periods else None,
            "cumulative_return": round(float(nav_series.iloc[-1] - 1.0), 6) if not nav_series.empty else None,
            "win_rate": round(float(hit_rate(return_series)) if not return_series.empty else 0.0, 6),
            "pct_positive_months": _positive_month_fraction(return_series),
        }
    )
    if benchmark_returns is not None and not benchmark_returns.dropna().empty:
        bench_nav = (1.0 + benchmark_returns.fillna(0.0)).cumprod()
        summary["benchmark_cumulative_return"] = round(float(bench_nav.iloc[-1] - 1.0), 6)
        summary["excess_return_vs_spy"] = round(float(summary["cumulative_return"] - summary["benchmark_cumulative_return"]), 6) if summary.get("cumulative_return") is not None else None
    return {"summary": summary, "nav": nav_df, "daily": daily_df, "weights": weights_df}


def _select_portfolio(daily: pd.DataFrame, spec: StrategySpec, active_positions: dict[str, pd.Timestamp]) -> pd.Series:
    candidates = daily[daily["signal_ready"]].copy()
    if candidates.empty:
        return pd.Series(dtype=float)
    candidates = candidates.sort_values("momentum_score", ascending=False)

    selected: list[str]
    if spec.use_rank_decay_exit:
        keep = []
        exit_cutoff = int(spec.top_n * spec.exit_rank_multiple)
        for ticker in list(active_positions):
            row = candidates[candidates["ticker"] == ticker]
            if not row.empty and float(row["momentum_rank"].iloc[0]) <= exit_cutoff:
                keep.append(ticker)
        fill = [ticker for ticker in candidates["ticker"].astype(str).tolist() if ticker not in keep]
        selected = (keep + fill)[: spec.top_n]
    else:
        selected = candidates.head(spec.top_n)["ticker"].astype(str).tolist()

    if not selected:
        return pd.Series(dtype=float)
    weight = 1.0 / len(selected)
    return pd.Series(weight, index=pd.Index(selected, dtype=str), dtype=float)


def _should_rebalance(dt: pd.Timestamp, mode: str) -> bool:
    weekday = int(dt.weekday())
    if mode == "daily":
        return True
    if mode == "weekly":
        return weekday == 0
    raise ValueError(f"Unknown rebalance_mode: {mode}")


def _turnover(prev_weights: pd.Series, new_weights: pd.Series) -> float:
    all_names = prev_weights.index.union(new_weights.index)
    return float((new_weights.reindex(all_names, fill_value=0.0) - prev_weights.reindex(all_names, fill_value=0.0)).abs().sum())


def _update_holding_periods(
    dt: pd.Timestamp,
    prev_weights: pd.Series,
    new_weights: pd.Series,
    active_positions: dict[str, pd.Timestamp],
    holding_periods: list[int],
) -> None:
    prev_names = set(prev_weights[prev_weights > 0].index)
    new_names = set(new_weights[new_weights > 0].index)
    for ticker in new_names - prev_names:
        active_positions[ticker] = dt
    for ticker in prev_names - new_names:
        start = active_positions.pop(ticker, None)
        if start is not None:
            holding_periods.append(int((dt - start).days))


def _close_remaining_holdings(last_date: pd.Timestamp | None, active_positions: dict[str, pd.Timestamp], holding_periods: list[int]) -> None:
    if last_date is None:
        return
    for ticker, start in list(active_positions.items()):
        holding_periods.append(int((last_date - start).days))
        active_positions.pop(ticker, None)


def _positive_month_fraction(return_series: pd.Series) -> float | None:
    if return_series.empty:
        return None
    monthly = (1.0 + return_series).resample("ME").prod() - 1.0
    monthly = monthly.dropna()
    if monthly.empty:
        return None
    return round(float((monthly > 0).mean()), 6)


def _empty_result(spec: StrategySpec) -> dict:
    return {
        "summary": {"strategy": spec.name, "hypothesis_id": spec.hypothesis_id, "error": "empty_signal_frame"},
        "nav": pd.DataFrame(columns=["date", "nav"]),
        "daily": pd.DataFrame(columns=["date", "net_return"]),
        "weights": pd.DataFrame(),
    }
