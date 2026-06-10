"""FR-068 Phase 3 (priced) — Legacy vs PIT Polaris rebaseline (RESEARCH_ONLY).

Runs the COMMITTED Polaris momentum baseline (research.alpha_lab_v1.signals
build_alpha_lab_signal_frame + research.alpha_lab_v2.engine run_backtest,
baseline_top10_daily) on two universes, changing ONLY the universe source:

  Legacy = data/universe.csv (static 200 current names)
  PIT    = caerus_large_cap membership (PIT, includes delisted), per as-of date

Both legs are priced from the SAME local Sharadar SEP adjusted-close cache (SPY
from the local matrix for the regime input), so the only difference is the
universe. Ranking / sizing / costs / rebalance are the frozen committed logic.
The 2025+ holdout is excluded by slicing all prices to <= end_date.

No production Polaris/execution/model/cron/registry changes; no tuning.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pandas as pd

from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import StrategySpec, prepare_backtest_inputs, run_backtest

REPO_ROOT = Path(__file__).resolve().parents[1]
SEP_CACHE = REPO_ROOT / "data" / "research_cache" / "sharadar_sep"
PRICE_MATRIX = REPO_ROOT / "alpha_stack_cache" / "prices" / "_matrix_prices_2007_2026.parquet"
LARGE_CAP_FAMILY = REPO_ROOT / "data" / "pit_universe" / "membership_universe_large_cap.csv"
LEGACY_UNIVERSE = REPO_ROOT / "data" / "universe.csv"

WARMUP_START = "2012-06-01"   # >252 trading days before 2014-01-02 for r12_1
VALIDATION_START = "2014-01-02"
VALIDATION_END = "2024-12-31"  # holdout (2025+) excluded
BASELINE_SPEC = StrategySpec(name="baseline_top10_daily", hypothesis_id="CONTROL",
                             description="Momentum baseline: daily, equal-weight top 10.",
                             top_n=10, rebalance_mode="daily", transaction_cost_bps=10.0)


def _norm_ticker(t: str) -> str:
    t = str(t or "").strip().upper()
    if "-" in t:
        head, _, tail = t.rpartition("-")
        if head and len(tail) <= 2 and tail.isalpha():
            return f"{head}.{tail}"
    return t


def load_legacy_tickers() -> list[str]:
    raw = "\n".join(l for l in LEGACY_UNIVERSE.read_text().splitlines() if l.strip())
    return sorted({_norm_ticker(r["ticker"]) for r in csv.DictReader(io.StringIO(raw)) if r.get("ticker", "").strip()})


def load_large_cap_tickers() -> list[str]:
    with LARGE_CAP_FAMILY.open() as fh:
        return sorted({_norm_ticker(r["ticker"]) for r in csv.DictReader(fh) if r.get("ticker", "").strip()})


def _sep_close(ticker: str, end_date: str) -> pd.DataFrame:
    path = SEP_CACHE / f"{ticker}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "closeadj" not in df.columns or "date" not in df.columns:
        return pd.DataFrame()
    df = df[["date", "closeadj"]].rename(columns={"closeadj": "close"})
    df["ticker"] = ticker
    df = df[df["date"] <= end_date]
    return df[["date", "ticker", "close"]]


def _spy_close(end_date: str) -> pd.DataFrame:
    m = pd.read_parquet(PRICE_MATRIX)
    if "SPY" not in m.columns:
        return pd.DataFrame()
    s = m[["SPY"]].rename(columns={"SPY": "close"}).reset_index()
    s.columns = ["date", "close"]
    s["date"] = pd.to_datetime(s["date"]).dt.strftime("%Y-%m-%d")
    s["ticker"] = "SPY"
    return s[s["date"] <= end_date][["date", "ticker", "close"]]


def build_panel(tickers: list[str], end_date: str) -> tuple[pd.DataFrame, list[str]]:
    frames, missing = [], []
    for t in tickers:
        f = _sep_close(t, end_date)
        (frames.append(f) if not f.empty else missing.append(t))
    frames.append(_spy_close(end_date))
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"]).dt.strftime("%Y-%m-%d")
    return panel, missing


def run_leg(tickers: list[str]) -> dict[str, Any]:
    panel, missing = build_panel(tickers, VALIDATION_END)
    signals = build_alpha_lab_signal_frame(panel)
    result = run_backtest(signals, BASELINE_SPEC, start_date=VALIDATION_START, end_date=VALIDATION_END)
    return {"result": result, "signals": signals, "priced_tickers": len(set(tickers) - set(missing)),
            "missing_from_sep": missing}


def attribution(signals: pd.DataFrame, top_n: int = 10) -> dict[str, float]:
    """Per-ticker cumulative contribution of the committed baseline (daily EW
    top-N by momentum_score). Reconstructed from the same signals+forward returns
    the engine uses; SPY excluded from the attribution display."""
    frame, returns_matrix, trading_dates = prepare_backtest_inputs(
        signals, start_date=VALIDATION_START, end_date=VALIDATION_END)
    if frame.empty:
        return {}
    contrib: dict[str, float] = {}
    w = 1.0 / top_n
    by_date = {d: g for d, g in frame[frame["signal_ready"]].groupby("date")}
    for dt in trading_dates:
        g = by_date.get(dt)
        if g is None:
            continue
        top = g.sort_values("momentum_score", ascending=False).head(top_n)["ticker"].tolist()
        if dt not in returns_matrix.index:
            continue
        row = returns_matrix.loc[dt]
        for tk in top:
            r = row.get(tk)
            if pd.notna(r):
                contrib[tk] = contrib.get(tk, 0.0) + w * float(r)
    contrib.pop("SPY", None)
    return contrib


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    s = result.get("summary", {}) if isinstance(result, dict) else {}
    return {k: s.get(k) for k in ("cagr", "sharpe", "sortino", "max_drawdown", "volatility",
                                  "avg_turnover", "hit_rate", "total_return", "trading_days")}
