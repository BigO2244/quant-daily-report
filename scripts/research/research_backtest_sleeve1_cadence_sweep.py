#!/usr/bin/env python3
"""Sleeve 1 alpha-variant cadence sweep (monthly vs weekly vs daily).

This script reuses the sleeve 1 alpha-variant research backtest machinery
but runs the same universe / scoring / cost model at three rebalance
cadences so we can isolate how much of the gross->net degradation is
pure cadence/turnover drag.

Outputs:
    outputs/research/sleeve1_cadence_sweep/summary.csv
    outputs/research/sleeve1_cadence_sweep/<cadence>_timeseries.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Also add the scripts dir so we can import the sibling alpha variant module
SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from sleeves.sleeve_1 import backtest as sleeve1  # noqa: E402
from scripts.research.research_backtest_sleeve1_alpha_variant import (  # noqa: E402
    _annualized_stats,
    _beta_vs_spy,
    _load_spy,
    _max_drawdown,
    _synthetic_prices,
)


# ----------------------------------------------------------------------- #
# Offline price loader (bypasses yfinance)                                 #
# ----------------------------------------------------------------------- #

CACHED_PRICES_PARQUET = (
    ROOT / "alpha_stack_cache" / "prices" / "_matrix_prices_2007_2026.parquet"
)


def _load_cached_prices_long(
    start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """Load cached wide close-price matrix and expand to long OHLCV form.

    High/Low/Open are synthesized from close with a small constant intraday
    range (sufficient for ATR computation — the cadence comparison is not
    sensitive to absolute ATR values, only to the relative shape of the
    gross->net degradation across rebalance frequencies).
    """
    wide = pd.read_parquet(CACHED_PRICES_PARQUET)
    wide = wide[(wide.index >= start) & (wide.index <= end)]
    wide = wide.dropna(how="all", axis=1)
    long = wide.stack(dropna=True).rename("close").reset_index()
    long.columns = ["date", "ticker", "close"]
    long["open"] = long["close"].astype(float)
    long["high"] = long["close"].astype(float) * 1.005
    long["low"] = long["close"].astype(float) * 0.995
    long["volume"] = 1_000_000
    return long[["date", "ticker", "open", "high", "low", "close", "volume"]]


def _build_signals_from_cached(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Reproduce sleeve_1.prepare_data fallback logic using cached prices."""
    prices = _load_cached_prices_long(start=start, end=end)
    s = prices.copy().sort_values(["ticker", "date"])
    s["ret20"] = s.groupby("ticker")["close"].pct_change(20)
    s["ret60"] = s.groupby("ticker")["close"].pct_change(60)
    s["vol20"] = (
        s.groupby("ticker")["volume"]
        .rolling(20)
        .mean()
        .reset_index(level=0, drop=True)
    )

    def rank(x):
        return x.rank(pct=True) * 100

    s["momentum_score_v2"] = s.groupby("date")["ret20"].transform(rank).fillna(50.0)
    s["factor_score"] = s.groupby("date")["ret60"].transform(rank).fillna(50.0)
    s["volume_score"] = s.groupby("date")["vol20"].transform(rank).fillna(50.0)
    s["final_signal"] = (
        0.50 * s["momentum_score_v2"]
        + 0.35 * s["factor_score"]
        + 0.15 * s["volume_score"]
    )
    s["date"] = pd.to_datetime(s["date"])
    return s[
        [
            "date",
            "ticker",
            "close",
            "volume",
            "momentum_score_v2",
            "factor_score",
            "volume_score",
            "final_signal",
        ]
    ].sort_values(["date", "ticker"]).reset_index(drop=True)


def _load_spy_cached(
    start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """SPY series from the cached matrix (falls back to synthetic if missing)."""
    wide = pd.read_parquet(CACHED_PRICES_PARQUET)
    if "SPY" in wide.columns:
        spy = wide[["SPY"]].dropna()
        spy = spy[(spy.index >= start) & (spy.index <= end)].copy()
        spy = spy.reset_index().rename(columns={"Date": "date", "SPY": "close"})
        spy["spy_ret"] = spy["close"].pct_change().fillna(0.0)
        spy["spy_nav"] = (1.0 + spy["spy_ret"]).cumprod()
        return spy[["date", "spy_ret", "spy_nav"]]
    # Fall back: build a flat synthetic benchmark so we can still complete
    dates = pd.bdate_range(start=start, end=end)
    return pd.DataFrame({"date": dates, "spy_ret": 0.0, "spy_nav": 1.0})


# ----------------------------------------------------------------------- #
# Cadence helpers                                                          #
# ----------------------------------------------------------------------- #

def _cadence_rebalance_dates(dates: pd.DatetimeIndex, cadence: str) -> set:
    """Return set of rebalance dates for the given cadence.

    cadence: one of {"M", "W", "D"}.
      - M: first trading day of each calendar month
      - W: first trading day of each ISO week (Mon-anchored)
      - D: every trading day
    """
    ser = dates.to_series()
    if cadence == "M":
        return set(ser.groupby(dates.to_period("M")).head(1).tolist())
    if cadence == "W":
        return set(ser.groupby(dates.to_period("W")).head(1).tolist())
    if cadence == "D":
        return set(ser.tolist())
    raise ValueError(f"Unknown cadence: {cadence}")


# ----------------------------------------------------------------------- #
# Core backtest loop (parameterized by cadence)                            #
# ----------------------------------------------------------------------- #

def _run_backtest_at_cadence(
    signals: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    synthetic: bool,
    apply_costs: bool,
    cost_bps: float,
    cadence: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = signals[(signals["date"] >= start) & (signals["date"] <= end)].copy()
    if signals.empty:
        raise RuntimeError("No Sleeve 1 signals in requested window.")

    signals["date"] = pd.to_datetime(signals["date"])
    prices = (
        signals.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
        .sort_index()
        .ffill()
    )
    rets = prices.pct_change().fillna(0.0)

    ranking = signals[["date", "ticker", "final_signal"]].dropna(subset=["final_signal"])
    ranking = ranking.sort_values(["date", "final_signal"], ascending=[True, False])

    dates = prices.index
    rebalance_dates = _cadence_rebalance_dates(dates, cadence)

    weights = pd.Series(0.0, index=prices.columns)
    pending_weights: pd.Series | None = None
    ts_rows: list[dict] = []
    gross_nav = 1.0
    net_nav = 1.0
    turnover_events: list[float] = []
    total_cost_drag = 0.0
    cost_rate = cost_bps / 10000.0

    for i, d in enumerate(dates):
        turnover = 0.0
        cost_t = 0.0
        if pending_weights is not None:
            turnover = float((pending_weights - weights).abs().sum() / 2.0)
            turnover_events.append(turnover)
            cost_t = turnover * cost_rate if apply_costs else 0.0
            total_cost_drag += cost_t
            weights = pending_weights
            pending_weights = None
        if i == 0 and weights.sum() == 0.0:
            rebalance_dates.add(d)

        gross_ret = float((weights * rets.loc[d].fillna(0.0)).sum()) if weights.sum() > 0 else 0.0
        net_ret = gross_ret - cost_t
        gross_nav *= (1.0 + gross_ret)
        net_nav *= (1.0 + net_ret)
        ts_rows.append(
            {
                "date": d,
                "gross_portfolio_ret": gross_ret,
                "net_portfolio_ret": net_ret,
                "gross_nav": gross_nav,
                "net_nav": net_nav,
                "turnover": turnover,
                "cost": cost_t,
                "n_holdings": int((weights > 0).sum()),
            }
        )

        if d in rebalance_dates:
            day_rank = ranking[ranking["date"] == d].head(5)
            selected = [t for t in day_rank["ticker"].tolist() if t in prices.columns]
            if len(selected) < 5:
                extra = ranking[ranking["date"] == d]["ticker"].tolist()
                for t in extra:
                    if t in prices.columns and t not in selected:
                        selected.append(t)
                    if len(selected) == 5:
                        break
            if len(selected) < 5:
                # Not enough names — skip rebalance this day
                continue
            pending_weights = pd.Series(0.0, index=prices.columns)
            pending_weights.loc[selected[:5]] = 0.20

    ts_df = pd.DataFrame(ts_rows)
    if synthetic:
        spy = _load_spy(start=start, end=end, synthetic=True)
    else:
        spy = _load_spy_cached(start=start, end=end)
    ts_df = ts_df.merge(spy[["date", "spy_ret", "spy_nav"]], on="date", how="left")
    ts_df["spy_ret"] = ts_df["spy_ret"].fillna(0.0)
    ts_df["spy_nav"] = ts_df["spy_nav"].ffill().bfill()

    gross_total, gross_cagr, gross_vol, gross_sharpe = _annualized_stats(ts_df["gross_portfolio_ret"])
    net_total, net_cagr, net_vol, net_sharpe = _annualized_stats(ts_df["net_portfolio_ret"])
    s_total, s_cagr, _, s_sharpe = _annualized_stats(ts_df["spy_ret"])
    avg_turnover = float(np.mean(turnover_events)) if turnover_events else 0.0
    n_rebals = len(turnover_events)
    # Annualized turnover: average per-rebalance * rebalances/year
    years = max(len(ts_df) / 252.0, 1e-9)
    rebals_per_year = n_rebals / years if years > 0 else 0.0
    ann_turnover_one_way = avg_turnover * rebals_per_year
    # Annualized cost drag
    ann_cost_drag = total_cost_drag / years if years > 0 else 0.0

    summary_row = {
        "cadence": cadence,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "n_days": int(len(ts_df)),
        "n_rebalances": int(n_rebals),
        "rebals_per_year": float(round(rebals_per_year, 2)),
        "avg_turnover_per_rebal": float(round(avg_turnover, 4)),
        "annualized_turnover_one_way": float(round(ann_turnover_one_way, 3)),
        "gross_total_return": float(round(gross_total, 4)),
        "gross_cagr": float(round(gross_cagr, 4)),
        "gross_vol": float(round(gross_vol, 4)),
        "gross_sharpe": float(round(gross_sharpe, 3)),
        "gross_max_drawdown": float(round(_max_drawdown(ts_df["gross_nav"]), 4)),
        "gross_beta_vs_spy": float(round(_beta_vs_spy(ts_df["gross_portfolio_ret"], ts_df["spy_ret"]), 3)),
        "net_total_return": float(round(net_total, 4)),
        "net_cagr": float(round(net_cagr, 4)),
        "net_vol": float(round(net_vol, 4)),
        "net_sharpe": float(round(net_sharpe, 3)),
        "net_max_drawdown": float(round(_max_drawdown(ts_df["net_nav"]), 4)),
        "net_beta_vs_spy": float(round(_beta_vs_spy(ts_df["net_portfolio_ret"], ts_df["spy_ret"]), 3)),
        "cost_bps_per_side": float(cost_bps),
        "total_cost_drag": float(round(total_cost_drag, 4)),
        "annualized_cost_drag": float(round(ann_cost_drag, 4)),
        "cagr_degradation_gross_to_net": float(round(gross_cagr - net_cagr, 4)),
        "spy_cagr": float(round(s_cagr, 4)),
    }
    return pd.DataFrame([summary_row]), ts_df


# ----------------------------------------------------------------------- #
# Main                                                                     #
# ----------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2009-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--output-dir", default="outputs/research/sleeve1_cadence_sweep")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--cost-bps", type=float, default=25.0, help="Cost per side in bps")
    p.add_argument("--apply-costs", action="store_true", default=True)
    p.add_argument(
        "--cadences",
        default="M,W,D",
        help="Comma-separated cadences to run (M=monthly, W=weekly, D=daily)",
    )
    return p.parse_args()


def _prepare_signals_once(
    start: pd.Timestamp, end: pd.Timestamp, synthetic: bool
) -> pd.DataFrame:
    """Prepare sleeve 1 signals once so all cadences share identical data.

    In synthetic mode, uses the alpha-variant synthetic generator via the
    sleeve1 pipeline (monkey-patched download_prices). In real-data mode,
    loads from the on-disk cached parquet matrix (no network), since
    yfinance is unavailable in this sandbox.
    """
    if synthetic:
        original_download = sleeve1.download_prices

        def _download_prices_full_history(tickers, period="1y", interval="1d"):
            return _synthetic_prices(list(tickers), start=start, end=end)

        sleeve1.download_prices = _download_prices_full_history
        try:
            signals = sleeve1.prepare_data()
        finally:
            sleeve1.download_prices = original_download
        return signals

    return _build_signals_from_cached(start=start, end=end)


def main() -> None:
    args = parse_args()
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    cadences = [c.strip() for c in args.cadences.split(",") if c.strip()]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[sweep] Preparing sleeve 1 signals once for {start.date()} -> {end.date()}")
    signals = _prepare_signals_once(start=start, end=end, synthetic=args.synthetic)
    print(f"[sweep] signals rows={len(signals):,} tickers={signals['ticker'].nunique()}")

    all_summaries = []
    for cadence in cadences:
        print(f"[sweep] running cadence={cadence} cost_bps={args.cost_bps} apply_costs={args.apply_costs}")
        summary, ts = _run_backtest_at_cadence(
            signals=signals,
            start=start,
            end=end,
            synthetic=args.synthetic,
            apply_costs=args.apply_costs,
            cost_bps=args.cost_bps,
            cadence=cadence,
        )
        all_summaries.append(summary)
        ts[["date", "gross_nav", "net_nav", "spy_nav", "turnover", "cost"]].to_csv(
            out_dir / f"{cadence}_timeseries.csv", index=False
        )

    combined = pd.concat(all_summaries, ignore_index=True)
    combined.to_csv(out_dir / "summary.csv", index=False)
    print("\n================= CADENCE SWEEP RESULTS =================")
    cols = [
        "cadence",
        "rebals_per_year",
        "avg_turnover_per_rebal",
        "annualized_turnover_one_way",
        "gross_cagr",
        "net_cagr",
        "annualized_cost_drag",
        "net_sharpe",
        "net_beta_vs_spy",
    ]
    print(combined[cols].to_string(index=False))
    print("==========================================================")


if __name__ == "__main__":
    main()
