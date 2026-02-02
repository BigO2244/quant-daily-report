"""
sleeves/sleeve_2/backtest.py — Valuation (P/E vs Industry + Trend) sleeve.

Architecture
------------
This sleeve is SIGNAL-ONLY.  It produces a target_weights DataFrame
(date × ticker) across a rolling lookback window and delegates all
portfolio accounting (cash, shares, MTM, costs, turnover, equity curve)
to engine.backtest_engine.

Rolling Lookback Design
-----------------------
Sleeve 2 uses valuation data from yfinance .info snapshots (not historical),
so a true historical backtest is impossible.  Instead, it behaves like a
REAL LIVE BOOK:

  • Positions are built over a rolling lookback window (LOOKBACK_DAYS
    trading days ending at asof_effective).
  • NEW ENTRIES are only allowed on the final date (asof_effective) —
    this is the forward-only guard that respects the snapshot nature
    of valuation data.
  • EXITS may occur on ANY date in the window via hold-day and signal
    rules, so positions naturally carry forward.
  • On most runs, the sleeve will have non-zero exposure at asof because
    positions entered on a prior run's asof (= today's historical date)
    are still held.

Weekend / holiday safety: asof_effective = min(today, last_bar_in_prices),
so on weekends the sleeve runs against Friday's bar and still contributes.

Public interface
----------------
    run_backtest(period="1y", interval="1d") -> (equity_df, trades_df)
    run_backtest_with_details(period="1y", interval="1d") -> dict

equity_df has columns ["date", "equity"].
trades_df has columns produced by the backtest engine.
"""

from __future__ import annotations

import datetime as dt
import numpy as np
import pandas as pd

# ── project imports ───────────────────────────────────────────────
from core.quant_report import (
    download_prices,
    load_universe_df,
)

from sleeves.sleeve_2.signals import build_signals
from sleeves.sleeve_2.valuation import fetch_valuation_snapshot
from sleeves.sleeve_2.config import (
    TOP_LONGS,
    TOP_SHORTS,
    LONG_THRESHOLD,
    LONG_FLOOR_EXIT,
    EXIT_SIGNAL_BUFFER,
    Z_EXTREME,
    Z_EXTREME_SHORT,
    Z_ENTRY_LONG,
    PE_CHANGE_20D_MAX_LONG_ENTRY,
    Z_SHORT_EXIT_MEAN_REVERT,
    MIN_HOLD_DAYS,
    MAX_HOLD_DAYS_LONG,
    MAX_HOLD_DAYS_SHORT,
    CASH_PROXY_TICKER,
    LOOKBACK_DAYS,
)

from engine.backtest_engine import run_backtest as engine_run_backtest


# ── public entry point ────────────────────────────────────────────
def run_backtest(
    period: str = "1y",
    interval: str = "1d",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Run the Sleeve 2 rolling-lookback forward-only backtest.

    Returns
    -------
    (equity_df, trades_df)
        equity_df : DataFrame with columns ["date", "equity"]
        trades_df : DataFrame with engine trade log columns
    """
    result = _run_backtest_core(period=period, interval=interval)
    return result["equity_df"], result["trades_df"]


def run_backtest_with_details(
    period: str = "1y",
    interval: str = "1d",
) -> dict:
    """
    Run the Sleeve 2 backtest and return additional details needed for
    reporting (weights, prices, signals, asof date).
    """
    return _run_backtest_core(period=period, interval=interval)


# =====================================================================
# Core run implementation
# =====================================================================

def _run_backtest_core(
    period: str = "1y",
    interval: str = "1d",
) -> dict:
    """
    Run the Sleeve 2 rolling-lookback forward-only backtest.

    Returns dict with:
        equity_df, trades_df, weights_df, holdings_df, prices_wide,
        prices_long, signals, factor_df, target_weights, asof, calendar
    """
    # ── 1. load universe ──────────────────────────────────────────
    universe_df = load_universe_df()
    tickers = sorted(universe_df["ticker"].unique().tolist())
    if not tickers:
        print("[SLEEVE2] empty universe — returning flat equity")
        return _flat_result()

    # Ensure cash proxy is in the download list (needed for parking)
    dl_tickers = sorted(set(tickers) | {CASH_PROXY_TICKER})

    # ── 2. download prices ────────────────────────────────────────
    prices_raw = download_prices(dl_tickers, period=period, interval=interval)
    if prices_raw is None or prices_raw.empty:
        print("[SLEEVE2] no price data — returning flat equity")
        return _flat_result()

    # wide format: DatetimeIndex × ticker columns
    prices_wide = _to_wide(prices_raw, dl_tickers)
    if prices_wide.empty:
        print("[SLEEVE2] prices_wide empty after pivot — returning flat equity")
        return _flat_result()

    # ── 3. asof_effective and calendar window ─────────────────────
    today_norm = pd.Timestamp(dt.date.today())
    last_bar = prices_wide.index.max()
    asof = min(today_norm, last_bar)

    lookback_start = asof - pd.tseries.offsets.BusinessDay(LOOKBACK_DAYS)

    # Calendar = all price dates in [lookback_start, asof]
    cal_mask = (prices_wide.index >= lookback_start) & (prices_wide.index <= asof)
    calendar = prices_wide.index[cal_mask]
    if calendar.empty:
        print("[SLEEVE2] empty calendar window — returning flat equity")
        return _flat_result()

    # ── 4. valuation snapshot ─────────────────────────────────────
    val_snap = fetch_valuation_snapshot(tickers)
    if val_snap.empty:
        print("[SLEEVE2] valuation snapshot empty — returning flat equity")
        return _flat_result()

    # ── 5. build factor data & signals over entire price history ──
    #    signals.build_signals needs multi-date factor_df for the
    #    cross-sectional ranking and pe_change_20d computation.
    factor_df = _build_factor_df(prices_wide, val_snap, tickers)
    if factor_df.empty:
        print("[SLEEVE2] factor_df empty — returning flat equity")
        return _flat_result()

    signals = build_signals(factor_df)
    if signals.empty:
        print("[SLEEVE2] signals empty — returning flat equity")
        return _flat_result()

    signals["date"] = pd.to_datetime(signals["date"])

    # ── 6. run position state machine over calendar window ────────
    target_w = _run_state_machine(signals, calendar, asof, prices_wide)

    # ── 7. run canonical backtest engine ──────────────────────────
    # Subset prices to the calendar window for the engine
    prices_window = prices_wide.loc[calendar].copy()

    result = engine_run_backtest(
        target_weights=target_w,
        prices=prices_window,
        initial_equity=10_000.0,
        commission_bps=5.0,
        slippage_bps=5.0,
        rebal_rule="D",
    )

    equity_df = result["equity_curve"]
    trades_df = result["trades"]
    weights_df = result["weights"]
    holdings_df = result["holdings"]

    # ── 8. debug summary ──────────────────────────────────────────
    last_gross = target_w.iloc[-1].abs().sum()   # gross exposure (always ≥ 0)
    last_net = target_w.iloc[-1].sum()           # net exposure (signed)

    print(
        f"[SLEEVE2] today={today_norm.date()} last_bar={last_bar.date()} "
        f"asof={asof.date()} lookback_start={lookback_start.date()} "
        f"calendar_days={len(calendar)} "
        f"prices_wide=({prices_wide.shape[0]},{prices_wide.shape[1]}) "
        f"target_w=({target_w.shape[0]},{target_w.shape[1]}) "
        f"last_gross={last_gross:.6f} last_net={last_net:.6f}"
    )

    return {
        "equity_df": equity_df,
        "trades_df": trades_df,
        "weights_df": weights_df,
        "holdings_df": holdings_df,
        "prices_wide": prices_wide,
        "prices_long": prices_raw,
        "signals": signals,
        "factor_df": factor_df,
        "target_weights": target_w,
        "asof": asof,
        "calendar": calendar,
    }


# =====================================================================
# Position state machine
# =====================================================================

def _run_state_machine(
    signals: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    asof: pd.Timestamp,
    prices_wide: pd.DataFrame,
) -> pd.DataFrame:
    """
    Run a day-by-day position state machine across the calendar window.

    Rules
    -----
    • Entries ONLY on date == asof (forward-only for valuation snapshot).
    • Exits on any date (hold-day logic, signal deterioration, max hold).
    • Positions carry forward between dates.
    • Output: target_weights DataFrame (calendar dates × tickers).
    """
    all_tickers = prices_wide.columns.tolist()

    # Index signals by date for O(1) lookup
    sig_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
    for d, grp in signals.groupby("date"):
        sig_by_date[pd.Timestamp(d)] = grp

    # Position state: ticker -> {direction, hold_days}
    positions: dict[str, dict] = {}

    weight_rows: list[dict[str, float]] = []

    for d in calendar:
        day_sig = sig_by_date.get(d, pd.DataFrame())
        if not day_sig.empty:
            day_sig = day_sig.set_index("ticker")

        # -- 1) increment hold days for existing positions ----------
        for t in list(positions):
            positions[t]["hold_days"] += 1

        # -- 2) evaluate exits (any date in window) ----------------
        exits = []
        for t in list(positions):
            reason = _check_exit(t, positions[t], day_sig)
            if reason is not None:
                exits.append(t)

        for t in exits:
            del positions[t]

        # -- 3) evaluate entries ONLY on asof -----------------------
        if d == asof and not day_sig.empty:
            _try_entries(positions, day_sig)

        # -- 4) compute target weights for this date ----------------
        day_weights = _positions_to_weights(positions, all_tickers)
        weight_rows.append(day_weights)

    # Assemble target_weights DataFrame aligned to prices_wide columns
    # Single reindex — no per-column insertion.
    tw = pd.DataFrame(weight_rows, index=calendar)
    tw = tw.reindex(columns=prices_wide.columns, fill_value=0.0).copy()

    return tw


def _check_exit(
    ticker: str,
    pos: dict,
    day_sig: pd.DataFrame,
) -> str | None:
    """
    Check whether an existing position should be exited.

    Returns a reason string if yes, None if position should be kept.
    """
    direction = pos["direction"]
    hold = pos["hold_days"]

    # Honour minimum hold
    if hold < MIN_HOLD_DAYS:
        return None

    # Max hold days
    if direction == 1 and hold >= MAX_HOLD_DAYS_LONG:
        return "max_hold_long"
    if direction == -1 and hold >= MAX_HOLD_DAYS_SHORT:
        return "max_hold_short"

    # Signal-based exits (only if we have signals for this date/ticker)
    if day_sig.empty or ticker not in day_sig.index:
        return None

    row = day_sig.loc[ticker]
    z = row.get("z_pe", np.nan)
    score_long = row.get("score_long", np.nan)

    if direction == 1:  # long position
        # Exit long if score drops below floor
        if pd.notna(score_long) and score_long < LONG_FLOOR_EXIT:
            return "long_score_below_floor"
        # Exit long if valuation becomes extreme (overvalued)
        if pd.notna(z) and z >= Z_EXTREME:
            return "long_z_extreme"

    if direction == -1:  # short position
        # Exit short if z-score reverts toward mean
        if pd.notna(z) and z <= Z_SHORT_EXIT_MEAN_REVERT:
            return "short_z_mean_revert"

    return None


def _try_entries(
    positions: dict[str, dict],
    day_sig: pd.DataFrame,
) -> None:
    """
    Attempt to open new positions on the entry date (asof).

    Mutates `positions` in place.

    Long entry:  score_long >= LONG_THRESHOLD
                 AND z_pe <= Z_ENTRY_LONG (undervalued)
                 AND pe_change_20d <= PE_CHANGE_20D_MAX_LONG_ENTRY (or NaN)
    Short entry: z_pe >= Z_EXTREME_SHORT

    Respects TOP_LONGS / TOP_SHORTS slot limits (minus already-held).
    """
    already_long = {t for t, p in positions.items() if p["direction"] == 1}
    already_short = {t for t, p in positions.items() if p["direction"] == -1}

    open_long_slots = max(0, TOP_LONGS - len(already_long))
    open_short_slots = max(0, TOP_SHORTS - len(already_short))

    # ── long candidates ───────────────────────────────────────────
    if open_long_slots > 0:
        cands = day_sig[
            (day_sig["score_long"] >= LONG_THRESHOLD)
            & (day_sig["z_pe"].notna())
            & (day_sig["z_pe"] <= Z_ENTRY_LONG)
        ].copy()

        # pe_change_20d filter (allow NaN through — snapshot may lack history)
        pe_chg = cands["pe_change_20d"]
        cands = cands[pe_chg.isna() | (pe_chg <= PE_CHANGE_20D_MAX_LONG_ENTRY)]

        # Exclude tickers already in book (long or short)
        cands = cands[~cands.index.isin(positions.keys())]

        cands = cands.nlargest(open_long_slots, "score_long")
        for t in cands.index:
            positions[t] = {"direction": 1, "hold_days": 0}

    # ── short candidates ──────────────────────────────────────────
    if open_short_slots > 0:
        cands = day_sig[
            (day_sig["z_pe"].notna())
            & (day_sig["z_pe"] >= Z_EXTREME_SHORT)
        ].copy()

        cands = cands[~cands.index.isin(positions.keys())]
        cands = cands.nlargest(open_short_slots, "score_short")
        for t in cands.index:
            positions[t] = {"direction": -1, "hold_days": 0}


def _positions_to_weights(
    positions: dict[str, dict],
    all_tickers: list[str],
) -> dict[str, float]:
    """
    Convert the current position set to a weight dict.

    Equal weight per slot, longs positive, shorts negative.
    If no positions: allocate 100% to CASH_PROXY_TICKER.
    """
    n_long = sum(1 for p in positions.values() if p["direction"] == 1)
    n_short = sum(1 for p in positions.values() if p["direction"] == -1)
    n_total = n_long + n_short

    if n_total == 0:
        # park in cash proxy
        w = {t: 0.0 for t in all_tickers}
        if CASH_PROXY_TICKER in w:
            w[CASH_PROXY_TICKER] = 1.0
        return w

    # Allocate: 80% long / 20% short (if shorts exist), else 100% long
    if n_short > 0:
        long_w = 0.8 / n_long if n_long > 0 else 0.0
        short_w = -0.2 / n_short
    else:
        long_w = 1.0 / n_long if n_long > 0 else 0.0
        short_w = 0.0

    w: dict[str, float] = {}
    for t, pos in positions.items():
        if pos["direction"] == 1:
            w[t] = long_w
        else:
            w[t] = short_w

    return w


# =====================================================================
# Data preparation helpers
# =====================================================================

def _flat_result() -> dict:
    """Flat (no-trade) result — signals 'inactive' to the allocator."""
    today = pd.Timestamp(dt.date.today())
    equity_df = pd.DataFrame({"date": [today], "equity": [10_000.0]})
    return {
        "equity_df": equity_df,
        "trades_df": pd.DataFrame(),
        "weights_df": pd.DataFrame(),
        "holdings_df": pd.DataFrame(),
        "prices_wide": pd.DataFrame(),
        "prices_long": pd.DataFrame(),
        "signals": pd.DataFrame(),
        "factor_df": pd.DataFrame(),
        "target_weights": pd.DataFrame(),
        "asof": today,
        "calendar": pd.DatetimeIndex([today]),
    }


def _to_wide(prices_raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """
    Convert long-format prices (with columns date/ticker/close or similar)
    to wide format: DatetimeIndex × ticker columns.

    Also handles the case where prices_raw is already wide.
    """
    df = prices_raw.copy()

    # Already wide? (columns are tickers, index is dates)
    if set(tickers) & set(df.columns):
        df.index = pd.to_datetime(df.index)
        return df.sort_index()

    # Long format — need date + ticker + close
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    elif df.index.name == "date" or hasattr(df.index, "date"):
        df = df.reset_index()
        df.rename(columns={df.columns[0]: "date"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])

    close_col = None
    for c in ("close", "Close", "adj_close", "Adj Close"):
        if c in df.columns:
            close_col = c
            break

    ticker_col = None
    for c in ("ticker", "Ticker", "symbol", "Symbol"):
        if c in df.columns:
            ticker_col = c
            break

    if close_col is None or ticker_col is None or "date" not in df.columns:
        return pd.DataFrame()

    wide = df.pivot_table(index="date", columns=ticker_col, values=close_col)
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def _build_factor_df(
    prices_wide: pd.DataFrame,
    val_snap: pd.DataFrame,
    tickers: list[str],
) -> pd.DataFrame:
    """
    Build a multi-date factor DataFrame by cross-joining daily prices
    with the (static) valuation snapshot.

    The valuation snapshot columns (industry, forward_pe, trailing_pe)
    are stamped identically on every date — this is expected because the
    snapshot is not time-series historical.

    signals.build_signals() then computes cross-sectional z-scores,
    rankings, and pe_change_20d from this multi-date frame.
    """
    # Melt prices_wide to long format: (date, ticker, close)
    long = prices_wide.reset_index().melt(
        id_vars=prices_wide.index.name or "index",
        var_name="ticker",
        value_name="close",
    )
    long.rename(columns={long.columns[0]: "date"}, inplace=True)
    long["date"] = pd.to_datetime(long["date"])
    long = long.dropna(subset=["close"])

    # Merge valuation snapshot (static) onto every date-row
    val = val_snap.copy()
    val["ticker"] = val["ticker"].astype(str).str.upper()
    long["ticker"] = long["ticker"].astype(str).str.upper()

    merged = long.merge(val, on="ticker", how="left")

    # Only keep tickers in our universe
    ticker_set = {t.upper() for t in tickers}
    merged = merged[merged["ticker"].isin(ticker_set)].copy()

    return merged


def _empty_weights(asof: pd.Timestamp, prices_wide: pd.DataFrame) -> pd.DataFrame:
    """All-cash target weights (single row of zeros)."""
    cols = prices_wide.columns.tolist()
    tw = pd.DataFrame(0.0, index=[asof], columns=cols)
    return tw
