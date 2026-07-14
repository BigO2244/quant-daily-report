"""Adversarial independent recompute of B's load-bearing IC numbers."""
import pandas as pd, numpy as np
from scipy import stats
OUT="outputs/research/concentration_live_signal_2026-07-14"
H=[1,5,10,21]
panel=pd.read_csv(f"{OUT}/A_recorded_signals_panel.csv")
panel["ticker"]=panel["ticker"].str.upper()
broad=panel[panel.concentration_status=="pre_concentration_broad"].copy()
broad["date"]=pd.to_datetime(broad["date"])
four=broad[broad.n_sleeves_day>=4]
px=pd.read_parquet(f"{OUT}/B_price_panel.parquet")
px["ticker"]=px["ticker"].str.upper(); px["date"]=pd.to_datetime(px["date"])
mat=px.pivot(index="date",columns="ticker",values="close").sort_index()
fwd={h:mat.shift(-h)/mat-1 for h in H}
mom=pd.read_parquet(f"{OUT}/A_reconstructed_conviction_panel.parquet")
mom["ticker"]=mom["ticker"].str.upper(); mom["date"]=pd.to_datetime(mom["date"])

def fr(d,t,h):
    try: return float(fwd[h].at[d,t])
    except KeyError: return np.nan

def daily_ic(book,col,posfilter=True):
    rows=[]
    for d,g in book.groupby("date"):
        if posfilter: g=g[g[col]>0]
        rec={"date":d}
        for h in H:
            r=np.array([fr(d,t,h) for t in g["ticker"]]); c=g[col].to_numpy()
            ok=~np.isnan(r); n=ok.sum()
            if n>=4 and len(np.unique(c[ok]))>1 and len(np.unique(r[ok]))>1:
                rec[f"ic{h}"]=stats.spearmanr(c[ok],r[ok]).correlation
            else: rec[f"ic{h}"]=np.nan
        rows.append(rec)
    return pd.DataFrame(rows)

def nw_se(x,lag):
    x=x[~np.isnan(x)]; n=len(x)
    if n<3: return np.nan
    xd=x-x.mean(); g0=xd@xd/n; var=g0
    for k in range(1,min(lag,n-1)+1):
        w=1-k/(lag+1); var+=2*w*(xd[k:]@xd[:-k]/n)
    return np.sqrt(max(var,1e-16)/n)

def agg(tab,label):
    out=[]
    for h in H:
        s=tab[f"ic{h}"].to_numpy(); s=s[~np.isnan(s)]; n=len(s); m=s.mean()
        se=nw_se(s,h); t=m/se if se and se>0 else np.nan
        out.append((label,h,n,round(m,4),round(se,4),round(t,2),round(2.8*se,4)))
    return out

print("=== RECOMPUTE live conviction IC ===")
for r in agg(daily_ic(four,"target_weight"),"4sleeve73"): print(r)
for r in agg(daily_ic(broad,"target_weight"),"full93"): print(r)

# momentum h2h
full_mom=broad.merge(mom[["date","ticker","conviction_momentum_only"]],on=["date","ticker"],how="inner").rename(columns={"conviction_momentum_only":"mc"})
four_mom=four.merge(mom[["date","ticker","conviction_momentum_only"]],on=["date","ticker"],how="inner").rename(columns={"conviction_momentum_only":"mc"})
print("\n=== momentum on live books (4sleeve, mc>0 filter as B did) ===")
for r in agg(daily_ic(four_mom,"mc"),"mom4"): print(r)
print("n rows four_mom:", four_mom.date.nunique(),"days")

# how many names dropped by mc>0 filter?
drop=four_mom.groupby("date").apply(lambda g:(g.mc<=0).sum())
tot=four_mom.groupby("date").size()
print(f"mc<=0 names dropped: total={int((four_mom.mc<=0).sum())} of {len(four_mom)} ({(four_mom.mc<=0).mean():.1%}); mean/day={drop.mean():.2f} of {tot.mean():.1f}")

# momentum WITHOUT the pos filter (rank all names incl negative momentum) -> same book as live
print("\n=== momentum 4sleeve, NO pos filter (identical book to live) ===")
for r in agg(daily_ic(four_mom,"mc",posfilter=False),"mom4_nofilt"): print(r)

# momentum LAGGED one day (align mom info cutoff to asof=d-1, removing the 1-day edge)
cal=list(mat.index)
pos={d:i for i,d in enumerate(cal)}
def lag_key(d):
    i=pos.get(d);
    return cal[i-1] if i and i>0 else None
mom_l=mom.copy(); mom_l["date_trade"]=mom_l["date"].map(lambda d: cal[pos[d]+1] if d in pos and pos[d]+1<len(cal) else pd.NaT)
# merge momentum computed at d-1 onto trade date d
four_moml=four.merge(mom_l[["date_trade","ticker","conviction_momentum_only"]].rename(columns={"date_trade":"date","conviction_momentum_only":"mc"}),on=["date","ticker"],how="inner")
print("\n=== momentum LAGGED to asof=d-1 (removes 1-day info edge), 4sleeve ===")
for r in agg(daily_ic(four_moml,"mc"),"mom4_lag"): print(r)
print("n days:", four_moml.date.nunique())

# ERA SPLIT of live 4-sleeve IC: high-VIX early (<=2026-04-30) vs low-VIX late
ic4=daily_ic(four,"target_weight")
early=ic4[ic4.date<="2026-04-30"]; late=ic4[ic4.date>"2026-04-30"]
print("\n=== ERA SPLIT live 4sleeve IC (early<=Apr30 vs late) ===")
for lab,tab in [("early",early),("late",late)]:
    for r in agg(tab,lab): print(r)
