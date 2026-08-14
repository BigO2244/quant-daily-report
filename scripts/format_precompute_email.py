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

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.dynamic_daily_email import render_dynamic_email_sections
from core.precompute_bundle_validation import validate_precompute_bundle


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _number_or_zero(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _render_sealed_target(payload: dict, daily_snapshot: dict, trade_date: str) -> str:
    rows = payload.get("target_portfolio") or []
    lines = [
        f"Caerus Orion — Sealed PAPER Target for {trade_date}",
        "",
        "ONE DECISION TARGET",
        f"  Approved target hash: {payload.get('approved_target_hash') or 'MISSING'}",
        f"  Cash target:          {_number_or_zero(payload.get('cash_target_weight')):.1%}",
        "  Exact orders:         Deferred to 09:35 ET broker-state Decision",
        "  Precompute authority: Target weights only; no order submission authority",
        "",
        "TARGET PORTFOLIO",
    ]
    for row in sorted(rows, key=lambda item: (-_number_or_zero(item.get("target_weight")), str(item.get("ticker") or item.get("symbol") or ""))):
        symbol = str(row.get("ticker") or row.get("symbol") or "")
        lines.append(f"  {symbol:<6} {_number_or_zero(row.get('target_weight')):>7.2%}")
    lines.extend(
        [
            "",
            "At 09:35 ET the workflow will price this same hashed target against fresh",
            "broker holdings and cash, apply Risk constraints, and seal exact whole-share",
            "orders. The 09:35 workflow cannot re-select a strategy, snapshot, or ticker.",
        ]
    )
    dynamic_sections = render_dynamic_email_sections(Path.cwd(), trade_date)
    lines.append(dynamic_sections["text"].rstrip())
    return "\n".join(lines)


def main() -> None:
    trade_date = os.environ.get("REPORT_DATE") or __import__("datetime").date.today().isoformat()
    payload_path = Path(f"outputs/precompute/{trade_date}/planned_execution_payload.json")
    daily_snapshot_path = Path(f"outputs/precompute/{trade_date}/daily_snapshot.json")

    if not payload_path.exists():
        print(f"[ERROR] Precompute payload not found: {payload_path}")
        sys.exit(1)

    validation = validate_precompute_bundle(
        payload_path.parent,
        trade_date=trade_date,
        required_files=(payload_path.name,),
    )
    if validation["status"] != "OK":
        reasons = ", ".join(validation.get("validation_failures") or ["unknown_failure"])
        print(
            f"[ERROR] Refusing to format invalid precompute evidence: {reasons}",
            file=sys.stderr,
        )
        sys.exit(2)

    p = _load_json(payload_path)
    daily_snapshot = _load_json(daily_snapshot_path)
    if p.get("schema_version") == "caerus.paper_precompute_handoff.v1":
        print(_render_sealed_target(p, daily_snapshot, trade_date))
        return
    strategy_identity = p.get("strategy_identity") or daily_snapshot.get("strategy_identity") or {}
    live_strategy_id = strategy_identity.get("live_strategy_id") or p.get("live_strategy_id") or "growth_engine_v4"
    shadow_baseline = strategy_identity.get("shadow_baseline_strategy") or p.get("shadow_baseline_strategy") or "caerus_polaris"
    tracks_shadow = bool(strategy_identity.get("live_tracks_shadow_baseline", p.get("live_tracks_shadow_baseline", False)))
    market_analyzer = p.get("market_analyzer") or {}
    regime_summary = daily_snapshot.get("regime_summary") or {}

    sells = [t for t in p.get("trades", []) if t["side"] == "SELL"]
    buys  = [t for t in p.get("trades", []) if t["side"] == "BUY"]

    regime = (
        regime_summary.get("composite_regime")
        or market_analyzer.get("regime")
        or "UNKNOWN"
    )
    signal = market_analyzer.get("signal_bucket", "?")
    vix_value = market_analyzer.get("vix")
    vix = f"{float(vix_value):.2f}" if isinstance(vix_value, (int, float)) else "UNAVAILABLE (degraded: VIX regime skipped)"
    equity     = _number_or_zero(p.get("equity"))
    cash_pct   = _number_or_zero(p.get("achieved_cash_weight")) * 100
    gross_exp  = float((p.get("risk_summary") or {}).get("Gross exposure (%)", "0%").strip("%"))
    positions  = (p.get("risk_summary") or {}).get("# positions", "?")
    t_cap      = _number_or_zero((p.get("risk_meta") or {}).get("turnover_cap"))
    t_req      = _number_or_zero((p.get("risk_meta") or {}).get("turnover_requested"))
    t_scaled   = (p.get("risk_meta") or {}).get("turnover_scaled", False)
    t_scope    = str((p.get("risk_meta") or {}).get("turnover_cap_scope") or "").strip().lower()
    turnover_label = "Buy turnover" if t_scope == "buys_only" else "Turnover"
    pricing_dt = p.get("pricing_asof", "?")

    lines = []
    lines.append(f"Alpha Stack — Trade Plan for {trade_date}")
    lines.append(f"Prices from prior close ({pricing_dt}). No orders sent yet.")
    lines.append("Live prices at execution may change share counts or substitute tickers.")
    lines.append(f"Live strategy: {live_strategy_id}")
    lines.append(f"Shadow baseline: {shadow_baseline} (live tracks baseline: {'YES' if tracks_shadow else 'NO'})")
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
        scope_suffix = " Sells were left intact." if t_scope == "buys_only" else ""
        lines.append(
            f"RISK NOTE: {turnover_label} capped at ${t_cap:,.2f} "
            f"(requested ${t_req:,.2f}). Orders scaled to {p['risk_meta']['turnover_scale']:.2%}."
            f"{scope_suffix}"
        )
    else:
        lines.append(f"{turnover_label}: ${t_req:,.2f} (no cap applied)")

    dynamic_sections = render_dynamic_email_sections(Path.cwd(), trade_date)
    lines.append(dynamic_sections["text"].rstrip())

    print("\n".join(lines))

if __name__ == "__main__":
    main()
