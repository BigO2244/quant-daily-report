"""
engine/backtest_engine.py — Canonical weights-based portfolio backtest engine.

Accepts a target_weights DataFrame (date × ticker) and a prices DataFrame,
then computes:
  - equity curve (with configurable commission + slippage costs)
  - holdings (shares per day)
  - trades log
  - realised turnover
  - summary statistics

Execution model
---------------
  close_to_next_open proxy: weights on date t are executed at close-of-day t,
  but P&L accrues from t+1 onward.  Implemented via a one-period shift on
  the returns series — weight_t * return_{t+1}.

Rebalance schedule
------------------
  If `rebal_rule` is provided (e.g. "D", "W-FRI", "ME"), the engine only
  applies new weights on rebalance dates; in between, weights drift with
  prices.  Default "D" = daily rebalance (weights re-applied every bar).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


# ── public entry point ────────────────────────────────────────────
def run_backtest(
    target_weights: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    initial_equity: float = 10_000.0,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
    rebal_rule: str = "D",
    benchmark_prices: Optional[pd.Series] = None,
) -> dict:
    """
    Run a weights-based portfolio backtest.

    Parameters
    ----------
    target_weights : DataFrame
        Index = datetime dates, columns = tickers, values = target portfolio
        weight (positive = long, negative = short).  Rows need not exist for
        every price date — missing dates inherit the previous weight.
    prices : DataFrame
        Index = datetime dates, columns = tickers, values = close prices.
    initial_equity : float
        Starting equity for the simulation.
    commission_bps : float
        Round-trip commission in basis points applied to absolute turnover
        notional each rebalance.
    slippage_bps : float
        One-way slippage in bps applied to absolute turnover notional.
    rebal_rule : str
        Pandas frequency alias for rebalance dates.  "D" = daily,
        "W-FRI" = weekly on Fridays, "ME" = month-end, etc.
    benchmark_prices : Series, optional
        If provided, computes benchmark return and alpha.

    Returns
    -------
    dict with keys:
        equity_curve : DataFrame — columns ["date", "equity"]
        holdings      : DataFrame — daily shares held (date × ticker)
        trades        : DataFrame — per-rebalance trade log
        weights       : DataFrame — realised weight matrix (date × ticker)
        stats         : dict — summary statistics
        benchmark     : Series or None — benchmark equity curve
        alpha         : Series or None — cumulative alpha vs benchmark
    """
    # ── align universes ───────────────────────────────────────────
    tickers = sorted(set(target_weights.columns) & set(prices.columns))
    if not tickers:
        return _empty_result(initial_equity)

    px = prices[tickers].sort_index().copy()
    tw = target_weights.reindex(columns=tickers, fill_value=0.0).sort_index().copy()

    # forward-fill target weights to every price date
    all_dates = px.index
    tw = tw.reindex(all_dates).ffill().fillna(0.0)

    # ── rebalance mask ────────────────────────────────────────────
    rebal_mask = _rebal_mask(all_dates, rebal_rule)

    # ── returns (shifted for execution lag) ───────────────────────
    # return on date t = close_t / close_{t-1} - 1
    asset_rets = px.pct_change().fillna(0.0)

    # ── simulate ──────────────────────────────────────────────────
    n_dates = len(all_dates)
    equity = np.empty(n_dates, dtype=np.float64)
    equity[0] = initial_equity

    # "held weights" drift between rebalances
    held_w = pd.Series(0.0, index=tickers)

    cost_rate = (commission_bps + slippage_bps) / 1e4

    trade_records: list[dict] = []
    weight_rows: list[pd.Series] = []
    turnover_daily: list[float] = []

    for i in range(n_dates):
        dt = all_dates[i]

        if i == 0:
            # first bar: set initial weights (no P&L yet)
            if rebal_mask[i]:
                held_w = tw.loc[dt].copy()
            weight_rows.append(held_w.copy())
            turnover_daily.append(0.0)
            continue

        # --- P&L from prior-day weights × today's returns -----------
        day_ret = asset_rets.loc[dt]
        port_gross_ret = (held_w * day_ret).sum()

        # --- drift weights to reflect price moves -------------------
        # w_new_i = w_old_i * (1 + r_i) / (1 + port_gross_ret)
        denom = 1.0 + port_gross_ret
        if abs(denom) > 1e-12:
            held_w = held_w * (1.0 + day_ret) / denom
        # else: leave weights as-is (extreme case guard)

        # --- rebalance? ---------------------------------------------
        turnover = 0.0
        if rebal_mask[i]:
            new_w = tw.loc[dt]
            delta = (new_w - held_w).abs().sum()
            turnover = delta
            if delta > 1e-8:
                _log_trades(trade_records, dt, held_w, new_w, equity[i - 1], tickers)
            held_w = new_w.copy()

        # --- costs --------------------------------------------------
        cost = turnover * cost_rate  # fraction of equity
        port_net_ret = port_gross_ret - cost

        equity[i] = equity[i - 1] * (1.0 + port_net_ret)
        weight_rows.append(held_w.copy())
        turnover_daily.append(turnover)

    # ── assemble outputs ──────────────────────────────────────────
    equity_curve = pd.DataFrame({"date": all_dates, "equity": equity})

    weights_df = pd.DataFrame(weight_rows, index=all_dates, columns=tickers)
    holdings_df = _weights_to_shares(weights_df, equity, px)

    trades_df = (
        pd.DataFrame(trade_records)
        if trade_records
        else pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "side",
                "weight_from",
                "weight_to",
                "notional",
                "shares_delta",
            ]
        )
    )

    stats = _compute_stats(equity, turnover_daily, all_dates)

    # ── benchmark / alpha ─────────────────────────────────────────
    bench_eq, alpha = None, None
    if benchmark_prices is not None:
        bench_ret = benchmark_prices.pct_change().reindex(all_dates).fillna(0.0)
        bench_eq = initial_equity * (1.0 + bench_ret).cumprod()
        alpha = pd.Series(equity, index=all_dates) / bench_eq - 1.0

    return {
        "equity_curve": equity_curve,
        "holdings": holdings_df,
        "trades": trades_df,
        "weights": weights_df,
        "stats": stats,
        "benchmark": bench_eq,
        "alpha": alpha,
    }


# ── helpers ───────────────────────────────────────────────────────
def _empty_result(initial_equity: float) -> dict:
    """Return a minimal valid result when there is nothing to simulate."""
    today = pd.Timestamp.now().normalize()
    return {
        "equity_curve": pd.DataFrame({"date": [today], "equity": [initial_equity]}),
        "holdings": pd.DataFrame(),
        "trades": pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "side",
                "weight_from",
                "weight_to",
                "notional",
                "shares_delta",
            ]
        ),
        "weights": pd.DataFrame(),
        "stats": {
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "avg_daily_turnover": 0.0,
            "n_rebalances": 0,
            "n_days": 0,
        },
        "benchmark": None,
        "alpha": None,
    }


def _rebal_mask(dates: pd.DatetimeIndex, rule: str) -> np.ndarray:
    """Boolean array — True on dates that are rebalance dates."""
    mask = np.zeros(len(dates), dtype=bool)
    if rule == "D":
        mask[:] = True
        return mask
    # Use pandas resampling to find period boundaries.
    # "ME" is preferred on newer pandas; older pandas may only support "M".
    rule = str(rule)
    if rule.upper() in {"M", "ME"}:
        probe = pd.Series([1], index=pd.to_datetime([dates[0]]))
        try:
            probe.resample("ME").last()
            rule = "ME"
        except Exception:
            rule = "M"
    dummy = pd.Series(1, index=dates)
    rebal_dates = set(dummy.resample(rule).last().dropna().index)
    # map to nearest available date in our index
    for rd in rebal_dates:
        idx = dates.searchsorted(rd, side="right") - 1
        if 0 <= idx < len(dates):
            mask[idx] = True
    # always rebalance on first date
    mask[0] = True
    return mask


def _log_trades(
    records: list[dict],
    dt: pd.Timestamp,
    old_w: pd.Series,
    new_w: pd.Series,
    prev_equity: float,
    tickers: list[str],
) -> None:
    for t in tickers:
        delta = new_w[t] - old_w[t]
        if abs(delta) < 1e-8:
            continue
        records.append(
            {
                "date": dt,
                "ticker": t,
                "side": "BUY" if delta > 0 else "SELL",
                "weight_from": round(old_w[t], 6),
                "weight_to": round(new_w[t], 6),
                "notional": round(abs(delta) * prev_equity, 2),
                "shares_delta": 0,  # placeholder — filled by caller if needed
            }
        )


def _weights_to_shares(
    weights: pd.DataFrame,
    equity: np.ndarray,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """Approximate share counts from weights and equity."""
    eq_col = pd.Series(equity, index=weights.index)
    notional = weights.multiply(eq_col, axis=0)
    shares = notional / prices.reindex(index=weights.index, columns=weights.columns)
    return shares.fillna(0.0).round(2)


def _compute_stats(
    equity: np.ndarray, turnover: list[float], dates: pd.DatetimeIndex
) -> dict:
    n = len(equity)
    if n < 2:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "avg_daily_turnover": 0.0,
            "n_rebalances": 0,
            "n_days": n,
        }

    total_ret = equity[-1] / equity[0] - 1.0

    # annualised
    days = (dates[-1] - dates[0]).days or 1
    years = days / 365.25
    cagr = (1.0 + total_ret) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    # daily returns
    daily_rets = np.diff(equity) / equity[:-1]
    sharpe = 0.0
    if daily_rets.std() > 1e-12:
        sharpe = (daily_rets.mean() / daily_rets.std()) * np.sqrt(252)

    # max drawdown
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min())

    avg_turnover = float(np.mean(turnover))
    n_rebal = int(np.sum(np.array(turnover) > 1e-8))

    return {
        "total_return": round(total_ret, 6),
        "cagr": round(cagr, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 6),
        "avg_daily_turnover": round(avg_turnover, 6),
        "n_rebalances": n_rebal,
        "n_days": n,
    }


def infer_latest_entries(weights: pd.DataFrame) -> pd.DataFrame:
    """
    Infer the latest entry date for each ticker based on the most recent
    non-zero weight streak.
    """
    if weights is None or weights.empty:
        return pd.DataFrame(
            columns=["ticker", "entry_date", "entry_weight", "current_weight"]
        )

    w = weights.copy()
    if not isinstance(w.index, pd.DatetimeIndex):
        w.index = pd.to_datetime(w.index)

    entries = []
    for ticker in w.columns:
        series = w[ticker].fillna(0.0)
        nonzero = series.abs() > 1e-8
        if not nonzero.any():
            continue
        last_pos = np.where(nonzero.values)[0][-1]
        entry_pos = last_pos
        while entry_pos > 0 and nonzero.iloc[entry_pos - 1]:
            entry_pos -= 1
        entries.append(
            {
                "ticker": ticker,
                "entry_date": series.index[entry_pos],
                "entry_weight": float(series.iloc[entry_pos]),
                "current_weight": float(series.iloc[last_pos]),
            }
        )

    return pd.DataFrame(entries)


def attach_entry_prices(entries: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    Attach entry prices to an entry table using the closest available close
    at or before the entry date.
    """
    if entries is None or entries.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "entry_date",
                "entry_weight",
                "current_weight",
                "entry_price",
            ]
        )

    out = entries.copy()
    if prices is None or prices.empty:
        out["entry_price"] = np.nan
        return out

    px = prices.copy()
    if not isinstance(px.index, pd.DatetimeIndex):
        px.index = pd.to_datetime(px.index)

    entry_prices = []
    for _, row in out.iterrows():
        ticker = row["ticker"]
        entry_date = pd.Timestamp(row["entry_date"])
        price = np.nan
        if ticker in px.columns:
            if entry_date in px.index:
                price = px.loc[entry_date, ticker]
            else:
                prior = px.loc[px.index <= entry_date, ticker]
                if not prior.empty:
                    price = prior.iloc[-1]
        entry_prices.append(price)

    out["entry_price"] = entry_prices
    return out
