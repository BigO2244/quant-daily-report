"""(b) SENSITIVITY ONLY: VIX-regime split (alternate to pre-committed 200dma).
Binary at VIX=20 (live LOW threshold). RESEARCH_ONLY."""
import numpy as np, pandas as pd
from pathlib import Path
REPO=Path("/Users/brettolson/Documents/Caerus/quant-daily-report-main")
ART=REPO/"outputs/research/concentration_thesis_2026-07-14/artifacts"
LEVELS=["top1","top3","top5","top10","full"]
def sharpe(x):x=np.asarray(x,float);x=x[~np.isnan(x)];return np.sqrt(252)*x.mean()/x.std(ddof=1)
def cagr(x):x=np.asarray(x,float);x=x[~np.isnan(x)];return np.prod(1+x)**(252/len(x))-1
net=pd.DataFrame({lv:pd.read_csv(ART/f"daily_clean_2014_2024_ew_nosettle_{lv}.csv",parse_dates=["date"]).set_index("date")["net_return"] for lv in LEVELS})
vix=pd.read_parquet(REPO/"data/macro/VIXCLS.parquet")[["date","value"]].set_index("date")["value"]
vix.index=pd.to_datetime(vix.index)
v=vix.reindex(net.index).ffill()
calm=v<20  # LOW regime per live config; else stressed (ELEVATED+)
print(f"frac days VIX<20 (calm): {calm.mean():.3f}  transitions: {int((calm.astype(int).diff().abs()==1).sum())}")
rows=[]
for lv in LEVELS:
    for st,nm in [(True,"VIX<20 calm"),(False,"VIX>=20 stress")]:
        x=net.loc[calm==st,lv].dropna()
        rows.append(dict(level=lv,regime=nm,n=len(x),sharpe=round(sharpe(x),3),cagr=round(cagr(x),3)))
t=pd.DataFrame(rows);print(t.to_string(index=False));t.to_csv(ART/"C_regime_vix_sensitivity.csv",index=False)
for st,nm in [(True,"calm"),(False,"stress")]:
    e=sharpe(net.loc[calm==st,"top5"].dropna())-sharpe(net.loc[calm==st,"top10"].dropna())
    print(f"  top5-top10 edge {nm}: {e:+.3f}")
