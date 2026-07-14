"""B: cost-aware top-N concentration sweep on the LIVE recorded conviction.
N in {1,3,5,7,10,full}, waterfill (0.50 cap / 5% cash, core.concentration semantics)
and equal-weight; 5 bps half-spread cost; T+1 settled-cash staging.
DESCRIPTIVE CONTEXT ONLY -- 4.5 months / ~92 rebalances cannot rank N by Sharpe.
The IC (B_analysis) is the inferential result.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path("/Users/brettolson/Documents/Caerus/quant-daily-report-main")
sys.path.insert(0, str(REPO))
from core.concentration import _capped_waterfill  # read-only live waterfill
sys.path.insert(0, str(REPO / "outputs/research/concentration_thesis_2026-07-14"))
from engine_ws_a import stage_settled, _turnover  # reuse prior study's staging/turnover

OUT = REPO / "outputs/research/concentration_live_signal_2026-07-14"
CAP, CASH, HS = 0.50, 0.05, 5 / 10000.0

panel = pd.read_csv(OUT / "A_recorded_signals_panel.csv")
panel["ticker"] = panel["ticker"].str.upper()
broad = panel[panel.concentration_status == "pre_concentration_broad"].copy()
broad["date"] = pd.to_datetime(broad["date"])
dates = sorted(broad.date.unique())

px = pd.read_parquet(OUT / "B_price_panel.parquet")
px["ticker"] = px["ticker"].str.upper(); px["date"] = pd.to_datetime(px["date"])
mat = px.pivot(index="date", columns="ticker", values="close").sort_index()
ret1 = mat.shift(-1) / mat - 1.0  # next-trading-day return, positional


def book_weights(g, n, sizing):
    g = g[g.target_weight > 0].sort_values(["target_weight", "ticker"], ascending=[False, True])
    if n is not None:
        g = g.head(n)
    names = g.ticker.tolist()
    if not names:
        return pd.Series(dtype=float)
    if sizing == "ew":
        w = min(1.0 / len(names), CAP)
        return pd.Series(w, index=names, dtype=float)
    conv = g.target_weight.to_numpy().astype(float)
    alloc = _capped_waterfill(list(conv), budget=1.0 - CASH, cap=CAP)
    s = pd.Series(alloc, index=names, dtype=float)
    return s[s > 1e-12]


def run_level(n, sizing, settled=True):
    by = {d: g for d, g in broad.groupby("date")}
    prev = pd.Series(dtype=float)
    pending = 1.0 if settled else 0.0
    recs = []
    for d in dates:
        target = book_weights(by[d], n, sizing)
        if settled:
            new_w, pending = stage_settled(prev, target, pending)
        else:
            new_w = target
        turn = _turnover(prev, new_w)
        if d in ret1.index:
            nxt = ret1.loc[d]
            gross = float(nxt.reindex(new_w.index).fillna(0.0).mul(new_w).sum()) if not new_w.empty else 0.0
        else:
            gross = 0.0
        cost = turn * HS
        recs.append({"date": d, "gross": gross, "net": gross - cost, "turnover": turn,
                     "n_names": int((new_w > 0).sum()), "cash": float(max(0.0, 1 - new_w.sum()))})
        prev = new_w
    return pd.DataFrame(recs)


def sharpe_boot(r, nboot=5000, seed=0):
    r = r[~np.isnan(r)]
    rng = np.random.default_rng(seed)
    def sh(x):
        return x.mean() / x.std(ddof=1) * np.sqrt(252) if x.std(ddof=1) > 0 else np.nan
    base = sh(r)
    idx = rng.integers(0, len(r), size=(nboot, len(r)))
    boots = np.array([sh(r[i]) for i in idx])
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    return base, lo, hi


def maxdd(nav):
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1).min())


rows = []
levels = [("N1", 1), ("N3", 3), ("N5", 5), ("N7", 7), ("N10", 10), ("full", None)]
for sizing in ["waterfill", "ew"]:
    for name, n in levels:
        df = run_level(n, sizing, settled=True)
        df.to_csv(OUT / "B_daily_series" / f"sweep_{sizing}_{name}.csv", index=False)
        r = df.net.to_numpy()
        nav = np.cumprod(1 + np.nan_to_num(r))
        sh, lo, hi = sharpe_boot(r)
        rows.append({"sizing": sizing, "level": name, "avg_n": round(df.n_names.mean(), 1),
                     "cum_return": round(nav[-1] - 1, 4), "ann_sharpe": round(sh, 2),
                     "sharpe_ci95_lo": round(lo, 2), "sharpe_ci95_hi": round(hi, 2),
                     "max_dd": round(maxdd(nav), 4), "ann_turnover": round(df.turnover.mean() * 252, 2),
                     "hit_rate": round(float((r > 0).mean()), 3), "avg_cash": round(df.cash.mean(), 3)})

# SPY benchmark over same rebalance dates
spy_r = np.array([float(ret1.at[d, "SPY"]) if (d in ret1.index and "SPY" in ret1.columns) else np.nan for d in dates])
spy_nav = np.cumprod(1 + np.nan_to_num(spy_r))
sh, lo, hi = sharpe_boot(spy_r)
rows.append({"sizing": "bench", "level": "SPY", "avg_n": 1, "cum_return": round(spy_nav[-1] - 1, 4),
             "ann_sharpe": round(sh, 2), "sharpe_ci95_lo": round(lo, 2), "sharpe_ci95_hi": round(hi, 2),
             "max_dd": round(maxdd(spy_nav), 4), "ann_turnover": 0.0,
             "hit_rate": round(float((spy_r > 0).mean()), 3), "avg_cash": 0.0})

sweep = pd.DataFrame(rows)
sweep.to_csv(OUT / "B_sweep_table.csv", index=False)
print(f"Sweep over {len(dates)} recorded rebalance days ({dates[0].date()}..{dates[-1].date()})")
print("Return convention: 1-trading-day-forward per recorded rebalance, compounded; 5bps half-spread; T+1 settled cash.")
print(sweep.to_string(index=False))
