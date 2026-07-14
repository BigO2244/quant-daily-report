"""Workstream C core analysis: variance decomp (a), regime (b), costs (d), bootstrap (e).
Uses A's EW clean daily series (primary; matches A headline Sharpes) + SPY returns.
RESEARCH_ONLY."""
import numpy as np, pandas as pd, json
from pathlib import Path

REPO = Path("/Users/brettolson/Documents/Caerus/quant-daily-report-main")
D = REPO / "outputs/research/concentration_thesis_2026-07-14"
ART = D / "artifacts"
OUT = ART
rng = np.random.default_rng(20260714)

LEVELS = ["top1","top3","top5","top10","full"]

def load_series(sizing="ew", settle="nosettle"):
    out = {}
    for lv in LEVELS:
        f = ART / f"daily_clean_2014_2024_{sizing}_{settle}_{lv}.csv"
        df = pd.read_csv(f, parse_dates=["date"]).set_index("date")
        out[lv] = df
    return out

# SPY daily returns aligned to series dates
def spy_returns(index):
    m = pd.read_parquet(REPO/"alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet")
    s = m["SPY"].copy(); s.index = pd.to_datetime(s.index)
    # engine convention: return earned on date t is close[t+1]/close[t]-1
    r = s.pct_change().shift(-1)
    return r.reindex(index)

def sharpe(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    sd = x.std(ddof=1)
    return np.sqrt(252)*x.mean()/sd if sd>0 else np.nan

def cagr(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    return (np.prod(1+x))**(252/n)-1 if n>0 else np.nan

def maxdd(x):
    nav = np.cumprod(1+np.asarray(x,float))
    peak = np.maximum.accumulate(nav)
    return float((nav/peak-1).min())

ser = load_series("ew","nosettle")
idx = ser["top5"].index
spy = spy_returns(idx)

# net return matrix
net = pd.DataFrame({lv: ser[lv]["net_return"] for lv in LEVELS})
gross = pd.DataFrame({lv: ser[lv]["gross_return"] for lv in LEVELS})
net["SPY"] = spy

# sanity: reproduce A headline sharpes
print("=== reproduce A EW clean net Sharpes ===")
for lv in LEVELS:
    print(f"  {lv:5s} Sharpe {sharpe(net[lv]):.3f}  CAGR {cagr(net[lv]):.3f}  MDD {maxdd(net[lv].dropna()):.3f}")
print(f"  SPY   Sharpe {sharpe(spy):.3f}  CAGR {cagr(spy):.3f}")

# ---------- (a) VARIANCE DECOMPOSITION: single-factor (market) ----------
print("\n=== (a) Variance decomposition: idiosyncratic vs market (single-factor OLS on SPY) ===")
va = {}
sub = net.dropna()
for lv in LEVELS:
    y = sub[lv].values; x = sub["SPY"].values
    b1, b0 = np.polyfit(x, y, 1)
    yhat = b0 + b1*x
    ss_res = np.sum((y-yhat)**2); ss_tot = np.sum((y-y.mean())**2)
    r2 = 1 - ss_res/ss_tot
    tot_var = y.var(ddof=1)
    sys_var = (b1**2)*x.var(ddof=1)
    idio_var = tot_var - sys_var
    va[lv] = dict(beta=b1, r2=r2, tot_vol_ann=np.sqrt(252*tot_var),
                  idio_frac=1-r2, idio_vol_ann=np.sqrt(252*max(idio_var,0)))
    print(f"  {lv:5s} beta {b1:.3f}  R2(mkt) {r2:.3f}  idio_frac {1-r2:.3f}  "
          f"tot_vol {np.sqrt(252*tot_var)*100:.1f}%  idio_vol {np.sqrt(252*max(idio_var,0))*100:.1f}%")
pd.DataFrame(va).T.to_csv(OUT/"C_variance_decomp.csv")

# ---------- (b) REGIME: PRIMARY = SPY above/below 200dma ----------
# PRE-COMMIT: primary regime = spy_above_200dma from A's regime_spy_trend.csv.
# risk-on/trending = above ; chop/risk-off = below. This is chosen BEFORE seeing splits.
print("\n=== (b) Regime split: PRIMARY = SPY vs 200dma (pre-committed) ===")
reg = pd.read_csv(ART/"regime_spy_trend.csv", parse_dates=["date"]).set_index("date")["spy_above_200dma"]
reg = reg.reindex(idx).ffill()
sub2 = net.copy(); sub2["risk_on"] = reg
# transition count
trans = int((reg.astype(int).diff().abs()==1).sum())
frac_on = float(reg.mean())
print(f"  days: {len(reg)}  frac risk-on (above 200dma): {frac_on:.3f}  regime transitions: {trans}")
rows=[]
for lv in LEVELS+["SPY"]:
    for state,name in [(True,"risk_on"),(False,"risk_off")]:
        mask = sub2["risk_on"]==state
        x = sub2.loc[mask, lv].dropna()
        rows.append(dict(level=lv, regime=name, n=len(x), sharpe=round(sharpe(x),3),
                         cagr=round(cagr(x),3), mean_bps=round(x.mean()*1e4,2), maxdd=round(maxdd(x),3)))
reg_tbl = pd.DataFrame(rows)
reg_tbl.to_csv(OUT/"C_regime_primary.csv", index=False)
print(reg_tbl.to_string(index=False))
# top5 vs top10 edge by regime
for state,name in [(True,"risk_on"),(False,"risk_off")]:
    mask = sub2["risk_on"]==state
    e = sharpe(sub2.loc[mask,"top5"].dropna())-sharpe(sub2.loc[mask,"top10"].dropna())
    print(f"  top5-top10 Sharpe edge in {name}: {e:+.3f}")

# ---------- (d) COST / TURNOVER: gross vs net edge ----------
print("\n=== (d) Cost/turnover drag: gross vs net edge, top5 vs top10 ===")
rows=[]
for lv in LEVELS:
    g = gross[lv]; n_ = net[lv]
    rows.append(dict(level=lv,
        gross_sharpe=round(sharpe(g),3), net_sharpe=round(sharpe(n_),3),
        gross_cagr=round(cagr(g),3), net_cagr=round(cagr(n_),3),
        ann_turnover=round(ser[lv]["turnover"].mean()*252,2),
        cost_drag_bps=round(ser[lv]["cost"].mean()*252*1e4,1)))
cost_tbl = pd.DataFrame(rows); cost_tbl.to_csv(OUT/"C_cost_edge.csv", index=False)
print(cost_tbl.to_string(index=False))
g_edge = sharpe(gross["top5"])-sharpe(gross["top10"])
n_edge = sharpe(net["top5"])-sharpe(net["top10"])
print(f"  top5-top10 GROSS Sharpe edge {g_edge:+.3f} ; NET {n_edge:+.3f} ; costs eat {g_edge-n_edge:+.3f}")
gc_edge = cagr(gross["top5"])-cagr(gross["top10"]); nc_edge = cagr(net["top5"])-cagr(net["top10"])
print(f"  top5-top10 GROSS CAGR edge {gc_edge:+.3f} ; NET {nc_edge:+.3f} ; costs eat {gc_edge-nc_edge:+.3f}")

# ---------- (e) BLOCK BOOTSTRAP: N ridge robustness ----------
print("\n=== (e) Block bootstrap (21d blocks, 5000 draws), paired across N ===")
BL=21; NB=5000
arr = net[LEVELS].dropna().values  # (T, 5) aligned rows
T = arr.shape[0]
nblocks = int(np.ceil(T/BL))
starts_all = np.arange(0, T-BL+1)
def boot_sharpe():
    st = rng.choice(starts_all, size=nblocks, replace=True)
    idxs = (st[:,None]+np.arange(BL)[None,:]).ravel()[:T]
    b = arr[idxs]
    mu = b.mean(0); sd = b.std(0,ddof=1)
    return np.sqrt(252)*mu/sd
boots = np.array([boot_sharpe() for _ in range(NB)])  # (NB,5)
colmap = {lv:i for i,lv in enumerate(LEVELS)}
def frac(a,b): return float((boots[:,colmap[a]]>boots[:,colmap[b]]).mean())
bt = dict(
  p_top5_gt_top10=frac("top5","top10"),
  p_top5_gt_top3=frac("top5","top3"),
  p_top5_gt_top1=frac("top5","top1"),
  p_top5_gt_full=frac("top5","full"),
  p_top5_is_max=float((boots.argmax(1)==colmap["top5"]).mean()),
  p_top10_is_max=float((boots.argmax(1)==colmap["top10"]).mean()),
  p_top3_is_max=float((boots.argmax(1)==colmap["top3"]).mean()),
)
print("  argmax-over-N distribution:", {lv: round(float((boots.argmax(1)==colmap[lv]).mean()),3) for lv in LEVELS})
for k,v in bt.items(): print(f"  {k}: {v:.3f}")
# CI on Sharpe diffs
for a,b in [("top5","top10"),("top5","top3")]:
    d = boots[:,colmap[a]]-boots[:,colmap[b]]
    print(f"  Sharpe[{a}-{b}] mean {d.mean():+.3f}  95%CI [{np.percentile(d,2.5):+.3f},{np.percentile(d,97.5):+.3f}]")
json.dump(bt, open(OUT/"C_bootstrap_ridge.json","w"), indent=2)

# subperiod stability
print("\n=== (e) Subperiod stability: 2014-2018 vs 2019-2024 ===")
rows=[]
for lbl,(a,b) in [("2014-2018",("2014-01-01","2018-12-31")),("2019-2024",("2019-01-01","2024-12-31"))]:
    seg = net[(net.index>=a)&(net.index<=b)]
    d=dict(period=lbl)
    for lv in LEVELS: d[lv]=round(sharpe(seg[lv]),3)
    d["argmax"]=max(LEVELS, key=lambda lv: sharpe(seg[lv]))
    rows.append(d)
sp=pd.DataFrame(rows); sp.to_csv(OUT/"C_subperiod.csv",index=False)
print(sp.to_string(index=False))
print("\nDONE core")
