"""B: within-book rank-IC of the live combined-allocator conviction (target_weight).
THE ONE QUESTION: does the live signal rank finely WITHIN its book?
RESEARCH_ONLY / NON_EXECUTIONAL. Read-only on operational artifacts.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

OUT = Path("outputs/research/concentration_live_signal_2026-07-14")
HORIZONS = [1, 5, 10, 21]

# ---------- load ----------
panel = pd.read_csv(OUT / "A_recorded_signals_panel.csv")
panel["ticker"] = panel["ticker"].str.upper()
broad = panel[panel.concentration_status == "pre_concentration_broad"].copy()
broad["date"] = pd.to_datetime(broad["date"])

px = pd.read_parquet(OUT / "B_price_panel.parquet")
px["ticker"] = px["ticker"].str.upper()
px["date"] = pd.to_datetime(px["date"])
mat = px.pivot(index="date", columns="ticker", values="close").sort_index()

# forward total returns on the common trading-day calendar (positional shift)
fwd = {h: mat.shift(-h) / mat - 1.0 for h in HORIZONS}


def fwd_ret(date, ticker, h):
    try:
        return float(fwd[h].at[date, ticker])
    except KeyError:
        return np.nan


# ---------- within-book IC ----------
def daily_ic_table(book: pd.DataFrame, conv_col: str) -> pd.DataFrame:
    """Per-day within-book Spearman IC of conv_col-rank vs forward return, each horizon."""
    rows = []
    for d, g in book.groupby("date"):
        g = g[g[conv_col] > 0]
        rec = {"date": d, "n_book": len(g)}
        for h in HORIZONS:
            r = np.array([fwd_ret(d, t, h) for t in g["ticker"]])
            c = g[conv_col].to_numpy()
            ok = ~np.isnan(r)
            n = ok.sum()
            rec[f"n{h}"] = int(n)
            if n >= 4 and len(np.unique(c[ok])) > 1 and len(np.unique(r[ok])) > 1:
                rec[f"ic{h}"] = stats.spearmanr(c[ok], r[ok]).correlation
            else:
                rec[f"ic{h}"] = np.nan
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def nw_se(x: np.ndarray, lag: int) -> float:
    """Newey-West HAC standard error of the mean of series x (Bartlett weights)."""
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return np.nan
    xd = x - x.mean()
    g0 = np.dot(xd, xd) / n
    var = g0
    for k in range(1, min(lag, n - 1) + 1):
        w = 1.0 - k / (lag + 1.0)
        gk = np.dot(xd[k:], xd[:-k]) / n
        var += 2.0 * w * gk
    var = max(var, 1e-16)
    return np.sqrt(var / n)


def aggregate_ic(ic_tab: pd.DataFrame, label: str) -> pd.DataFrame:
    out = []
    for h in HORIZONS:
        s = ic_tab[f"ic{h}"].to_numpy()
        s = s[~np.isnan(s)]
        n = len(s)
        m = s.mean()
        se_iid = s.std(ddof=1) / np.sqrt(n)
        se_hac = nw_se(s, lag=h)          # overlap-aware
        t_hac = m / se_hac if se_hac and se_hac > 0 else np.nan
        # non-overlapping subsample: take rows spaced >= h positions apart
        sub = s[::max(1, h)]
        t_sub = sub.mean() / (sub.std(ddof=1) / np.sqrt(len(sub))) if len(sub) > 2 else np.nan
        # sign test (day-level): fraction positive vs 0.5
        pos = int((s > 0).sum())
        sign_p = stats.binomtest(pos, n, 0.5).pvalue
        # minimum detectable IC at 80% power (two-sided a=0.05): 2.8 * HAC SE
        mde = 2.8 * se_hac if se_hac and se_hac > 0 else np.nan
        out.append({"label": label, "horizon": h, "n_days": n, "mean_ic": round(m, 4),
                    "se_iid": round(se_iid, 4), "se_hac": round(se_hac, 4) if se_hac==se_hac else np.nan,
                    "t_hac": round(t_hac, 2) if t_hac==t_hac else np.nan,
                    "n_sub": len(sub), "t_subsample": round(t_sub, 2) if t_sub==t_sub else np.nan,
                    "frac_pos": round(pos / n, 3), "sign_test_p": round(sign_p, 4),
                    "mde_ic_80pct": round(mde, 4) if mde==mde else np.nan})
    return pd.DataFrame(out)


# ---- run: live conviction, full 93-day and 73 four-sleeve ----
full_ic = daily_ic_table(broad, "target_weight")
full_ic.to_csv(OUT / "B_daily_series" / "live_ic_full93.csv", index=False)
four = broad[broad.n_sleeves_day >= 4]
four_ic = daily_ic_table(four, "target_weight")
four_ic.to_csv(OUT / "B_daily_series" / "live_ic_foursleeve73.csv", index=False)

agg_full = aggregate_ic(full_ic, "live_full_93d")
agg_four = aggregate_ic(four_ic, "live_4sleeve_73d")

# ---- momentum head-to-head on SAME days/books (<=2026-06-09 per A parquet) ----
mom = pd.read_parquet(OUT / "A_reconstructed_conviction_panel.parquet")
mcol = [c for c in mom.columns if "momentum" in c.lower() and "conviction" in c.lower()]
mcol = mcol[0] if mcol else "conviction_momentum_only"
mom["ticker"] = mom["ticker"].str.upper()
mom["date"] = pd.to_datetime(mom["date"])
# join momentum conviction onto the recorded (date,ticker) books
def merge_mom(book):
    b = book.merge(mom[["date", "ticker", mcol]], on=["date", "ticker"], how="inner")
    return b.rename(columns={mcol: "mom_conv"})

full_mom = merge_mom(broad)
four_mom = merge_mom(four)
# restrict live to the exact same (date,ticker) rows present in momentum for a clean h2h
live_on_mom_full = broad.merge(full_mom[["date", "ticker"]], on=["date", "ticker"], how="inner")
live_on_mom_four = four.merge(four_mom[["date", "ticker"]], on=["date", "ticker"], how="inner")

full_mom_ic = daily_ic_table(full_mom, "mom_conv")
four_mom_ic = daily_ic_table(four_mom, "mom_conv")
live_h2h_full_ic = daily_ic_table(live_on_mom_full, "target_weight")
live_h2h_four_ic = daily_ic_table(live_on_mom_four, "target_weight")
full_mom_ic.to_csv(OUT / "B_daily_series" / "mom_ic_full.csv", index=False)
four_mom_ic.to_csv(OUT / "B_daily_series" / "mom_ic_foursleeve.csv", index=False)

agg_mom_full = aggregate_ic(full_mom_ic, "momentum_on_livebooks_full")
agg_mom_four = aggregate_ic(four_mom_ic, "momentum_on_livebooks_4sleeve")
agg_live_h2h_full = aggregate_ic(live_h2h_full_ic, "live_matched_to_mom_full")
agg_live_h2h_four = aggregate_ic(live_h2h_four_ic, "live_matched_to_mom_4sleeve")

ic_all = pd.concat([agg_four, agg_full, agg_live_h2h_four, agg_mom_four,
                    agg_live_h2h_full, agg_mom_full], ignore_index=True)
ic_all.to_csv(OUT / "B_ic_tables.csv", index=False)
print("=== IC TABLES ===")
print(ic_all.to_string(index=False))
print(f"\nmomentum h2h days: full={full_mom_ic.shape[0]} 4sleeve={four_mom_ic.shape[0]}")
