from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import os
from typing import Iterable

import numpy as np
import pandas as pd

from engine.backtest_engine import run_backtest as engine_run_backtest
from engine.breaker import get_exposure_multiplier
from sleeves.sleeve_1 import backtest as sleeve1


@dataclass(frozen=True)
class Sleeve1Dataset:
    signals: pd.DataFrame
    prices_wide: pd.DataFrame
    ranking: pd.DataFrame


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def _normalize_policy(policy: str | None) -> str:
    value = str(policy or "FULL").strip().upper()
    return value if value in {"FULL", "PARTIAL", "LOCK"} else "FULL"


def _policy_breaker_state() -> dict:
    mode = str(os.getenv("BREAKER_MODE", "off")).strip().lower()
    partial = float(max(0.0, min(1.0, _env_float("BREAKER_PARTIAL_EXPOSURE", 0.5))))
    return {
        "mode": mode,
        "partial_exposure": partial,
        "exposure_multiplier": partial if mode == "partial" else (0.0 if mode == "lock" else 1.0),
    }


def _synthetic_prices(
    tickers: list[str], start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, end=end)
    rows: list[dict] = []
    for idx, ticker in enumerate(sorted(set(tickers))):
        base = 40.0 + (idx % 30) * 2.0
        phase = (idx % 13) / 13.0
        for j, dt in enumerate(dates):
            drift = 0.00020 + (idx % 7) * 0.00003
            wave = 0.0085 * np.sin((j / 21.0) + phase)
            close = base * (1 + drift) ** j * (1 + wave)
            rows.append(
                {
                    "date": dt,
                    "ticker": ticker,
                    "open": close * 0.998,
                    "high": close * 1.002,
                    "low": close * 0.996,
                    "close": close,
                    "volume": int(750_000 + (idx % 17) * 30_000 + (j % 11) * 5000),
                }
            )
    return pd.DataFrame(rows)


def load_sleeve1_dataset(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    lookback_days: int = 420,
    synthetic: bool = False,
) -> Sleeve1Dataset:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if end_ts < start_ts:
        raise ValueError("end date must be >= start date")

    download_start = start_ts - pd.Timedelta(days=lookback_days)
    original_download = sleeve1.download_prices

    def _download_prices_full_history(tickers, period="1y", interval="1d"):
        if synthetic:
            return _synthetic_prices(list(tickers), start=download_start, end=end_ts)
        px = original_download(tickers=tickers, period="max", interval=interval)
        if px is None or px.empty:
            return px
        px["date"] = pd.to_datetime(px["date"])
        return px[(px["date"] >= download_start) & (px["date"] <= end_ts)].copy()

    sleeve1.download_prices = _download_prices_full_history
    try:
        signals = sleeve1.prepare_data()
    finally:
        sleeve1.download_prices = original_download

    if signals is None or signals.empty:
        raise RuntimeError("Sleeve 1 produced no signals for the requested window.")

    signals = signals.copy()
    signals["date"] = pd.to_datetime(signals["date"])
    signals = signals[(signals["date"] >= start_ts) & (signals["date"] <= end_ts)].copy()
    if signals.empty:
        raise RuntimeError("No sleeve signals remain after start/end filtering.")

    prices_wide = (
        signals.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
        .sort_index()
        .ffill()
    )
    prices_wide = prices_wide[(prices_wide.index >= start_ts) & (prices_wide.index <= end_ts)]
    prices_wide = prices_wide.dropna(axis=1, how="all")
    if prices_wide.empty:
        raise RuntimeError("No price matrix available for the requested window.")

    ranking = (
        signals[["date", "ticker", "final_signal"]]
        .dropna(subset=["final_signal"])
        .sort_values(["date", "final_signal"], ascending=[True, False])
    )
    return Sleeve1Dataset(signals=signals, prices_wide=prices_wide, ranking=ranking)


def build_monthly_topn_target_weights(
    prices_wide: pd.DataFrame, ranking: pd.DataFrame, top_n: int = 5
) -> pd.DataFrame:
    if prices_wide.empty:
        return pd.DataFrame()

    dates = pd.DatetimeIndex(prices_wide.index).sort_values()
    tickers = list(prices_wide.columns)
    if not tickers:
        return pd.DataFrame(index=dates)

    top_n = max(1, int(top_n))
    rebalance_dates = pd.Index(
        dates.to_series().groupby(dates.to_period("M")).head(1)
    )
    rebalance_set = {pd.Timestamp(d) for d in rebalance_dates.tolist()}
    if len(dates) > 0:
        rebalance_set.add(pd.Timestamp(dates[0]))

    ranking_in_window = ranking[
        (ranking["date"] >= dates.min()) & (ranking["date"] <= dates.max())
    ].copy()
    by_date: dict[pd.Timestamp, list[str]] = (
        ranking_in_window.groupby("date")["ticker"].apply(list).to_dict()
        if not ranking_in_window.empty
        else {}
    )

    current = pd.Series(0.0, index=tickers, dtype=float)
    rows: list[pd.Series] = []
    for dt in dates:
        dt_ts = pd.Timestamp(dt)
        if dt_ts in rebalance_set:
            selected = [t for t in by_date.get(dt_ts, []) if t in tickers][:top_n]
            current = pd.Series(0.0, index=tickers, dtype=float)
            if selected:
                current.loc[selected] = 1.0 / len(selected)
        rows.append(current.copy())

    out = pd.DataFrame(rows, index=dates, columns=tickers).fillna(0.0)
    return out


def apply_policy_overlay(
    weights: pd.DataFrame, breaker_policy: str, breaker_state: dict | None = None
) -> tuple[pd.DataFrame, pd.Series]:
    if weights is None or weights.empty:
        return pd.DataFrame(), pd.Series(dtype=float)

    policy = _normalize_policy(breaker_policy)
    state = dict(_policy_breaker_state())
    if breaker_state:
        state.update(breaker_state)
    raw_override = os.getenv("BREAKER_STATE_CAN_OVERRIDE")
    if raw_override is None:
        override_allowed = False
    else:
        override_allowed = str(raw_override).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }

    multiplier = float(
        max(
            0.0,
            min(
                1.0,
                get_exposure_multiplier(
                    policy,
                    state,
                    state_can_override=override_allowed,
                ),
            ),
        )
    )
    exposure_series = pd.Series(multiplier, index=weights.index, dtype=float)
    scaled = weights.multiply(exposure_series, axis=0)
    return scaled, exposure_series


def _max_drawdown(equity: pd.Series) -> float:
    s = pd.to_numeric(equity, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return 0.0
    dd = s / s.cummax() - 1.0
    return float(dd.min())


def _sharpe_from_equity(equity: pd.Series) -> float:
    s = pd.to_numeric(equity, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) < 2:
        return 0.0
    ret = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if ret.empty:
        return 0.0
    std = float(ret.std(ddof=0))
    if std <= 0:
        return 0.0
    return float((ret.mean() / std) * np.sqrt(252.0))


def _ulcer_index(equity: pd.Series) -> float:
    s = pd.to_numeric(equity, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return 0.0
    dd_pct = (s / s.cummax() - 1.0) * 100.0
    return float(np.sqrt(np.mean(np.square(dd_pct))))


def _build_portfolio_daily(
    equity_curve: pd.DataFrame, weights: pd.DataFrame
) -> pd.DataFrame:
    eq = equity_curve.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.sort_values("date")
    eq = eq.rename(columns={"equity": "total_equity"})
    eq = eq[["date", "total_equity"]].reset_index(drop=True)
    eq_indexed = eq.set_index("date")

    w = weights.copy() if weights is not None else pd.DataFrame(index=eq_indexed.index)
    if not w.empty:
        if not isinstance(w.index, pd.DatetimeIndex):
            w.index = pd.to_datetime(w.index)
        w = w.reindex(eq_indexed.index).fillna(0.0)
    else:
        w = pd.DataFrame(index=eq_indexed.index)

    gross = w.abs().sum(axis=1) if not w.empty else pd.Series(0.0, index=eq_indexed.index)
    net = w.sum(axis=1) if not w.empty else pd.Series(0.0, index=eq_indexed.index)
    turnover = (
        w.diff().abs().sum(axis=1).fillna(0.0)
        if not w.empty
        else pd.Series(0.0, index=eq_indexed.index)
    )

    cash_weight = (1.0 - net).clip(lower=0.0)
    cash = eq_indexed["total_equity"] * cash_weight

    out = pd.DataFrame(
        {
            "date": eq_indexed.index,
            "total_equity": eq_indexed["total_equity"].astype(float),
            "cash": cash.astype(float),
            "gross_exposure": gross.astype(float),
            "net_exposure": net.astype(float),
            "turnover": turnover.astype(float),
        }
    )
    return out.reset_index(drop=True)


def _build_holdings_daily(
    holdings_matrix: pd.DataFrame,
    prices_wide: pd.DataFrame,
    weights: pd.DataFrame,
    portfolio_daily: pd.DataFrame,
) -> pd.DataFrame:
    portfolio = portfolio_daily.copy()
    portfolio["date"] = pd.to_datetime(portfolio["date"])
    pidx = pd.DatetimeIndex(portfolio["date"])
    eq_map = portfolio.set_index("date")["total_equity"]
    cash_map = portfolio.set_index("date")["cash"]

    if holdings_matrix is None or holdings_matrix.empty:
        non_cash = pd.DataFrame(columns=["date", "ticker", "shares", "price", "market_value", "weight"])
    else:
        h = holdings_matrix.copy()
        if not isinstance(h.index, pd.DatetimeIndex):
            h.index = pd.to_datetime(h.index)
        h = h.reindex(pidx).fillna(0.0)

        px = prices_wide.copy()
        if not isinstance(px.index, pd.DatetimeIndex):
            px.index = pd.to_datetime(px.index)
        px = px.reindex(index=pidx, columns=h.columns).ffill().fillna(0.0)

        shares_long = (
            h.stack(dropna=False).rename("shares").reset_index().rename(columns={"level_0": "date", "level_1": "ticker"})
        )
        prices_long = (
            px.stack(dropna=False).rename("price").reset_index().rename(columns={"level_0": "date", "level_1": "ticker"})
        )
        non_cash = shares_long.merge(prices_long, on=["date", "ticker"], how="left")
        non_cash["shares"] = pd.to_numeric(non_cash["shares"], errors="coerce").fillna(0.0)
        non_cash["price"] = pd.to_numeric(non_cash["price"], errors="coerce").fillna(0.0)
        non_cash["market_value"] = non_cash["shares"] * non_cash["price"]
        non_cash = non_cash[non_cash["market_value"].abs() > 1e-8].copy()

        if weights is not None and not weights.empty:
            w = weights.copy()
            if not isinstance(w.index, pd.DatetimeIndex):
                w.index = pd.to_datetime(w.index)
            w = w.reindex(index=pidx, columns=h.columns).fillna(0.0)
            w_long = (
                w.stack(dropna=False)
                .rename("weight")
                .reset_index()
                .rename(columns={"level_0": "date", "level_1": "ticker"})
            )
            non_cash = non_cash.merge(w_long, on=["date", "ticker"], how="left")

        if "weight" not in non_cash.columns:
            non_cash["weight"] = np.nan
        missing_weight = non_cash["weight"].isna()
        if missing_weight.any():
            non_cash.loc[missing_weight, "weight"] = (
                non_cash.loc[missing_weight, "market_value"]
                / non_cash.loc[missing_weight, "date"].map(eq_map).replace(0, np.nan)
            ).fillna(0.0)

    non_cash["sleeve"] = "sleeve_1"
    cash_rows = pd.DataFrame(
        {
            "date": pidx,
            "ticker": "CASH",
            "sleeve": "CASH",
            "shares": cash_map.values,
            "price": 1.0,
            "market_value": cash_map.values,
            "weight": np.where(eq_map.values > 0, cash_map.values / eq_map.values, 1.0),
        }
    )

    out = pd.concat([non_cash, cash_rows], ignore_index=True, sort=False)
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values(["date", "ticker"]).reset_index(drop=True)
    return out[
        ["date", "ticker", "sleeve", "shares", "price", "market_value", "weight"]
    ]


def _build_trades(trades_df: pd.DataFrame, prices_wide: pd.DataFrame) -> pd.DataFrame:
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(
            columns=["date", "ticker", "sleeve", "side", "shares", "price", "notional", "reason"]
        )

    t = trades_df.copy()
    t["date"] = pd.to_datetime(t["date"])
    if "side" not in t.columns:
        t["side"] = np.where(
            pd.to_numeric(t.get("weight_to", 0.0), errors="coerce").fillna(0.0)
            - pd.to_numeric(t.get("weight_from", 0.0), errors="coerce").fillna(0.0)
            >= 0.0,
            "BUY",
            "SELL",
        )
    t["side"] = t["side"].astype(str).str.upper()
    t["notional"] = pd.to_numeric(t.get("notional"), errors="coerce").fillna(0.0)

    px = prices_wide.copy()
    if not isinstance(px.index, pd.DatetimeIndex):
        px.index = pd.to_datetime(px.index)
    px_long = (
        px.stack(dropna=False).rename("price").reset_index().rename(columns={"level_0": "date", "level_1": "ticker"})
    )
    t = t.merge(px_long, on=["date", "ticker"], how="left")
    t["price"] = pd.to_numeric(t["price"], errors="coerce").fillna(0.0)
    abs_shares = np.where(t["price"] > 0.0, t["notional"].abs() / t["price"], 0.0)
    t["shares"] = np.where(t["side"] == "SELL", -abs_shares, abs_shares)
    t["notional"] = np.where(t["side"] == "SELL", -t["notional"].abs(), t["notional"].abs())
    t["sleeve"] = "sleeve_1"
    t["reason"] = "rebalance"
    t = t.sort_values(["date", "ticker"]).reset_index(drop=True)
    return t[["date", "ticker", "sleeve", "side", "shares", "price", "notional", "reason"]]


def run_window_backtest(
    dataset: Sleeve1Dataset,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    breaker_policy: str = "FULL",
    top_n: int = 5,
    initial_equity: float = 10_000.0,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
    breaker_state: dict | None = None,
    allow_empty_sleeves: bool = False,
) -> dict:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    policy = _normalize_policy(breaker_policy)

    prices_window = dataset.prices_wide[
        (dataset.prices_wide.index >= start_ts) & (dataset.prices_wide.index <= end_ts)
    ].copy()
    if prices_window.empty:
        raise RuntimeError(f"No price data for window {start_ts.date()} to {end_ts.date()}.")

    ranking_window = dataset.ranking[
        (dataset.ranking["date"] >= prices_window.index.min())
        & (dataset.ranking["date"] <= prices_window.index.max())
    ].copy()

    target_weights_pre = build_monthly_topn_target_weights(
        prices_wide=prices_window, ranking=ranking_window, top_n=top_n
    )
    if target_weights_pre.empty and not allow_empty_sleeves:
        raise RuntimeError(
            "Backtest mode failed: sleeve_1 empty target_weights (missing data). Set ALLOW_EMPTY_SLEEVES=1 to continue."
        )
    target_weights_post, exposure_series = apply_policy_overlay(
        target_weights_pre, policy, breaker_state=breaker_state
    )

    bt = engine_run_backtest(
        target_weights=target_weights_post,
        prices=prices_window,
        initial_equity=float(initial_equity),
        commission_bps=float(commission_bps),
        slippage_bps=float(slippage_bps),
        rebal_rule="D",
    )

    equity_curve = bt.get("equity_curve", pd.DataFrame(columns=["date", "equity"])).copy()
    holdings_matrix = bt.get("holdings", pd.DataFrame())
    realized_weights = bt.get("weights", pd.DataFrame())
    trades_exec = _build_trades(bt.get("trades", pd.DataFrame()), prices_wide=prices_window)
    portfolio_daily = _build_portfolio_daily(equity_curve=equity_curve, weights=realized_weights)
    holdings_daily = _build_holdings_daily(
        holdings_matrix=holdings_matrix,
        prices_wide=prices_window,
        weights=realized_weights,
        portfolio_daily=portfolio_daily,
    )
    if portfolio_daily.empty and not allow_empty_sleeves:
        raise RuntimeError(
            "Backtest mode failed: portfolio_daily is empty. Set ALLOW_EMPTY_SLEEVES=1 to continue."
        )
    if holdings_daily.empty and not allow_empty_sleeves:
        raise RuntimeError(
            "Backtest mode failed: holdings_daily is empty. Set ALLOW_EMPTY_SLEEVES=1 to continue."
        )

    total_equity = pd.to_numeric(portfolio_daily["total_equity"], errors="coerce").fillna(0.0)
    total_return = (
        float(total_equity.iloc[-1] / total_equity.iloc[0] - 1.0)
        if len(total_equity) > 1 and total_equity.iloc[0] > 0
        else 0.0
    )
    n_days = int(len(total_equity))
    years = max(n_days / 252.0, 1e-9)
    cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0) if n_days > 1 else 0.0

    invested_before = (
        float(target_weights_pre.sum(axis=1).mean()) if not target_weights_pre.empty else 0.0
    )
    invested_after = (
        float(target_weights_post.sum(axis=1).mean()) if not target_weights_post.empty else 0.0
    )
    cash_target = (
        float(1.0 - target_weights_post.iloc[-1].sum())
        if not target_weights_post.empty
        else 1.0
    )

    summary = {
        "start_date": start_ts.date().isoformat(),
        "end_date": end_ts.date().isoformat(),
        "policy": policy,
        "exposure_multiplier": float(exposure_series.iloc[-1]) if not exposure_series.empty else 1.0,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": _sharpe_from_equity(total_equity),
        "max_drawdown": _max_drawdown(total_equity),
        "ulcer_index": _ulcer_index(total_equity),
        "avg_turnover": float(portfolio_daily["turnover"].mean()) if not portfolio_daily.empty else 0.0,
        "n_days": n_days,
        "trade_count": int(len(trades_exec)),
        "invested_before": invested_before,
        "invested_after": invested_after,
        "cash_target": cash_target,
        "ending_equity": float(total_equity.iloc[-1]) if len(total_equity) else float(initial_equity),
        "allow_empty_sleeves": bool(allow_empty_sleeves),
    }

    return {
        "summary": summary,
        "equity_curve": equity_curve,
        "trades": trades_exec,
        "holdings_daily": holdings_daily,
        "portfolio_daily": portfolio_daily,
        "target_weights_pre": target_weights_pre,
        "target_weights_post": target_weights_post,
    }


def sample_random_windows(
    *,
    trading_dates: pd.DatetimeIndex,
    n_windows: int,
    years: int,
    seed: int,
    sample_start_min: str | pd.Timestamp = "2008-01-01",
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if n_windows <= 0:
        return []
    if years <= 0:
        raise ValueError("years must be > 0")

    dates = pd.DatetimeIndex(pd.to_datetime(trading_dates)).sort_values().unique()
    if dates.empty:
        raise RuntimeError("trading_dates is empty")

    rng = np.random.default_rng(int(seed))
    start_min = pd.Timestamp(sample_start_min).normalize()
    latest = pd.Timestamp(dates.max()).normalize()
    start_max = latest - pd.DateOffset(years=years)
    eligible = dates[(dates >= start_min) & (dates <= start_max)]
    if eligible.empty:
        raise RuntimeError("No eligible start dates for requested random windows.")

    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    seen: set[tuple[str, str]] = set()
    attempts = 0
    max_attempts = max(10_000, n_windows * 250)

    while len(windows) < n_windows and attempts < max_attempts:
        attempts += 1
        start_date = pd.Timestamp(eligible[int(rng.integers(0, len(eligible)))])
        target_end = start_date + pd.DateOffset(years=years) - pd.Timedelta(days=1)
        end_idx = dates.searchsorted(target_end, side="right") - 1
        if end_idx < 0:
            continue
        end_date = pd.Timestamp(dates[end_idx])
        if end_date <= start_date:
            continue
        key = (start_date.date().isoformat(), end_date.date().isoformat())
        if key in seen:
            continue
        seen.add(key)
        windows.append((start_date, end_date))

    if len(windows) < n_windows:
        raise RuntimeError(
            f"Unable to sample {n_windows} windows of {years}y after {attempts} attempts."
        )
    return windows


def evaluate_windows(
    dataset: Sleeve1Dataset,
    *,
    windows: Iterable[tuple[pd.Timestamp, pd.Timestamp]],
    breaker_policy: str,
    top_n: int = 5,
    initial_equity: float = 10_000.0,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> pd.DataFrame:
    rows: list[dict] = []
    for window_id, (start_date, end_date) in enumerate(windows, start=1):
        result = run_window_backtest(
            dataset,
            start=start_date,
            end=end_date,
            breaker_policy=breaker_policy,
            top_n=top_n,
            initial_equity=initial_equity,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
        )
        row = dict(result["summary"])
        row["window_id"] = window_id
        rows.append(row)
    return pd.DataFrame(rows)


def select_worst_window(metrics_df: pd.DataFrame, metric: str = "MAX_DD") -> dict:
    if metrics_df is None or metrics_df.empty:
        raise RuntimeError("metrics_df is empty")

    metric_key = str(metric or "MAX_DD").strip().upper()
    if metric_key == "MAX_DD":
        idx = metrics_df["max_drawdown"].astype(float).idxmin()
        selection_metric = "max_drawdown"
    elif metric_key == "CAGR":
        idx = metrics_df["cagr"].astype(float).idxmin()
        selection_metric = "cagr"
    elif metric_key == "ULCER":
        idx = metrics_df["ulcer_index"].astype(float).idxmax()
        selection_metric = "ulcer_index"
    else:
        raise ValueError(f"Unsupported MC metric: {metric}")

    row = metrics_df.loc[idx].to_dict()
    row["selection_metric"] = selection_metric
    row["selection_mode"] = metric_key
    return row


def default_run_id(
    *,
    start: str | date | datetime | pd.Timestamp,
    end: str | date | datetime | pd.Timestamp,
    policy: str,
) -> str:
    s = pd.Timestamp(start).date().isoformat().replace("-", "")
    e = pd.Timestamp(end).date().isoformat().replace("-", "")
    p = _normalize_policy(policy).lower()
    return f"{s}_{e}_{p}"
