"""B: tie/degeneracy of the live conviction + bucket spreads (rank1-5/6-10/11+)."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

OUT = Path("outputs/research/concentration_live_signal_2026-07-14")
HORIZONS = [1, 5, 10, 21]

panel = pd.read_csv(OUT / "A_recorded_signals_panel.csv")
panel["ticker"] = panel["ticker"].str.upper()
broad = panel[panel.concentration_status == "pre_concentration_broad"].copy()
broad["date"] = pd.to_datetime(broad["date"])
four = broad[broad.n_sleeves_day >= 4]

px = pd.read_parquet(OUT / "B_price_panel.parquet")
px["ticker"] = px["ticker"].str.upper(); px["date"] = pd.to_datetime(px["date"])
mat = px.pivot(index="date", columns="ticker", values="close").sort_index()
fwd = {h: mat.shift(-h) / mat - 1.0 for h in HORIZONS}

# ---------- TIE / DEGENERACY ----------
def tie_stats(book, label):
    rows = []
    for d, g in book.groupby("date"):
        w = g[g.target_weight > 0].target_weight.round(6).to_numpy()
        n = len(w)
        if n == 0:
            continue
        # fraction of names sharing their weight with >=1 other name
        vc = pd.Series(w).value_counts()
        tied = int(sum(c for c in vc if c >= 2))
        frac_tied = tied / n
        uniq = len(vc)
        cv = w.std() / w.mean() if w.mean() > 0 else 0.0
        wsort = np.sort(w)[::-1]
        # top-5 spread: are the top 5 distinguishable?
        top5 = wsort[:5]
        top5_uniq = len(np.unique(np.round(top5, 6)))
        # is the whole book flat (all equal)?
        flat = int(uniq == 1)
        rows.append({"date": d, "n": n, "n_unique_w": uniq, "frac_tied": round(frac_tied, 3),
                     "cv_weight": round(cv, 3), "top5_unique": top5_uniq, "book_flat": flat})
    df = pd.DataFrame(rows)
    df["label"] = label
    return df

tie_full = tie_stats(broad, "full_93d")
tie_four = tie_stats(four, "four_73d")
tie_full.to_csv(OUT / "B_daily_series" / "tie_stats_full.csv", index=False)
tie_four.to_csv(OUT / "B_daily_series" / "tie_stats_four.csv", index=False)

# by era (n_sleeves)
broad2 = broad.copy()
era_rows = []
for ns, g in broad.groupby("n_sleeves_day"):
    ts = tie_stats(g, f"nsleeve{ns}")
    era_rows.append({"n_sleeves": ns, "n_dates": g.date.nunique(),
                     "mean_frac_tied": round(ts.frac_tied.mean(), 3),
                     "frac_days_fully_flat": round(ts.book_flat.mean(), 3),
                     "mean_cv_weight": round(ts.cv_weight.mean(), 3),
                     "mean_top5_unique": round(ts.top5_unique.mean(), 2)})
era_tie = pd.DataFrame(era_rows)

print("=== TIE / DEGENERACY by sleeve-era ===")
print(era_tie.to_string(index=False))
print(f"\nFULL 93d: mean frac_tied={tie_full.frac_tied.mean():.3f}, "
      f"days fully flat={tie_full.book_flat.mean():.3f}, mean top5_unique={tie_full.top5_unique.mean():.2f}")
print(f"4-SLEEVE 73d: mean frac_tied={tie_four.frac_tied.mean():.3f}, "
      f"days fully flat={tie_four.book_flat.mean():.3f}, mean top5_unique={tie_four.top5_unique.mean():.2f}, "
      f"mean cv={tie_four.cv_weight.mean():.3f}")

# ---------- BUCKET SPREADS ----------
def bucket_spread(book, label):
    """Per day: mean fwd return of conviction rank 1-5, 6-10, 11+; then avg across days."""
    recs = {h: {"b1_5": [], "b6_10": [], "b11p": []} for h in HORIZONS}
    for d, g in book.groupby("date"):
        g = g[g.target_weight > 0].sort_values(["target_weight", "ticker"], ascending=[False, True]).reset_index(drop=True)
        for h in HORIZONS:
            r = np.array([fwd[h].at[d, t] if (d in fwd[h].index and t in fwd[h].columns) else np.nan
                          for t in g.ticker])
            def m(lo, hi):
                seg = r[lo:hi]
                seg = seg[~np.isnan(seg)]
                return seg.mean() if len(seg) else np.nan
            recs[h]["b1_5"].append(m(0, 5))
            recs[h]["b6_10"].append(m(5, 10))
            recs[h]["b11p"].append(m(10, None))
    out = []
    for h in HORIZONS:
        row = {"label": label, "horizon": h}
        for b in ["b1_5", "b6_10", "b11p"]:
            a = np.array(recs[h][b]); a = a[~np.isnan(a)]
            row[f"{b}_mean"] = round(a.mean(), 4)
            row[f"{b}_ci95"] = round(1.96 * a.std(ddof=1) / np.sqrt(len(a)), 4)
        # top5 minus 11+ spread with paired t
        top = np.array(recs[h]["b1_5"]); bot = np.array(recs[h]["b11p"])
        pair = ~(np.isnan(top) | np.isnan(bot))
        diff = top[pair] - bot[pair]
        row["top5_minus_11p"] = round(diff.mean(), 4)
        row["spread_t"] = round(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))), 2)
        out.append(row)
    return pd.DataFrame(out)

buck_four = bucket_spread(four, "live_4sleeve_73d")
buck_full = bucket_spread(broad, "live_full_93d")
buckets = pd.concat([buck_four, buck_full], ignore_index=True)
buckets.to_csv(OUT / "B_bucket_spreads.csv", index=False)
print("\n=== BUCKET SPREADS (mean fwd return by conviction rank tier) ===")
print(buckets.to_string(index=False))
