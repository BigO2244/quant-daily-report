"""Rung 2 + VALIDATION.
Reconstruction proxy = momentum_score (the committed Polaris momentum signal used by
the prior study), the only conviction component honestly reconstructable PIT here
(value/quality need PIT fundamentals; price sleeves need OHLCV not in the close-only PIT
panel; regime-strength blending needs frozen broker-drift/IC-throttle). We therefore
DO NOT claim this is the full combined conviction. We MEASURE its fidelity to the
recorded combined conviction on every overlap date, so Workstream B knows whether the
momentum-only history is a valid stand-in.
"""
import pandas as pd, numpy as np
from scipy.stats import spearmanr

RES="outputs/research/concentration_live_signal_2026-07-14"
sig=pd.read_parquet(f"outputs/research/concentration_thesis_2026-07-14/data/signals_largecap_pit.parquet",
                    columns=["date","ticker","momentum_score","signal_ready","pit_eligible"])
sig["date"]=pd.to_datetime(sig["date"]).dt.strftime("%Y-%m-%d")
sig["ticker"]=sig["ticker"].str.upper().str.strip()

# ---- Reconstructed panel: momentum-only conviction over PIT-eligible signal-ready universe ----
recon=sig[(sig.signal_ready==True)&(sig.pit_eligible==True)].copy()
recon=recon.rename(columns={"momentum_score":"conviction_momentum_only"})
recon["sleeve_trend"]=recon["conviction_momentum_only"]  # only component present
recon=recon[["date","ticker","conviction_momentum_only","sleeve_trend"]]
recon.to_parquet(f"{RES}/A_reconstructed_conviction_panel.parquet",index=False)

# ---- Validation against recorded truth ----
rec=pd.read_csv(f"{RES}/A_recorded_signals_panel.csv")
rec=rec[rec.concentration_status.isin(["pre_concentration_broad","pre_conc_riskoff_narrow"])].copy()
rec["ticker"]=rec["ticker"].str.upper().str.strip()

sig_by=dict(tuple(sig.groupby("date")))
rows=[]
for d,g in rec.groupby("date"):
    if d not in sig_by: continue
    sd=sig_by[d].set_index("ticker")
    g=g[g.target_weight>0].copy()
    # momentum for recorded names
    g["mom"]=g.ticker.map(sd["momentum_score"])
    common=g.dropna(subset=["mom"])
    n_book=len(g); n_common=len(common)
    if n_common<3:
        rows.append(dict(date=d,n_book=n_book,n_common=n_common,spearman=np.nan)); continue
    rho,_=spearmanr(common["target_weight"],common["mom"])
    # selection overlap: recorded top-k vs momentum top-k from FULL signal_ready universe
    univ=sig_by[d]
    univ=univ[univ.signal_ready==True].sort_values("momentum_score",ascending=False)
    def topk(k):
        rec_top=set(g.sort_values("target_weight",ascending=False).head(k).ticker)
        mom_top=set(univ.head(k).ticker)
        return len(rec_top&mom_top)
    # within-book: does momentum pick the same top-5 AS RANKED WITHIN the recorded book
    def topk_within(k):
        rec_top=set(g.sort_values("target_weight",ascending=False).head(k).ticker)
        mom_top=set(common.sort_values("mom",ascending=False).head(k).ticker)
        return len(rec_top&mom_top)
    # weight MAE on common names: renorm recorded vs momentum-share (shift positive)
    w_rec=common["target_weight"]/common["target_weight"].sum()
    m=common["mom"]-common["mom"].min()+1e-9
    w_mom=m/m.sum()
    mae=float(np.mean(np.abs(w_rec.values-w_mom.values)))
    rows.append(dict(date=d,n_book=n_book,n_common=n_common,
                     spearman=rho,
                     top5_overlap_fulluniv=topk(5),top10_overlap_fulluniv=topk(10),
                     top5_overlap_within=topk_within(5),
                     weight_mae=mae))
val=pd.DataFrame(rows).sort_values("date")
val.to_csv(f"{RES}/A_validation_recon_vs_recorded.csv",index=False)

vv=val.dropna(subset=["spearman"])
print(f"Reconstructed panel: {recon.date.nunique()} dates, {len(recon)} rows, {recon.date.min()}->{recon.date.max()}")
print(f"\nVALIDATION over {len(vv)} overlap dates ({vv.date.min()}->{vv.date.max()}):")
print(f"  Spearman(recorded target_weight vs momentum_score) within recorded book:")
print(f"    mean={vv.spearman.mean():.3f}  median={vv.spearman.median():.3f}  "
      f"std={vv.spearman.std():.3f}  min={vv.spearman.min():.3f}  max={vv.spearman.max():.3f}")
print(f"    frac dates >=0.8: {(vv.spearman>=0.8).mean():.2f}   frac >=0.5: {(vv.spearman>=0.5).mean():.2f}   frac<=0: {(vv.spearman<=0).mean():.2f}")
print(f"  Selection overlap vs FULL momentum universe (mean of |intersection|):")
print(f"    top5: {vv.top5_overlap_fulluniv.mean():.2f}/5   top10: {vv.top10_overlap_fulluniv.mean():.2f}/10")
print(f"  Within-book top5 (momentum re-ranking the recorded names): {vv.top5_overlap_within.mean():.2f}/5")
print(f"  Weight MAE (recorded vs momentum-share, common names): mean={vv.weight_mae.mean():.4f}")
print("\nPer-date head:")
print(vv[["date","n_book","n_common","spearman","top5_overlap_fulluniv","top10_overlap_fulluniv"]].head(12).to_string(index=False))
