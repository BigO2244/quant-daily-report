"""(c) Selection precision: do ranks 1-5 out-predict 6-10? IC, bucket spreads, per-rank.
THE load-bearing analysis. Uses PIT signals panel; forward returns from close matrix.
RESEARCH_ONLY."""
import numpy as np, pandas as pd, json
from pathlib import Path
from scipy import stats

REPO = Path("/Users/brettolson/Documents/Caerus/quant-daily-report-main")
D = REPO/"outputs/research/concentration_thesis_2026-07-14"
ART = D/"artifacts"

sig = pd.read_parquet(D/"data/signals_largecap_pit.parquet",
                      columns=["date","ticker","close","momentum_score","signal_ready","pit_eligible","spy_above_200dma"])
sig["date"] = pd.to_datetime(sig["date"])
sig = sig[(sig["date"]>="2014-01-02")&(sig["date"]<="2024-12-31")]
# selectable set = what the engine ranks on
sig["sel"] = sig["signal_ready"].astype(bool) & sig["pit_eligible"].astype(bool)

# forward-return matrix per horizon from close matrix (common trading-day index)
cm = sig.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
dates = cm.index
H = [1,5,21]
fwd = {h: (cm.shift(-h)/cm - 1.0) for h in H}

# rank among selectable each date (rank 1 = highest momentum_score)
s = sig[sig["sel"]].copy()
s = s.sort_values(["date","momentum_score","ticker"], ascending=[True,False,True])
s["rank"] = s.groupby("date").cumcount()+1

# attach forward returns
for h in H:
    fm = fwd[h]
    # map (date,ticker)->fwd
    fs = fm.stack().rename(f"f{h}")
    s = s.merge(fs, left_on=["date","ticker"], right_index=True, how="left")

s.to_parquet(ART/"C_ranked_forward.parquet")  # cache

# ---- 1. Bucket forward-return spreads (per date mean, then avg over dates) ----
def bucket(r):
    if r<=5: return "r01_05"
    if r<=10: return "r06_10"
    if r<=20: return "r11_20"
    if r<=50: return "r21_50"
    return "r51_plus"
s["bucket"] = s["rank"].map(bucket)
print("=== (c1) Forward-return by rank bucket (daily mean %, annualized-ish; mean of per-name fwd ret) ===")
rows=[]
for h in H:
    # per-date bucket mean, then mean across dates -> avoids day-count weighting
    g = s.groupby(["date","bucket"])[f"f{h}"].mean().reset_index()
    m = g.groupby("bucket")[f"f{h}"].mean()
    for bk in ["r01_05","r06_10","r11_20","r21_50","r51_plus"]:
        rows.append(dict(horizon=h, bucket=bk, mean_fwd_pct=round(m.get(bk,np.nan)*100,4)))
bt = pd.DataFrame(rows).pivot(index="bucket", columns="horizon", values="mean_fwd_pct")
bt = bt.reindex(["r01_05","r06_10","r11_20","r21_50","r51_plus"])
print(bt.to_string())
bt.to_csv(ART/"C_bucket_spreads.csv")

# spread 1-5 vs 6-10, with paired t-test on daily bucket-mean differences
print("\n=== (c2) rank[1-5] vs rank[6-10] daily spread + t-test ===")
sp_rows=[]
for h in H:
    piv = s.groupby(["date","bucket"])[f"f{h}"].mean().unstack()
    d15_610 = (piv["r01_05"]-piv["r06_10"]).dropna()
    d15_1120 = (piv["r01_05"]-piv["r11_20"]).dropna()
    d610_1120 = (piv["r06_10"]-piv["r11_20"]).dropna()
    for lbl,dd in [("1-5 vs 6-10",d15_610),("1-5 vs 11-20",d15_1120),("6-10 vs 11-20",d610_1120)]:
        # non-overlapping for h>1 clean t; here report naive t and NW-lite (every h-th day)
        t_naive = dd.mean()/dd.std(ddof=1)*np.sqrt(len(dd))
        nov = dd.iloc[::h]
        t_nov = nov.mean()/nov.std(ddof=1)*np.sqrt(len(nov))
        sp_rows.append(dict(horizon=h, pair=lbl, mean_spread_bps=round(dd.mean()*1e4,2),
                            t_naive=round(t_naive,2), t_nonoverlap=round(t_nov,2), n_days=len(dd)))
sp = pd.DataFrame(sp_rows); print(sp.to_string(index=False)); sp.to_csv(ART/"C_bucket_ttests.csv",index=False)

# ---- 3. Rank IC: daily Spearman(momentum_score, fwd) over ALL selectable ----
print("\n=== (c3) Rank Information Coefficient (daily Spearman, full selectable set) ===")
ic_rows=[]
for h in H:
    def daily_ic(g):
        x=g["momentum_score"].values; y=g[f"f{h}"].values
        m=~np.isnan(y)
        if m.sum()<20: return np.nan
        return stats.spearmanr(x[m], y[m]).correlation
    ic = s.groupby("date").apply(daily_ic).dropna()
    mean_ic=ic.mean(); sd=ic.std(ddof=1)
    t_naive=mean_ic/sd*np.sqrt(len(ic))
    ic_nov=ic.iloc[::h]; t_nov=ic_nov.mean()/ic_nov.std(ddof=1)*np.sqrt(len(ic_nov))
    hit=(ic>0).mean()
    ic_rows.append(dict(horizon=h, mean_IC=round(mean_ic,4), IC_std=round(sd,3),
                        t_naive=round(t_naive,2), t_nonoverlap=round(t_nov,2),
                        IC_hitrate=round(hit,3), n=len(ic)))
    ic.to_frame("ic").to_csv(ART/f"C_ic_timeseries_h{h}.csv")
icdf=pd.DataFrame(ic_rows); print(icdf.to_string(index=False)); icdf.to_csv(ART/"C_ic_summary.csv",index=False)

# ---- 4. IC RESTRICTED to top-20 (is ordering INSIDE top decile informative?) ----
print("\n=== (c4) IC within top-20 only (is intra-top-decile ordering informative?) ===")
t20 = s[s["rank"]<=20]
ic_rows2=[]
for h in H:
    def dic(g):
        x=g["momentum_score"].values; y=g[f"f{h}"].values
        m=~np.isnan(y)
        if m.sum()<10: return np.nan
        return stats.spearmanr(x[m], y[m]).correlation
    ic=t20.groupby("date").apply(dic).dropna()
    t=ic.mean()/ic.std(ddof=1)*np.sqrt(len(ic))
    ic_rows2.append(dict(horizon=h, mean_IC_top20=round(ic.mean(),4), t_naive=round(t,2), n=len(ic)))
print(pd.DataFrame(ic_rows2).to_string(index=False))
pd.DataFrame(ic_rows2).to_csv(ART/"C_ic_within_top20.csv",index=False)

# ---- 5. Per-rank mean forward return, ranks 1..20 (is rank1 > rank5?) ----
print("\n=== (c5) Per-exact-rank mean forward return (bps), ranks 1..20 ===")
pr_rows=[]
for r in range(1,21):
    row=dict(rank=r)
    for h in H:
        row[f"h{h}_bps"]=round(s.loc[s["rank"]==r,f"f{h}"].mean()*1e4,2)
    pr_rows.append(row)
prdf=pd.DataFrame(pr_rows); print(prdf.to_string(index=False)); prdf.to_csv(ART/"C_per_rank.csv",index=False)
# monotonicity: Spearman between rank(1..20) and its mean fwd return
for h in H:
    rr=prdf["rank"].values; vv=prdf[f"h{h}_bps"].values
    rho=stats.spearmanr(rr,vv).correlation
    print(f"  monotonicity rho(rank, mean_fwd) h{h}: {rho:+.3f}  (neg = higher rank predicts higher return)")
print("\nDONE rank")
