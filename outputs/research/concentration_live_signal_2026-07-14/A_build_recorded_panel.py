"""Rung 1: build the recorded ground-truth panel of the live combined-allocator
conviction (target_weight) from every shipped signal snapshot.
Sources (priority): local precompute/<date>/signals.json (authoritative, has meta),
then VM-pulled precompute, then VM/local signals/<date>.json.
READ-ONLY over operational artifacts. Writes only under the research dir.
"""
import json, os, glob
import pandas as pd

RES="outputs/research/concentration_live_signal_2026-07-14"
VM=f"{RES}/_vm_pull"

def load_signals_json(path):
    j=json.load(open(path))
    if isinstance(j,list):
        return {"signals":j,"meta":{},"cash_target_weight":None,"breaker":{},"market_analyzer":{}}
    return j

# Gather candidate files per date with source priority
cands={}  # date -> (priority, path, kind)
def add(date, pri, path, kind):
    if date not in cands or pri < cands[date][0]:
        cands[date]=(pri,path,kind)

# priority 0: local precompute
for p in glob.glob("outputs/precompute/*/signals.json"):
    d=p.split("/")[-2]
    if len(d)==10: add(d,0,p,"precompute_local")
# priority 1: VM precompute
for p in glob.glob(f"{VM}/precompute/*/signals.json"):
    d=p.split("/")[-2]
    if len(d)==10: add(d,1,p,"precompute_vm")
# priority 2: VM signals/
for p in glob.glob(f"{VM}/signals/*.json"):
    d=os.path.basename(p)[:-5]; add(d,2,p,"signals_vm")
# priority 3: local signals/
for p in glob.glob("signals/*.json"):
    d=os.path.basename(p)[:-5]; add(d,3,p,"signals_local")

RISKOFF_NARROW={"2026-06-05","2026-06-10","2026-06-17"}
def classify(date,n,cash):
    if date>="2026-07-08":
        return "post_concentration"
    if date in RISKOFF_NARROW:
        return "pre_conc_riskoff_narrow"
    return "pre_concentration_broad"

rows=[]; prov=[]
for date in sorted(cands):
    pri,path,kind=cands[date]
    j=load_signals_json(path)
    sigs=j.get("signals",[])
    meta=j.get("meta",{}) or {}
    breaker=j.get("breaker",{}) or {}
    ma=j.get("market_analyzer",{}) or {}
    cash=j.get("cash_target_weight")
    nc=[s for s in sigs if str(s.get("ticker"))!="CASH"]
    n=len(nc)
    sleeves=set()
    for s in nc:
        for part in str(s.get("sleeve","")).split(","):
            if part.strip(): sleeves.add(part.strip())
    status=classify(date,n,cash)
    for s in nc:
        rows.append({
            "date":date,
            "ticker":str(s.get("ticker")).upper().strip(),
            "target_weight":s.get("target_weight"),
            "raw_score":s.get("raw_score"),
            "sleeve":s.get("sleeve"),
            "n_names_day":n,
            "n_sleeves_day":len(sleeves),
            "cash_target_weight":cash,
            "concentration_status":status,
            "source":kind,
            "asof_date":meta.get("asof_date"),
            "generated_at":meta.get("generated_at"),
            "breaker_mode":breaker.get("mode"),
            "exposure_multiplier":breaker.get("exposure_multiplier_today"),
            "vix":ma.get("vix"),
            "regime":ma.get("regime"),
        })
    prov.append({
        "date":date,"source":kind,"n_names":n,"n_sleeves":len(sleeves),
        "sleeves":",".join(sorted(sleeves)),
        "cash_target_weight":cash,"concentration_status":status,
        "has_raw_score":any("raw_score" in s for s in nc),
        "asof_date":meta.get("asof_date"),"vix":ma.get("vix"),"regime":ma.get("regime"),
    })

panel=pd.DataFrame(rows)
panel=panel.sort_values(["date","target_weight"],ascending=[True,False]).reset_index(drop=True)
panel.to_csv(f"{RES}/A_recorded_signals_panel.csv",index=False)
prov=pd.DataFrame(prov).sort_values("date").reset_index(drop=True)
prov.to_csv(f"{RES}/A_recorded_provenance.csv",index=False)

# Summary
print("TOTAL rows:",len(panel),"| dates:",panel['date'].nunique())
print("\nBy concentration_status (dates, name-rows):")
g=prov.groupby("concentration_status").agg(dates=("date","nunique")).reset_index()
print(g.to_string(index=False))
print("\nGOLD pre_concentration_broad multi-sleeve (>=2 sleeves) dates:",
      prov[(prov.concentration_status=="pre_concentration_broad")&(prov.n_sleeves>=2)].shape[0])
print("GOLD pre_concentration_broad 4-sleeve dates:",
      prov[(prov.concentration_status=="pre_concentration_broad")&(prov.n_sleeves>=4)].shape[0])
print("\nName-count distribution (pre_conc_broad):")
pb=prov[prov.concentration_status=="pre_concentration_broad"]
print(pb["n_names"].describe().to_string())
print("\nDate range pre_conc_broad:",pb.date.min(),"->",pb.date.max())
