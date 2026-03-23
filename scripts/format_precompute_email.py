"""
Reads today's precompute bundle and prints a formatted plain-text
trade plan summary to stdout. Called by cron_precompute.sh to build
the email body.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

def main() -> None:
    trade_date = os.environ.get("REPORT_DATE") or __import__("datetime").date.today().isoformat()
    payload_path = Path(f"outputs/precompute/{trade_date}/planned_execution_payload.json")

    if not payload_path.exists():
        print(f"[ERROR] Precompute payload not found: {payload_path}")
        sys.exit(1)

    p = json.loads(payload_path.read_text())

    sells = [t for t in p.get("trades", []) if t["side"] == "SELL"]
    buys  = [t for t in p.get("trades", []) if t["side"] == "BUY"]

    regime     = (p.get("market_analyzer") or {}).get("regime", "UNKNOWN")
    vix        = (p.get("market_analyzer") or {}).get("vix", "?")
    signal     = (p.get("market_analyzer") or {}).get("signal_bucket", "?")
    equity     = p.get("equity", 0)
    cash_pct   = p.get("achieved_cash_weight", 0) * 100
    gross_exp  = float((p.get("risk_summary") or {}).get("Gross exposure (%)", "0%").strip("%"))
    positions  = (p.get("risk_summary") or {}).get("# positions", "?")
    t_cap      = (p.get("risk_meta") or {}).get("turnover_cap", 0)
    t_req      = (p.get("risk_meta") or {}).get("turnover_requested", 0)
    t_scaled   = (p.get("risk_meta") or {}).get("turnover_scaled", False)
    pricing_dt = p.get("pricing_asof", "?")

    lines = []
    lines.append(f"Alpha Stack — Trade Plan for {trade_date}")
    lines.append(f"Prices from prior close ({pricing_dt}). No orders sent yet.")
    lines.append("")

    lines.append("PORTFOLIO STATE")
    lines.append(f"  Equity:        ${equity:,.2f}")
    lines.append(f"  Cash:          {cash_pct:.1f}%")
    lines.append(f"  Gross exp:     {gross_exp:.1f}%")
    lines.append(f"  Positions:     {positions}")
    lines.append("")

    lines.append("REGIME")
    lines.append(f"  State:         {regime}")
    lines.append(f"  Signal bucket: {signal}")
    lines.append(f"  VIX:           {vix}")
    lines.append("")

    if sells:
        lines.append(f"SELLS  ({len(sells)} orders)")
        for t in sorted(sells, key=lambda x: -x["notional"]):
            reason = t.get("reason", "")
            lines.append(f"  SELL  {t['ticker']:<6}  {t['shares']:>4} sh  ~${t['notional']:>8,.2f}  [{reason}]")
        lines.append("")

    if buys:
        lines.append(f"BUYS   ({len(buys)} orders)")
        for t in sorted(buys, key=lambda x: -x["notional"]):
            sl  = f"SL ${t['stop_loss']:,.2f}"  if t.get("stop_loss")  else "no SL"
            tp  = f"TP ${t['take_profit']:,.2f}" if t.get("take_profit") else "no TP"
            lines.append(f"  BUY   {t['ticker']:<6}  {t['shares']:>4} sh  ~${t['notional']:>8,.2f}  {sl}  {tp}")
        lines.append("")

    if t_scaled:
        lines.append(f"RISK NOTE: Turnover capped at ${t_cap:,.2f} (requested ${t_req:,.2f}). Orders scaled to {p['risk_meta']['turnover_scale']:.2%}.")
    else:
        lines.append(f"Turnover: ${t_req:,.2f} (no cap applied)")

    print("\n".join(lines))

if __name__ == "__main__":
    main()