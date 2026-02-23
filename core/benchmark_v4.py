from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import yfinance as yf

INCEPTION_DATE = "2026-02-23"
STARTING_NAV = 10_000.0
DEFAULT_PATH = "outputs/perf/inception_nav_2026-02-23.csv"


def _max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    dd = (nav / peak) - 1.0
    return float(dd.min()) if len(dd) else 0.0


def _fetch_spy_adj_close(start_date: str, end_date: str) -> pd.Series:
    data = yf.download("SPY", start=start_date, end=end_date, auto_adjust=False, progress=False, threads=False)
    if data is None or data.empty or "Adj Close" not in data.columns:
        return pd.Series(dtype=float)
    s = data["Adj Close"].astype(float)
    s.index = pd.to_datetime(s.index)
    return s


def _effective_inception_date() -> str:
    return str(os.getenv("PAPER_INCEPTION_DATE", INCEPTION_DATE)).strip() or INCEPTION_DATE


def update_inception_nav_series(asof_date: str, model_nav: float, output_path: str | None = None) -> pd.DataFrame:
    inception_date = _effective_inception_date()
    start = pd.Timestamp(inception_date)
    asof = pd.Timestamp(asof_date)
    if asof < start:
        return pd.DataFrame()

    if output_path is None:
        output_path = f"outputs/perf/inception_nav_{start.strftime('%Y-%m-%d')}.csv"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    spy = _fetch_spy_adj_close(inception_date, (asof + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    if spy.empty or start not in spy.index:
        return pd.DataFrame()

    spy = spy.loc[(spy.index >= start) & (spy.index <= asof)].copy()
    base = float(spy.iloc[0])
    spy_nav = STARTING_NAV * (spy / base)

    if path.exists() and path.stat().st_size > 0:
        ts = pd.read_csv(path)
        ts["date"] = pd.to_datetime(ts["date"])
    else:
        ts = pd.DataFrame(columns=["date", "model_nav", "spy_nav"])

    row = pd.DataFrame([{"date": asof, "model_nav": float(model_nav), "spy_nav": float(spy_nav.iloc[-1])}])
    ts = ts[ts["date"] != asof] if not ts.empty else ts
    ts = row if ts.empty else pd.concat([ts, row], ignore_index=True)
    ts = ts.sort_values("date").reset_index(drop=True)

    ts["model_return_since_inception"] = ts["model_nav"] / STARTING_NAV - 1.0
    ts["spy_return_since_inception"] = ts["spy_nav"] / STARTING_NAV - 1.0
    ts["alpha_since_inception"] = ts["model_return_since_inception"] - ts["spy_return_since_inception"]
    ts["model_mdd_since_inception"] = _max_drawdown(ts["model_nav"])
    ts["spy_mdd_since_inception"] = _max_drawdown(ts["spy_nav"])

    out = ts.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False)
    return out
