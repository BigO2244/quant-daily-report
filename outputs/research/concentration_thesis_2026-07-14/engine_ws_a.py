"""Workstream A research engine: PIT-masked concentration sweep with realistic
costs and settled-cash T+1 staging. RESEARCH_ONLY / NON_EXECUTIONAL.

Reuses the committed alpha_lab signal builder and the LIVE waterfill logic from
core/concentration.py (imported read-only). Adds, relative to
research.alpha_lab_v2.engine:
  * PIT per-date eligibility mask (survivorship-free universe)
  * conviction-weighted waterfill sizing (cap + cash floor) mirroring core.concentration
  * per-side half-spread slippage cost model (tiered)
  * settled-cash T+1 staging drag (cash account, buys <= settled cash)

Validated to reproduce research.alpha_lab_v2.engine on the unconstrained EW case.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/Users/brettolson/Documents/Caerus/quant-daily-report-main")
sys.path.insert(0, str(REPO))
from alpha_stack.research.metrics import summarise_performance  # noqa: E402
from core.concentration import _capped_waterfill  # noqa: E402  (read-only import)

SEP = REPO / "data" / "research_cache" / "sharadar_sep"
MATRIX = REPO / "alpha_stack_cache" / "prices" / "_matrix_prices_2007_2026.parquet"
LARGECAP = REPO / "data" / "pit_universe" / "membership_universe_large_cap.csv"
LEGACY = REPO / "data" / "universe.csv"


def norm_ticker(t: str) -> str:
    t = str(t or "").strip().upper()
    if "-" in t:
        head, _, tail = t.rpartition("-")
        if head and len(tail) <= 2 and tail.isalpha():
            return f"{head}.{tail}"
    return t


def sep_close(ticker: str) -> pd.DataFrame:
    p = SEP / f"{ticker}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    if "closeadj" not in df or "date" not in df:
        return pd.DataFrame()
    df = df[["date", "closeadj"]].rename(columns={"closeadj": "close"})
    df["ticker"] = ticker
    return df[["date", "ticker", "close"]]


def spy_close() -> pd.DataFrame:
    m = pd.read_parquet(MATRIX)
    s = m[["SPY"]].rename(columns={"SPY": "close"}).reset_index()
    s.columns = ["date", "close"]
    s["date"] = pd.to_datetime(s["date"]).dt.strftime("%Y-%m-%d")
    s["ticker"] = "SPY"
    return s[["date", "ticker", "close"]]


def legacy_tickers() -> list[str]:
    import io
    raw = "\n".join(l for l in LEGACY.read_text().splitlines() if l.strip())
    rows = list(csv.DictReader(io.StringIO(raw)))
    return sorted({norm_ticker(r["ticker"]) for r in rows if r.get("ticker", "").strip()})


def largecap_membership() -> pd.DataFrame:
    rows = list(csv.DictReader(open(LARGECAP)))
    df = pd.DataFrame(rows)
    df["ticker"] = df["ticker"].map(norm_ticker)
    df["start"] = pd.to_datetime(df["membership_start_date"], errors="coerce")
    df["end"] = pd.to_datetime(df["membership_end_date"].replace("", np.nan), errors="coerce")
    return df[["ticker", "start", "end"]]


def build_sep_panel(tickers: list[str]) -> pd.DataFrame:
    frames = []
    for t in tickers:
        f = sep_close(t)
        if not f.empty:
            frames.append(f)
    frames.append(spy_close())
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"]).dt.strftime("%Y-%m-%d")
    return panel


# ---- eligibility mask ------------------------------------------------------
def apply_pit_mask(signals: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    """Set signal_ready False where a ticker is not PIT-eligible on that date.
    A ticker with multiple membership spans is eligible if ANY span covers the date.
    SPY always eligible (benchmark, never selected via signal_ready path anyway)."""
    sig = signals.copy()
    sig["date_dt"] = pd.to_datetime(sig["date"])
    span = membership.groupby("ticker").agg(start=("start", "min"), end=("end", "max"))
    # For tickers with any open (NaT) end, treat as open-ended:
    open_end = membership.groupby("ticker")["end"].apply(lambda s: s.isna().any())
    span["open"] = open_end
    starts = sig["ticker"].map(span["start"])
    ends = sig["ticker"].map(span["end"])
    opens = sig["ticker"].map(span["open"]).fillna(False)
    elig = (sig["date_dt"] >= starts) & (opens | (sig["date_dt"] <= ends))
    elig = elig.fillna(False)
    elig = elig | (sig["ticker"] == "SPY")
    sig["pit_eligible"] = elig
    sig["signal_ready"] = sig["signal_ready"] & elig
    return sig.drop(columns=["date_dt"])


# ---- backtest core ---------------------------------------------------------
def prepare(signals: pd.DataFrame, start: str, end: str):
    f = signals.copy()
    f["date"] = pd.to_datetime(f["date"])
    f = f[(f["date"] >= pd.Timestamp(start)) & (f["date"] <= pd.Timestamp(end))]
    f = f.sort_values(["date", "ticker"]).reset_index(drop=True)
    rmat = f.pivot(index="date", columns="ticker", values="close").sort_index().pct_change().shift(-1)
    dates = list(rmat.index[:-1])
    return f, rmat, dates


def select_weights(daily: pd.DataFrame, top_n, sizing: str, cap: float, cash: float) -> pd.Series:
    """Return target weights (sum <= 1). sizing in {'ew','waterfill'}.
    'full' book: top_n=None -> hold all signal_ready EW."""
    cand = daily[daily["signal_ready"]].copy()
    if cand.empty:
        return pd.Series(dtype=float)
    cand = cand[cand["ticker"] != "SPY"]
    cand = cand.sort_values(["momentum_score", "ticker"], ascending=[False, True])
    if top_n is not None:
        cand = cand.head(int(top_n))
    names = cand["ticker"].astype(str).tolist()
    if not names:
        return pd.Series(dtype=float)
    if sizing == "ew":
        w = min(1.0 / len(names), cap)
        return pd.Series(w, index=names, dtype=float)
    # waterfill: conviction = momentum_score shifted to be strictly positive
    conv = cand["momentum_score"].astype(float).to_numpy()
    conv = conv - conv.min() + 1e-6 if conv.min() <= 0 else conv
    invested = 1.0 - cash
    alloc = _capped_waterfill(list(conv), budget=invested, cap=cap)
    s = pd.Series(alloc, index=names, dtype=float)
    return s[s > 1e-12]


def run(signals, spec, start, end):
    """spec: dict with top_n, sizing, cap, cash, half_spread_bps, settled_cash(bool)."""
    f, rmat, dates = prepare(signals, start, end)
    if f.empty or not dates:
        return None
    top_n = spec["top_n"]; sizing = spec["sizing"]; cap = spec["cap"]; cash = spec["cash"]
    hs = spec["half_spread_bps"] / 10000.0
    settled = spec.get("settled_cash", False)
    exec_lag = int(spec.get("exec_lag_days", 0))
    if exec_lag:
        rmat = rmat.shift(-exec_lag)  # earn dt+lag -> dt+lag+1 return instead of dt -> dt+1
    by_date = {d: g for d, g in f.groupby("date")}

    prev_w = pd.Series(dtype=float)
    # settled-cash state: unsettled proceeds pipeline (settles next day).
    # Seed with 1.0: the initial cash deposit is fully settled and spendable on day 1.
    pending_cash = 1.0 if settled else 0.0
    nav = 1.0
    recs = []
    wrecs = []
    active = {}
    holds = []
    for dt in dates:
        daily = by_date.get(dt)
        if daily is None:
            new_w = prev_w.copy()
        else:
            target = select_weights(daily, top_n, sizing, cap, cash)
            if settled:
                new_w, pending_cash = stage_settled(prev_w, target, pending_cash)
            else:
                new_w = target
        # cost on turnover (per-side half-spread)
        turn = _turnover(prev_w, new_w)
        nxt = rmat.loc[dt]
        gross = float(nxt.reindex(new_w.index).fillna(0.0).mul(new_w).sum()) if not new_w.empty else 0.0
        cost = turn * hs
        net = gross - cost
        nav *= 1.0 + net
        _update_holds(dt, prev_w, new_w, active, holds)
        recs.append({"date": dt, "gross_return": gross, "net_return": net, "turnover": turn,
                     "cost": cost, "n_names": int((new_w > 0).sum()),
                     "invested": float(new_w.sum()), "cash_weight": float(max(0.0, 1.0 - new_w.sum()))})
        wrecs.append(new_w.rename(dt))
        prev_w = new_w
    daily_df = pd.DataFrame(recs)
    wdf = pd.DataFrame(wrecs).fillna(0.0)
    nav_s = pd.Series((1 + daily_df["net_return"]).cumprod().values, index=pd.to_datetime(daily_df["date"]), name="nav")
    ret_s = pd.Series(daily_df["net_return"].values, index=pd.to_datetime(daily_df["date"]), name="return")
    bench = None
    if "SPY" in rmat.columns:
        bench = pd.to_numeric(pd.Series(rmat["SPY"].values, index=rmat.index), errors="coerce").reindex(ret_s.index)
    summary = summarise_performance(nav_s, returns=ret_s, benchmark_returns=bench, label=spec["name"])
    ann_turn = float(daily_df["turnover"].mean() * 252)
    cost_bps_yr = float(daily_df["cost"].mean() * 252 * 10000)
    summary.update({
        "name": spec["name"], "top_n": (0 if top_n is None else int(top_n)), "sizing": sizing,
        "avg_n_names": round(float(daily_df["n_names"].mean()), 2),
        "avg_cash_weight": round(float(daily_df["cash_weight"].mean()), 4),
        "ann_turnover": round(ann_turn, 3),
        "cost_drag_bps_yr": round(cost_bps_yr, 1),
        "avg_holding_days": round(sum(holds) / len(holds), 1) if holds else None,
        "hit_rate_daily": round(float((ret_s > 0).mean()), 4),
    })
    return {"summary": summary, "daily": daily_df, "weights": wdf, "returns": ret_s, "nav": nav_s}


def stage_settled(prev_w: pd.Series, target: pd.Series, pending_cash: float):
    """Cash-account T+1 staging. Sells free cash that settles NEXT day; buys today
    limited to already-settled cash. Returns (executed_weights_today, new_pending_cash).

    Model (weight space, NAV-normalised): settled cash available today = pending_cash
    from yesterday's sales. Sells execute fully (reduce/exit positions immediately).
    Buys are funded only up to settled cash; any unfunded buy is deferred (stays cash,
    retried next day as target persists). Today's sale proceeds go to pending (settle T+1)."""
    all_names = prev_w.index.union(target.index)
    p = prev_w.reindex(all_names, fill_value=0.0)
    t = target.reindex(all_names, fill_value=0.0)
    delta = t - p
    sells = -delta.clip(upper=0.0)   # positive amounts sold
    buys = delta.clip(lower=0.0)     # desired buys
    proceeds_today = float(sells.sum())
    settled_available = float(pending_cash)
    want_buy = float(buys.sum())
    funded = min(want_buy, settled_available)
    scale = (funded / want_buy) if want_buy > 1e-12 else 0.0
    executed = p - sells + buys * scale
    executed = executed[executed > 1e-12]
    # new pending = today's proceeds; leftover settled cash rolls forward as spendable
    leftover_settled = settled_available - funded
    new_pending = proceeds_today + leftover_settled
    return executed, new_pending


def _turnover(a: pd.Series, b: pd.Series) -> float:
    idx = a.index.union(b.index)
    return float((b.reindex(idx, fill_value=0.0) - a.reindex(idx, fill_value=0.0)).abs().sum())


def _update_holds(dt, prev_w, new_w, active, holds):
    pn = set(prev_w[prev_w > 0].index); nn = set(new_w[new_w > 0].index)
    for tk in nn - pn:
        active[tk] = dt
    for tk in pn - nn:
        s = active.pop(tk, None)
        if s is not None:
            holds.append(int((dt - s).days))
