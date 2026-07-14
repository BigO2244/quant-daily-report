"""(a) Drawdown attribution + fragility test (exclude top-k contributors).
Reconstruct EW top-N gross series from ranked forward returns; verify vs A.
RESEARCH_ONLY."""
import numpy as np, pandas as pd
from pathlib import Path
REPO=Path("/Users/brettolson/Documents/Caerus/quant-daily-report-main")
ART=REPO/"outputs/research/concentration_thesis_2026-07-14/artifacts"
s=pd.read_parquet(ART/"C_ranked_forward.parquet")  # date,ticker,rank,momentum_score,f1,f5,f21,sel...
s=s.sort_values(["date","rank"])

def sharpe(x): x=np.asarray(x,float);x=x[~np.isnan(x)];return np.sqrt(252)*x.mean()/x.std(ddof=1)
def cagr(x): x=np.asarray(x,float);x=x[~np.isnan(x)];return np.prod(1+x)**(252/len(x))-1
def mdd_window(ret):
    nav=np.cumprod(1+np.nan_to_num(ret.values))
    peak=np.maximum.accumulate(nav); dd=nav/peak-1
    tr=int(dd.argmin()); pk=int(np.argmax(nav[:tr+1]))
    return ret.index[pk], ret.index[tr], float(dd.min())

# EW top-N gross daily return from f1 (rank<=N mean)
def ew_series(N, exclude=None):
    d=s if exclude is None else s[~s["ticker"].isin(exclude)]
    d=d.sort_values(["date","rank"]) if exclude is None else \
      d.assign(rank=d.sort_values(["date","momentum_score"],ascending=[True,False]).groupby("date").cumcount()+1)
    top=d[d["rank"]<=N]
    r=top.groupby("date")["f1"].mean()
    return r

print("=== reproduce A EW gross Sharpe (should ~match top1 .775/top5 1.006/top10 .957) ===")
for N in [1,3,5,10]:
    r=ew_series(N)
    print(f"  top{N}: gross Sharpe {sharpe(r):.3f}  CAGR {cagr(r):.3f}")

# ---- Drawdown attribution: top1 and top5 ----
print("\n=== (a) Max-drawdown window attribution ===")
for N in [1,5]:
    r=ew_series(N)
    pk,tr,dd=mdd_window(r)
    print(f"\n top{N}: MDD {dd:.3f} from {pk.date()} to {tr.date()} ({(tr-pk).days} cal days)")
    win=s[(s['date']>=pk)&(s['date']<=tr)&(s['rank']<=N)].copy()
    win["contrib"]=win["f1"]/N  # EW contribution to daily port return
    byname=win.groupby("ticker")["contrib"].agg(["sum","count"]).sort_values("sum")
    tot=byname["sum"].sum()
    print(f"   sum of contributions over window (approx cum log-ish): {tot:.3f}; #distinct names held: {len(byname)}")
    print("   worst 8 contributor names (sum contribution, days held):")
    for tk,row in byname.head(8).iterrows():
        print(f"     {tk:6s} {row['sum']:+.3f}  ({int(row['count'])}d)")
    # concentration of losses: share of total negative contribution from worst 3 names
    neg=byname[byname["sum"]<0]["sum"]
    top3share=neg.head(3).sum()/neg.sum() if neg.sum()!=0 else np.nan
    print(f"   worst-3 names = {top3share*100:.0f}% of total negative contribution (1.0=one blowup, ~equal=many)")

# ---- Fragility test: exclude top cumulative contributors, re-run top5 ----
print("\n=== (a) Fragility: exclude top-k lifetime contributors, re-run top5 EW gross ===")
# lifetime contribution of each name to top5 (rank<=5, weight 1/5)
t5=s[s["rank"]<=5].copy(); t5["contrib"]=t5["f1"]/5
life=t5.groupby("ticker")["contrib"].sum().sort_values(ascending=False)
print("  top-5 lifetime BEST contributors:", list(life.head(6).index))
base=ew_series(5); base_sh=sharpe(base); base_cg=cagr(base)
print(f"  baseline top5 gross: Sharpe {base_sh:.3f} CAGR {base_cg:.3f}")
b10=ew_series(10); b10_sh=sharpe(b10)
print(f"  baseline top10 gross Sharpe {b10_sh:.3f} ; top5-top10 edge {base_sh-b10_sh:+.3f}")
for k in [1,3,5]:
    ex=set(life.head(k).index)
    r=ew_series(5, exclude=ex)
    r10=ew_series(10, exclude=ex)
    print(f"  exclude top-{k} contributors {sorted(ex)}: top5 Sharpe {sharpe(r):.3f} CAGR {cagr(r):.3f} "
          f"| top5-top10 edge now {sharpe(r)-sharpe(r10):+.3f}")
print("\nDONE dd")
