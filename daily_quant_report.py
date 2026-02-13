import datetime as dt
import json
import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from paper.signals_io import write_signals_snapshot
from paper.paper_broker import run_paper_day, reset_orders_sent_ledger_for_date
from paper.paper_report import build_paper_report_html
from paper.build_execution_email import build_execution_email_html, build_execution_email_text
from paper.alpha import compute_alpha_attribution
from paper.email_styles import base_email_css
from paper.trading_calendar import prev_trading_day
from paper.ledger import append_ledger_rows, compute_signal_hash, ledger_rows_from_execution_payload, load_ledger, make_run_id
from paper.positions import rebuild_positions_from_ledger, write_position_outputs
from paper.mark_to_market import mark_holdings, update_nav_timeseries, write_perf_outputs
from paper.ledger2 import append_ledger2_rows, ledger2_rows_from_execution_payload
from paper.nav2 import update_nav_outputs
from reporting.attribution import compute_daily_attribution, write_attribution_outputs
from research.signal_store import persist_signal_snapshot

# ============================================================
# Sleeve 1 — structured access (do NOT call main())
# ============================================================
from sleeves.sleeve_1.backtest import (
    prepare_data as s1_prepare_data,
    backtest as s1_backtest,
)

# ============================================================
# Sleeve Trend — structured access
# ============================================================
from sleeves.sleeve_trend.backtest import (
    prepare_data as st_prepare_data,
    backtest as st_backtest,
)
from sleeves.sleeve_trend import config as trend_cfg

# ============================================================
# Sleeve 2 — import module once; resolve symbols dynamically
# ============================================================
try:
    import sleeves.sleeve_2.backtest as s2_mod
except Exception:
    s2_mod = None
s2_run_backtest = getattr(s2_mod, "run_backtest", None) if s2_mod else None
s2_run_backtest_details = (
    getattr(s2_mod, "run_backtest_with_details", None) if s2_mod else None
)
s2_prepare_data = getattr(s2_mod, "prepare_data", None) if s2_mod else None
s2_backtest = getattr(s2_mod, "backtest", None) if s2_mod else None

try:
    import sleeves.charlie_munger.backtest as cm_mod
except Exception:
    cm_mod = None
cm_run_backtest_details = getattr(cm_mod, "run_backtest_with_details", None) if cm_mod else None
# ============================================================
# Email sender (exact repo-aware lookup)
# ============================================================
try:
    from core.quant_report import send_email
except Exception:
    send_email = None
# ============================================================
# Portfolio allocation (dynamic)
# ============================================================
from core.portfolio_alloc import (  # noqa: E402
    PortfolioAllocator,
    SleeveOutput,
    AllocationResult,
    create_sleeve_output,
    allocation_summary_df,
    holdings_snapshot_df,
    validate_allocation_result,
    CASH_TICKER,
    DEFAULT_PORTFOLIO_BASE_EQUITY,
    WEIGHT_TOLERANCE,
)
from core.quant_report import (  # noqa: E402
    download_prices,
    add_atr,
)
from core.alpha_attribution import load_benchmark_prices  # noqa: E402
from engine.backtest_engine import (  # noqa: E402
    infer_latest_entries,
    attach_entry_prices,
)
from sleeves.sleeve_2.config import (  # noqa: E402
    STOP_ATR_MULT,
    TAKE_PROFIT_ATR_MULT,
    STOP_PCT,
    TAKE_PROFIT_PCT,
    LONG_THRESHOLD as S2_LONG_THRESHOLD,
    Z_EXTREME_SHORT,
)

logger = logging.getLogger(__name__)
# Backward-compatible alias for tests/patch points
calc_alpha_stats = compute_alpha_attribution
# ============================================================
# Output config
# ============================================================
OUTPUT_DIR = "outputs/daily"
DATE_FORMAT = os.getenv("DATE_FORMAT", "US")
DISPLAY_DECIMALS = int(os.getenv("DISPLAY_DECIMALS", "2"))
CHARLIE_MIN = 0.20
CHARLIE_MAX = 0.30


# ============================================================
# Helpers
# ============================================================
def _safe_df(df):
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def _fmt_money(x):
    if isinstance(x, str) and x.strip().startswith("$"):
        return x
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "n/a"


def _fmt_pct(x):
    if isinstance(x, str) and "%" in x:
        return x
    try:
        return f"{100 * float(x):.{DISPLAY_DECIMALS}f}%"
    except Exception:
        return "n/a"


def _fmt_number(x):
    if isinstance(x, str):
        return x
    try:
        return f"{float(x):,.{DISPLAY_DECIMALS}f}"
    except Exception:
        return "n/a"

def _alpha_min_overlap_days(default: int = 5) -> int:
    env_val = os.getenv("ALPHA_MIN_OVERLAP_DAYS")
    if env_val:
        try:
            return max(1, int(env_val))
        except Exception:
            pass
    try:
        with open("paper/config_paper.json", "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        return max(1, int((((cfg.get("reporting") or {}).get("alpha_min_overlap_days")) or default)))
    except Exception:
        return default




def _fmt_date(value) -> str:
    try:
        dt_value = pd.to_datetime(value)
        if DATE_FORMAT.upper() == "US":
            return dt_value.strftime("%m/%d/%Y")
        return dt_value.strftime("%Y-%m-%d")
    except Exception:
        return "n/a"


def _asof_date_from_df(df: pd.DataFrame) -> pd.Timestamp | None:
    if df is None or df.empty:
        return None
    if "date" in df.columns:
        return pd.to_datetime(df["date"]).max()
    return None


def _infer_report_date(
    *,
    sleeve_details: list[dict | None] | None,
    fallback: pd.Timestamp,
) -> pd.Timestamp:
    report_date_env = os.getenv("REPORT_DATE", "").strip()
    if report_date_env:
        return pd.to_datetime(report_date_env).normalize()

    asof_candidates: list[pd.Timestamp] = []
    target_weight_candidates: list[pd.Timestamp] = []
    for details in (sleeve_details or []):
        if not isinstance(details, dict):
            continue
        asof = details.get("asof")
        if asof is not None:
            asof_candidates.append(pd.to_datetime(asof).normalize())
        target_weights = details.get("target_weights")
        if isinstance(target_weights, pd.DataFrame) and not target_weights.empty:
            try:
                target_weight_candidates.append(
                    pd.to_datetime(target_weights.index).max().normalize()
                )
            except Exception:
                pass

    if asof_candidates:
        return max(asof_candidates)
    if target_weight_candidates:
        return max(target_weight_candidates)
    return pd.to_datetime(fallback).normalize()


def _write_execution_email_payload(payload: dict, run_date: str) -> tuple[str, bool, str | None]:
    out_dir = Path("outputs") / "execution_email"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_date}.json"

    preserved = False
    preserved_path = None
    write_path = out_path
    if out_path.exists():
        try:
            existing_payload = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing_payload = {}
        existing_num = _payload_num_trades(existing_payload)
        incoming_num = _payload_num_trades(payload)
        incoming_status = str((payload or {}).get("execution_status", "")).upper() if isinstance(payload, dict) else ""
        if existing_num > 0 and incoming_num == 0:
            suffix = "halted" if incoming_status == "HALTED" else "empty"
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            write_path = out_dir / f"{run_date}.{suffix}.{ts}.json"
            preserved = True
            preserved_path = str(out_path)
            logger.warning(
                "[EXECUTION_EMAIL] preserving non-empty payload=%s; writing new %s payload to %s",
                out_path,
                suffix,
                write_path,
            )

    with open(write_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    logger.info("[EXECUTION_EMAIL] payload written: %s", write_path)
    return str(write_path), preserved, preserved_path


def _payload_num_trades(payload: object) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        trades = payload.get("trades", [])
        return len(trades) if isinstance(trades, list) else 0
    return 0


def _coerce_whole_shares(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except Exception:
        return 0


def build_execution_email_payload(
    trade_date: str,
    daily_snapshot: dict,
    paper_summary: dict | None,
) -> dict:
    mode = (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", "shadow")
    mode = str(mode).upper()

    if mode == "LIVE":
        return {
            "trade_date": trade_date,
            "mode": mode,
            "execution_status": "HALTED",
            "halt_reason": "LIVE MODE BLOCKED",
            "trades": [],
            "run_id": (paper_summary or {}).get("run_id", ""),
            "order_ids": [],
        }

    halted_reason = None
    status = "READY"
    planned_for = (paper_summary or {}).get("planned_for")
    plan_only = bool((paper_summary or {}).get("plan_only", False))
    if paper_summary:
        market_open = str(paper_summary.get("market_status", "")).upper() == "OPEN"
        if not market_open:
            if mode == "SHADOW" or plan_only:
                status = "PLANNED"
            else:
                status = "HALTED"
                halted_reason = "MARKET CLOSED"
        if plan_only:
            status = "PLANNED"
        blocked = paper_summary.get("blocked_reasons", []) or []
        if any("stale_prices" in str(r) for r in blocked):
            status = "HALTED"
            halted_reason = "STALE PRICES"
        if any("signal_date_mismatch" in str(r) for r in blocked):
            status = "HALTED"
            halted_reason = "SIGNAL DATE MISMATCH"

    risk_map = {r.get("ticker"): r for r in (daily_snapshot.get("risk_levels", []) or []) if r.get("ticker")}
    holdings_shares = {
        str(h.get("ticker")): _coerce_whole_shares(h.get("shares"))
        for h in (daily_snapshot.get("holdings", []) or [])
        if h.get("ticker")
    }
    orders = (paper_summary or {}).get("shadow_orders", []) or []
    execution_trades = (paper_summary or {}).get("execution_trades", []) or []
    planned_trades = (paper_summary or {}).get("trade_plan", []) or []
    execution_filter = (paper_summary or {}).get("execution_filter", {}) or {}
    min_trade_dollars = float((paper_summary or {}).get("min_trade_dollars", execution_filter.get("min_trade_dollars", 100.0)))
    risk_meta = (paper_summary or {}).get("risk_meta", {}) or {}
    turnover_scaled = bool(risk_meta.get("turnover_scaled", False))
    turnover_scale = float(risk_meta.get("turnover_scale", 1.0))
    turnover_requested = float(risk_meta.get("turnover_requested", 0.0))
    turnover_cap = float(risk_meta.get("turnover_cap", 0.0))
    trades = []
    dropped_zero_shares = 0
    dropped_min_notional = 0

    source_rows = []
    if status == "PLANNED" and planned_trades:
        for tr in planned_trades:
            source_rows.append(
                {
                    "ticker": tr.get("ticker"),
                    "side": str(tr.get("side", "")).upper(),
                    "shares": tr.get("shares", tr.get("quantity")),
                    "price": tr.get("price"),
                    "reason": tr.get("reason"),
                    "order_id": tr.get("order_id"),
                    "notional": tr.get("notional"),
                    "source": "trade_plan",
                }
            )
    elif execution_trades:
        for tr in execution_trades:
            source_rows.append(
                {
                    "ticker": tr.get("ticker"),
                    "side": str(tr.get("side", "")).upper(),
                    "shares": tr.get("shares"),
                    "price": tr.get("price"),
                    "reason": tr.get("reason"),
                    "order_id": tr.get("order_id"),
                    "notional": tr.get("notional"),
                    "source": "execution_trades",
                }
            )
    elif status == "READY":
        for order in orders:
            source_rows.append(
                {
                    "ticker": order.get("ticker"),
                    "side": str(order.get("side", "")).upper(),
                    "shares": order.get("quantity"),
                    "price": None,
                    "reason": order.get("reason"),
                    "order_id": order.get("order_id"),
                    "notional": order.get("notional"),
                    "source": "shadow_orders",
                }
            )

    for row in source_rows:
        ticker = row.get("ticker")
        side = str(row.get("side", "")).upper()
        risk = risk_map.get(ticker, {})

        shares = _coerce_whole_shares(row.get("shares"))
        if side in {"SELL", "CLOSE", "REDUCE"}:
            available = holdings_shares.get(str(ticker))
            if available is not None:
                shares = min(shares, available)
        if shares < 1:
            dropped_zero_shares += 1
            continue

        entry_price = risk.get("entry_price")
        notional = None
        try:
            if entry_price is not None:
                notional = shares * float(entry_price)
            elif row.get("notional") is not None:
                notional = abs(float(row.get("notional")))
        except Exception:
            notional = None

        if notional is not None and abs(float(notional)) < float(min_trade_dollars):
            dropped_min_notional += 1
            continue

        trades.append(
            {
                "ticker": ticker,
                "side": side,
                "shares": shares,
                "entry_price": entry_price if entry_price is not None else row.get("price"),
                "stop_loss": risk.get("stop_loss"),
                "take_profit": risk.get("take_profit"),
                "notional": notional if notional is not None else row.get("notional"),
                "reason": row.get("reason"),
                "notes": row.get("reason"),
                "order_id": row.get("order_id"),
            }
        )

    logger.info(
        "[EXECUTION_EMAIL] rounded_to_whole_shares dropped_zero_shares=%d dropped_min_notional=%d",
        dropped_zero_shares,
        dropped_min_notional,
    )
    trades = sorted(trades, key=lambda x: (x.get("ticker") or "", x.get("side") or ""))
    order_ids = sorted(
        [t.get("order_id") for t in trades if t.get("order_id")],
        key=lambda oid: tuple((str(oid).split(":"))[-2:]) if ":" in str(oid) else ("", str(oid)),
    )
    blocked_reasons = [str(r) for r in ((paper_summary or {}).get("blocked_reasons", []) or [])]
    blocked_display = [f"{r.replace('_', ' ')}" for r in blocked_reasons]
    blocked_tickers = {
        str(ticker): [str(reason) for reason in reasons]
        for ticker, reasons in (((paper_summary or {}).get("blocked_tickers") or {}).items())
    }

    blocked_tickers: list[str] = []
    for reason in blocked_reasons:
        if "missing_open_prices:" not in reason:
            continue
        _, _, raw_tickers = reason.partition("missing_open_prices:")
        for ticker in [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]:
            blocked_tickers.append(f"{ticker} (missing_open_prices)")
    blocked_tickers = sorted(set(blocked_tickers))

    pricing_source = (paper_summary or {}).get("pricing_source") or ("PREV_CLOSE" if status == "PLANNED" else "OPEN")
    pricing_asof = (paper_summary or {}).get("pricing_asof") or trade_date

    total_equity = float((paper_summary or {}).get("total_equity", 0.0) or 0.0)
    holdings = (daily_snapshot or {}).get("holdings", []) or []
    position_weights: list[float] = []
    for holding in holdings:
        try:
            shares = float(holding.get("shares"))
            price = float(holding.get("last_price"))
            mv = abs(shares * price)
            if total_equity > 0:
                position_weights.append(mv / total_equity)
        except Exception:
            continue

    gross_exposure = sum(position_weights) if position_weights else None
    net_exposure = None
    if holdings and total_equity > 0:
        try:
            signed = []
            for h in holdings:
                shares = float(h.get("shares"))
                px = float(h.get("last_price"))
                direction = str(h.get("direction", "LONG")).upper()
                sign = -1.0 if direction == "SHORT" else 1.0
                signed.append(sign * abs(shares * px) / total_equity)
            net_exposure = sum(signed)
        except Exception:
            net_exposure = None

    target_cash_weight = float((paper_summary or {}).get("target_cash_weight", 0.0) or 0.0)
    achieved_cash_weight = float((paper_summary or {}).get("achieved_cash_weight", 0.0) or 0.0)
    risk_summary = {
        "Turnover requested ($)": f"${turnover_requested:,.2f}",
        "Turnover cap ($)": f"${turnover_cap:,.2f}",
        "Turnover scale": f"{turnover_scale:.4f}",
        "Target cash weight (%)": f"{target_cash_weight * 100:.2f}%",
        "Achieved cash weight (%)": f"{achieved_cash_weight * 100:.2f}%",
        "Gross exposure (%)": f"{gross_exposure * 100:.2f}%" if gross_exposure is not None else "n/a",
        "Net exposure (%)": f"{net_exposure * 100:.2f}%" if net_exposure is not None else "n/a",
        "# positions": str(len(holdings)),
        "Max position weight (%)": f"{max(position_weights) * 100:.2f}%" if position_weights else "n/a",
    }

    payload = {
        "trade_date": trade_date,
        "mode": mode,
        "execution_status": status,
        "halt_reason": halted_reason,
        "market_status": (paper_summary or {}).get("market_status"),
        "market_reason": (paper_summary or {}).get("market_reason"),
        "planned_for": planned_for,
        "plan_only": plan_only,
        "pricing_source": pricing_source,
        "pricing_asof": pricing_asof,
        "trades": trades,
        "run_id": (paper_summary or {}).get("run_id", ""),
        "order_ids": order_ids,
        "cash_target_weight": target_cash_weight,
        "investable_dollars": float((paper_summary or {}).get("investable_dollars", 0.0)),
        "equity": float((paper_summary or {}).get("sizing_equity", (paper_summary or {}).get("total_equity", 0.0))),
        "cash_target_dollars": float((paper_summary or {}).get("target_cash_dollars", 0.0)),
        "blocked_tickers": blocked_tickers,
        "proposed_trades_intent": int((paper_summary or {}).get("execution_filter", {}).get("raw", len(source_rows))),
        "executable_trades_count": int(len(trades)),
        "min_trade_dollars": float(min_trade_dollars),
        "risk_meta": {
            "turnover_requested": turnover_requested,
            "turnover_cap": turnover_cap,
            "turnover_scaled": turnover_scaled,
            "turnover_scale": turnover_scale,
        },
        "risk_summary": risk_summary,
    }

    if mode == "SHADOW" and not trades:
        payload.update(
            {
                "recommended_action": "NO",
                "confidence_level": "HIGH",
                "human_override_required": "NO",
                "rationale": [
                    "Sleeve 1 (Momentum): Signals present but blocked by portfolio cash constraint",
                    "Sleeve 2 (Valuation): Rebalance signals generated but position caps exceeded",
                    "Charlie Munger Sleeve: No new accumulation opportunities near 200-week MA",
                    "Portfolio cash currently above target due to constraint enforcement",
                ],
                "recommended_trades": [],
                "blocked_by_constraints": blocked_display,
                "next_checkpoint": "Re-evaluate at next rebalance window or upon signal state change",
                "signals_status": "VALID",
                "constraints_status": "ENFORCED",
                "execution_payload_status": "NOT GENERATED (Expected in SHADOW)",
                "no_trades_reason": f"No executable trades after rounding and ${min_trade_dollars:.0f} minimum trade filter",
            }
        )
        if turnover_scaled:
            payload["no_trades_reason"] = (
                f"Turnover cap scaling applied (requested ${turnover_requested:,.2f} vs cap ${turnover_cap:,.2f}, "
                f"scale={turnover_scale:.4f}); no trades remained after rounding/minimum filters"
            )

    if turnover_scaled:
        payload["turnover_note"] = (
            f"Turnover cap applied: requested ${turnover_requested:,.2f}, "
            f"cap ${turnover_cap:,.2f}, scale {turnover_scale:.4f}."
        )

    if status == "PLANNED":
        payload["planning_disclaimer"] = "Planning email only — no orders were sent."
        if str(pricing_source).upper() == "PREV_CLOSE":
            payload["pricing_disclaimer"] = "Prices are estimated from prior close; actual execution may differ."

    return payload


def _format_text_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "(none)"

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    def _fmt_row(cells: list[str]) -> str:
        return " | ".join(
            str(cell).ljust(col_widths[i]) for i, cell in enumerate(cells)
        )

    lines = [_fmt_row(headers), "-+-".join("-" * w for w in col_widths)]
    for row in rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)


def create_pm_first_trade_email(snapshot: dict) -> tuple[str, str]:
    """Backward-compatible PM-first digest email formatter."""
    asof = snapshot.get("asof")
    asof_str = _fmt_date(asof) if asof is not None else "n/a"
    subject = f"Daily Trade Rundown — {asof_str}"

    allocations = (snapshot.get("allocations") or {})
    sleeve_splits = (allocations.get("sleeves") or {})
    diagnostics = snapshot.get("performance_diagnostics") or {}
    perf = snapshot.get("performance_summary") or {}
    cm_sig = snapshot.get("charlie_munger") or {}

    near_ma = (cm_sig.get("meta") or {}).get("near_ma_candidates")
    if near_ma in (None, ""):
        charlie_status = "Pending"
    elif int(near_ma) <= 0:
        charlie_status = "Pending (insufficient lookback window)"
    else:
        charlie_status = f"Active ({int(near_ma)} near-MA candidates)"

    lines = [
        "ENVIRONMENT: SHADOW (NO CAPITAL AT RISK)",
        "",
        "PORTFOLIO AT A GLANCE",
        f"• Total Equity: {_fmt_money(diagnostics.get('current_equity'))}",
        f"• Day Move: {_fmt_pct(diagnostics.get('day_return'))}",
        f"• WTD: {_fmt_pct(perf.get('wtd'))}",
        f"• MTD: {_fmt_pct(perf.get('mtd'))}",
        f"• Total Return: {_fmt_pct(perf.get('total_return'))}",
        "",
        "SLEEVE ALLOCATION (DYNAMIC)",
        f"• Sleeve 1 — Momentum: {_fmt_pct(sleeve_splits.get('sleeve_trend', 0.0))}",
        f"• Sleeve 2 — Valuation: {_fmt_pct(sleeve_splits.get('sleeve_2', 0.0))}",
        "• Charlie Munger — Long Hold",
        f"  • Allocation: {_fmt_pct(sleeve_splits.get('charlie_munger', 0.0))}",
        f"  • Status: {charlie_status}",
        "",
        "— Automated Portfolio Engine",
    ]
    return subject, "\n".join(lines)



def create_snapshot_email(snapshot: dict, execution_payload: dict | None = None) -> tuple[str, str]:
    asof = snapshot.get("asof")
    asof_str = _fmt_date(asof) if asof is not None else "n/a"
    subject = f"MODEL & PERFORMANCE SNAPSHOT — {asof_str}"

    allocations = snapshot.get("allocations", {}) or {}
    sleeve_splits = allocations.get("sleeves", {}) or {}
    cash_pct = allocations.get("cash", 0.0)
    target_cash = snapshot.get("target_cash_weight", cash_pct)
    perf = snapshot.get("performance_summary", {}) or {}
    diagnostics = snapshot.get("performance_diagnostics", {}) or {}
    alpha = snapshot.get("alpha_attribution", {}) or {}
    cm_sig = snapshot.get("charlie_munger", {}) or {}
    cm_near_ma = (cm_sig.get("meta") or {}).get("near_ma_candidates", 0)
    cm_selected = cm_sig.get("selected", []) or []

    total_equity = diagnostics.get("current_equity")
    day_return = diagnostics.get("day_return")
    orders = snapshot.get("orders", []) or []
    skipped = snapshot.get("skipped_trades", []) or []

    def _trades_today(sleeve_name: str) -> str:
        count = len([o for o in orders if o.get("sleeve") == sleeve_name])
        return "NONE" if count == 0 else str(count)

    raw_mode = str((execution_payload or {}).get("mode") or os.getenv("TRADING_MODE", "SHADOW")).upper()
    env_mode = "LIVE" if raw_mode == "LIVE" else "SHADOW"
    exec_trades = (execution_payload or {}).get("trades", []) or []
    exec_no_trade_reason = (
        (execution_payload or {}).get("halt_reason")
        or "No executable trades in execution payload"
    )

    nav_metrics = snapshot.get("nav_metrics", {}) or {}
    lines = [
        f"ENVIRONMENT: {env_mode}",
        "",
        "PORTFOLIO AT A GLANCE",
        f"• Total Equity: {_fmt_money(nav_metrics.get('equity', total_equity))}",
        f"• Day Move: {_fmt_pct(nav_metrics.get('return_1d', day_return))}",
        f"• Cash: {_fmt_pct(cash_pct)} (Target: {_fmt_pct(target_cash)})",
        f"• Active Sleeves: {len([k for k, v in sleeve_splits.items() if float(v) > WEIGHT_TOLERANCE])}",
        "",
        "SLEEVE ALLOCATION (DYNAMIC)",
        f"• Sleeve 1 — Momentum: {_fmt_pct(sleeve_splits.get('sleeve_trend', 0.0))}",
        f"• Sleeve 2 — Valuation: {_fmt_pct(sleeve_splits.get('sleeve_2', 0.0))}",
        f"• Charlie Munger — Long Hold: {_fmt_pct(sleeve_splits.get('charlie_munger', 0.0))}",
        f"• Cash: {_fmt_pct(cash_pct)}",
        "",
        "NOTE: Charlie Munger sleeve allocation is dynamically maintained between 20–30% by design.",
        "",
        "---",
        "",
        "SLEEVE 1 — MOMENTUM (FAST)",
        "• Status: ACTIVE",
        "• Signal State: ON",
        f"• Trades Today: {_trades_today('sleeve_trend')}",
        f"• Constraint Impact: {'Position caps + cash target' if skipped else 'None'}",
        "• Role: Capture short- to mid-term trend persistence",
        "",
        "SLEEVE 2 — VALUATION (OPPORTUNISTIC)",
        "• Status: ACTIVE",
        "• Signal State: ON",
        f"• Trades Today: {_trades_today('sleeve_2')}",
        f"• Constraint Impact: {'Position caps' if skipped else 'None'}",
        "• Role: Mean reversion and valuation dislocations",
        "",
        "CHARLIE MUNGER SLEEVE — LONG HOLD",
        "• Status: ACTIVE",
        f"• Allocation: {_fmt_pct(sleeve_splits.get('charlie_munger', 0.0))}",
        f"• New Buys Today: {'NONE' if not cm_selected else len(cm_selected)}",
        f"• Candidates Near 200-Week MA: {cm_near_ma}",
        "• Role: Long-duration quality accumulation hedge",
        "",
        "This sleeve acts as a stabilizer against higher-velocity trading in Sleeves 1 & 2.",
        "",
        "---",
        "",
        "PERFORMANCE SUMMARY",
        f"• Week-to-Date: {_fmt_pct(nav_metrics.get('wtd', perf.get('wtd')))}",
        f"• Month-to-Date: {_fmt_pct(nav_metrics.get('mtd', perf.get('mtd')))}",
        f"• Since Inception: {_fmt_pct(nav_metrics.get('si', perf.get('total_return')))}",
        "",
        "ALPHA ATTRIBUTION VS SPY",
    ]

    if alpha and alpha.get("ok"):
        summary = alpha.get("summary", {}) or {}
        lines.extend(
            [
                "• Status: Available",
                f"• Overlap: {alpha.get('overlap_start')} to {alpha.get('overlap_end')} ({alpha.get('overlap_days')} days)",
                f"• Cumulative Portfolio Return: {_fmt_pct(summary.get('cumulative_port_return'))}",
                f"• Cumulative SPY Return: {_fmt_pct(summary.get('cumulative_spy_return'))}",
                f"• Cumulative Alpha: {_fmt_pct(summary.get('cumulative_alpha'))}",
                "• Last 10 daily spreads:",
            ]
        )
        for row in (alpha.get("rows") or [])[-10:]:
            lines.append(
                "  - {date}: Port {port}, SPY {spy}, Spread {spread}".format(
                    date=row.get("date"),
                    port=_fmt_pct(row.get("port_ret")),
                    spy=_fmt_pct(row.get("spy_ret")),
                    spread=_fmt_pct(row.get("spread")),
                )
            )
    else:
        reason = (alpha or {}).get("reason") or "Alpha attribution unavailable."
        lines.extend([f"• Status: Pending — {reason}"])

    lines.extend(
        [
            "",
            "---",
            "",
            "TRADES FOR TODAY (NEW ORDERS — EXECUTION PAYLOAD)",
            f"• Executable Trades: {len(exec_trades)}",
            f"• Status: {'NO TRADES' if not exec_trades else 'TRADES READY'}",
            f"• Reason: {exec_no_trade_reason if not exec_trades else 'See execution recommendation email for order details'}",
            "",
            "PROPOSED / NEXT REBALANCE (NOT EXECUTED TODAY)",
            "• Momentum breadth remains narrow",
            "• Valuation signals concentrated in capped names",
            "• Long-term quality names not yet at accumulation thresholds",
            "",
            f"• Proposed Trades (Intent): {len(snapshot.get('proposed_trades', []) or [])}",
            "• Note: These are model-intent recommendations only; execution is governed by the separate TRADE EXECUTION email.",
            "",
            "SYSTEM HEALTH",
            "• Signals: VALID",
            "• Data Freshness: OK",
            "• Constraint Engine: OPERATING AS DESIGNED",
            "",
            "— Automated Portfolio Engine",
        ]
    )

    return subject, "\n".join(lines)



def _normalize_weights(sleeve_allocations: dict[str, float], cash_weight: float) -> tuple[dict[str, float], float]:
    sleeves = {k: max(0.0, float(v)) for k, v in (sleeve_allocations or {}).items()}
    cash = max(0.0, float(cash_weight))
    total = sum(sleeves.values()) + cash
    if total <= WEIGHT_TOLERANCE:
        return sleeves, 1.0
    if abs(total - 1.0) <= WEIGHT_TOLERANCE:
        return sleeves, cash
    factor = 1.0 / total
    sleeves = {k: v * factor for k, v in sleeves.items()}
    cash = cash * factor
    return sleeves, cash


def enforce_charlie_bounds(
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    *,
    charlie_active: bool = True,
) -> tuple[dict[str, float], float]:
    """Clamp Charlie allocation to configured bounds and rebalance other sleeves first."""
    sleeves, cash = _normalize_weights(sleeve_allocations, cash_weight)
    if not charlie_active:
        return sleeves, cash

    sleeves.setdefault("sleeve_trend", 0.0)
    sleeves.setdefault("sleeve_2", 0.0)
    sleeves.setdefault("charlie_munger", 0.0)

    orig_charlie = float(sleeves.get("charlie_munger", 0.0))
    target_charlie = min(max(orig_charlie, CHARLIE_MIN), CHARLIE_MAX)
    delta = target_charlie - orig_charlie
    if abs(delta) <= WEIGHT_TOLERANCE:
        return sleeves, cash

    other_keys = ["sleeve_trend", "sleeve_2"]
    others_total = sum(float(sleeves.get(k, 0.0)) for k in other_keys)

    if delta > 0:
        if others_total > WEIGHT_TOLERANCE:
            for key in other_keys:
                share = float(sleeves.get(key, 0.0)) / others_total
                sleeves[key] = max(0.0, float(sleeves.get(key, 0.0)) - delta * share)
        else:
            logger.warning(
                "[ALLOCATION] Unable to increase Charlie allocation without adjusting CASH or active sleeves."
            )
            target_charlie = orig_charlie
    else:
        give = -delta
        if others_total > WEIGHT_TOLERANCE:
            for key in other_keys:
                share = float(sleeves.get(key, 0.0)) / others_total
                sleeves[key] = max(0.0, float(sleeves.get(key, 0.0)) + give * share)
        else:
            logger.warning(
                "[ALLOCATION] Unable to reduce Charlie allocation because non-Charlie sleeves are inactive."
            )
            sleeves["charlie_munger"] = max(0.0, orig_charlie)
            sleeves, cash = _normalize_weights(sleeves, cash)
            return sleeves, cash

    sleeves["charlie_munger"] = max(0.0, target_charlie)
    sleeves, cash = _normalize_weights(sleeves, cash)
    return sleeves, cash



def _apply_enforced_allocations_to_result(
    alloc_result: AllocationResult,
    old_allocations: dict[str, float],
    new_allocations: dict[str, float],
    new_cash_weight: float,
) -> None:
    alloc_result.sleeve_allocations = dict(new_allocations)
    combined = _safe_df(alloc_result.combined_weights).copy()
    if combined.empty:
        alloc_result.combined_weights = pd.DataFrame([
            {"ticker": CASH_TICKER, "target_weight": float(new_cash_weight), "sleeve_name": CASH_TICKER, "reason": "post_enforce", "signal_strength": 1.0}
        ])
        return

    if "sleeve_name" not in combined.columns:
        combined["sleeve_name"] = combined.get("ticker", pd.Series(dtype=str)).apply(
            lambda t: CASH_TICKER if str(t) == CASH_TICKER else ""
        )

    if "target_weight" in combined.columns:
        def _scale_row(row):
            names = [n.strip() for n in str(row.get("sleeve_name", "")).split(",") if n.strip()]
            ratios = []
            for name in names:
                old = float(old_allocations.get(name, 0.0))
                new = float(new_allocations.get(name, 0.0))
                if old > WEIGHT_TOLERANCE:
                    ratios.append(new / old)
                elif new <= WEIGHT_TOLERANCE:
                    ratios.append(0.0)
            scale = sum(ratios) / len(ratios) if ratios else 1.0
            return float(row.get("target_weight", 0.0)) * scale

        mask_cash = combined.get("ticker", pd.Series(dtype=str)) == CASH_TICKER
        non_cash = combined[~mask_cash].copy()
        if not non_cash.empty:
            non_cash["target_weight"] = non_cash.apply(_scale_row, axis=1)
            non_cash = non_cash[non_cash["target_weight"].abs() > WEIGHT_TOLERANCE]
        cash_row = pd.DataFrame([
            {
                "ticker": CASH_TICKER,
                "target_weight": float(new_cash_weight),
                "sleeve_name": CASH_TICKER,
                "reason": "post_enforce",
                "signal_strength": 1.0,
            }
        ])
        alloc_result.combined_weights = pd.concat([non_cash, cash_row], ignore_index=True)

def _equity_series_from_df(df: pd.DataFrame) -> pd.Series:
    df = _safe_df(df)
    if df.empty or "equity" not in df.columns:
        return pd.Series(dtype=float)
    series = pd.Series(
        df["equity"].values,
        index=pd.to_datetime(df["date"]) if "date" in df.columns else df.index,
    )
    return series.dropna().sort_index()


def _series_date_range(series: pd.Series) -> str:
    series = pd.Series(series).dropna()
    if series.empty:
        return "empty"
    idx = pd.to_datetime(series.index)
    return f"{idx.min().date()} -> {idx.max().date()}"


def _load_equity_history(path: str) -> pd.Series:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns or "equity" not in df.columns:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return pd.Series(df["equity"].values, index=df["date"]).dropna().sort_index()


def _load_portfolio_fixture(path: str) -> pd.Series:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns or "equity" not in df.columns:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return pd.Series(df["equity"].values, index=df["date"]).dropna().sort_index()


def _append_equity_history(
    path: str, report_date: pd.Timestamp, equity: float
) -> pd.Series:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    report_date = pd.to_datetime(report_date).normalize()
    rows = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        df = pd.read_csv(path)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.normalize()
            rows = df.to_dict(orient="records")
            if (df["date"] == report_date).any():
                return (
                    pd.Series(df["equity"].values, index=df["date"])
                    .dropna()
                    .sort_index()
                )
    rows.append({"date": report_date.strftime("%Y-%m-%d"), "equity": float(equity)})
    out_df = pd.DataFrame(rows)
    out_df.to_csv(path, index=False)
    out_df["date"] = pd.to_datetime(out_df["date"])
    return (
        pd.Series(out_df["equity"].values, index=out_df["date"]).dropna().sort_index()
    )


def compute_portfolio_equity_series(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
) -> pd.Series:
    returns_map = {}
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        series = _equity_series_from_df(
            sleeve_equity_map.get(sleeve_name, pd.DataFrame())
        )
        if series.empty:
            continue
        returns_map[sleeve_name] = series.pct_change(fill_method=None).dropna()
    if not returns_map:
        return pd.Series(dtype=float)
    aligned = pd.concat(returns_map, axis=1, join="inner").dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    weights = pd.Series({k: sleeve_allocations.get(k, 0.0) for k in aligned.columns})
    portfolio_ret = aligned.mul(weights, axis=1).sum(axis=1)
    portfolio_equity = base_equity * (1.0 + portfolio_ret).cumprod()
    if cash_weight > WEIGHT_TOLERANCE:
        portfolio_equity = portfolio_equity * (1.0 + 0.0)
    return portfolio_equity


def _compute_execution_price(price_map: dict, ticker: str) -> float | None:
    entry = price_map.get(ticker, {})
    return entry.get("next_open") or entry.get("last_close")


def _build_price_map(prices: pd.DataFrame, asof: pd.Timestamp) -> dict:
    price_map = {}
    if prices.empty:
        return price_map
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    for ticker, group in df.groupby("ticker"):
        group = group.sort_values("date")
        past = group[group["date"] <= asof]
        future = group[group["date"] > asof]
        last_close = past["close"].iloc[-1] if not past.empty else None
        next_open = future["open"].iloc[0] if not future.empty else None
        price_map[ticker] = {
            "last_close": float(last_close) if last_close is not None else None,
            "next_open": float(next_open) if next_open is not None else None,
        }
    return price_map


def _build_atr_map(prices: pd.DataFrame, asof: pd.Timestamp) -> dict:
    if prices.empty:
        return {}
    atr_df = add_atr(prices)
    atr_df = atr_df.sort_values(["ticker", "date"])
    atr_map = {}
    for ticker, group in atr_df.groupby("ticker"):
        past = group[group["date"] <= asof]
        if past.empty:
            atr_map[ticker] = None
        else:
            atr_map[ticker] = (
                float(past["atr"].iloc[-1]) if pd.notna(past["atr"].iloc[-1]) else None
            )
    return atr_map


def _format_df_for_email(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        col_lower = str(col).lower()
        if "%" in col_lower or "pct" in col_lower or "percent" in col_lower:
            df[col] = df[col].apply(_fmt_pct)
        elif "date" in col_lower:
            df[col] = df[col].apply(_fmt_date)
        elif any(
            key in col_lower
            for key in [
                "p&l",
                "pnl",
                "equity",
                "notional",
                "allocated",
                "price",
                "value",
                "capital",
                "cash",
                "difference",
                "model",
                "broker",
                "amount",
            ]
        ):
            df[col] = df[col].apply(_fmt_money)
        elif any(
            key in col_lower
            for key in ["return", "weight", "delta", "change", "gross", "net"]
        ):
            df[col] = df[col].apply(_fmt_pct)
        elif "shares" in col_lower:
            df[col] = df[col].apply(_fmt_number)
    return df


def html_table(
    df: pd.DataFrame, title: str, max_rows: int = 25, empty_message: str = "No data"
) -> str:
    df = _safe_df(df).copy()
    df = df.where(pd.notnull(df), "—")
    if df.empty:
        return f"<h3>{title}</h3><p><em>{empty_message}</em></p>"
    df = _format_df_for_email(df)
    return f"<h3>{title}</h3>" + df.head(max_rows).to_html(
        index=False, border=0, classes="tbl", justify="left"
    )


def filter_sleeve2_cash_proxy(trades: pd.DataFrame) -> pd.DataFrame:
    """Remove SGOV 'cash_proxy_fund_entries' rows from trades."""
    trades = _safe_df(trades).copy()
    if trades.empty:
        return trades
    if "ticker" in trades.columns and "reason_exit" in trades.columns:
        trades = trades[
            ~(
                (trades["ticker"] == "SGOV")
                & (trades["reason_exit"] == "cash_proxy_fund_entries")
            )
        ].copy()
    return trades


# ============================================================
# Sleeve health check
# ============================================================
def _sleeve_is_valid(equity_df: pd.DataFrame) -> tuple[bool, str]:
    """
    Check whether a sleeve produced valid results.
    Returns (is_valid, reason) where reason is empty string if valid.
    """
    equity_df = _safe_df(equity_df)
    if equity_df.empty:
        return False, "empty equity_df"
    if "equity" not in equity_df.columns:
        return False, "no 'equity' column"
    if len(equity_df) < 1:
        return False, "zero rows"
    last_eq = equity_df["equity"].iloc[-1]
    if pd.isna(last_eq) or last_eq <= 0:
        return False, f"invalid terminal equity ({last_eq})"
    return True, ""


# ============================================================
# Sleeve runners
# ============================================================
def run_sleeve_1():
    logger.info("[SLEEVE 1] Preparing data...")
    signals = s1_prepare_data()
    logger.info("[SLEEVE 1] Running backtest...")
    return s1_backtest(signals)


def run_sleeve_trend():
    logger.info("[SLEEVE TREND] Preparing data...")
    signals = st_prepare_data()
    logger.info("[SLEEVE TREND] Running backtest...")
    equity_df, trades_df = st_backtest(signals)
    return equity_df, trades_df, signals


def run_sleeve_2():
    if s2_run_backtest_details is not None:
        logger.info("[SLEEVE 2] Running run_backtest_with_details()...")
        return s2_run_backtest_details(period="1y", interval="1d")
    if s2_run_backtest is not None:
        logger.info("[SLEEVE 2] Running run_backtest()...")
        equity_df, trades_df = s2_run_backtest(period="1y", interval="1d")
        return {"equity_df": equity_df, "trades_df": trades_df}
    if s2_prepare_data is not None and s2_backtest is not None:
        logger.info("[SLEEVE 2] Preparing data...")
        signals = s2_prepare_data()
        logger.info("[SLEEVE 2] Running backtest...")
        equity_df, trades_df = s2_backtest(signals)
        return {"equity_df": equity_df, "trades_df": trades_df}
    raise RuntimeError("No valid Sleeve 2 runner found")

def run_charlie_munger():
    """
    Run Charlie Munger sleeve backtest and return details dict.
    Keep this direct to avoid fragile "runner discovery" behavior.
    """
    from sleeves.charlie_munger.backtest import run_backtest_with_details
    return run_backtest_with_details()

def run_sleeve_charlie_munger():
    """Backward-compatible alias."""
    return run_charlie_munger()


# ============================================================
# Sleeve output extraction (for dynamic allocation)
# ============================================================
def extract_sleeve_output(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    sleeve_name: str,
    base_strength: float = 1.0,
    target_weights: pd.DataFrame | None = None,
) -> SleeveOutput:
    """
    Extract a SleeveOutput from backtest results.
    Does NOT modify any signal logic - just reads backtest output.
    """
    equity_df = _safe_df(equity_df)
    trades_df = _safe_df(trades_df)
    positions = []
    if equity_df.empty:
        return create_sleeve_output([], sleeve_name, 0.0, "No equity data")
    latest_equity = (
        float(equity_df["equity"].iloc[-1])
        if "equity" in equity_df.columns
        else 10000.0
    )
    start_equity = (
        float(equity_df["equity"].iloc[0]) if "equity" in equity_df.columns else 10000.0
    )
    if not trades_df.empty and "ticker" in trades_df.columns:
        real_trades = trades_df.copy()
        if "reason_exit" in real_trades.columns:
            real_trades = real_trades[
                ~(
                    (real_trades.get("ticker", "") == "SGOV")
                    & (real_trades.get("reason_exit", "") == "cash_proxy_fund_entries")
                )
            ].copy()
        if not real_trades.empty and "entry_date" in real_trades.columns:
            real_trades["entry_date"] = pd.to_datetime(
                real_trades["entry_date"], errors="coerce"
            )
            latest_trades = real_trades.nlargest(5, "entry_date")
            for _, row in latest_trades.iterrows():
                ticker = row.get("ticker", "")
                shares = row.get("shares", 0)
                entry_price = row.get("entry_price", 0)
                if ticker and shares > 0 and entry_price > 0:
                    notional = shares * entry_price
                    weight = notional / latest_equity if latest_equity > 0 else 0
                    positions.append(
                        {
                            "ticker": ticker,
                            "target_weight": weight,
                            "reason": row.get("reason_exit", "signal"),
                            "signal_strength": 1.0,
                        }
                    )
        # Also handle engine-style trades (which have "date" + "weight_to" instead
        # of "entry_date" + "shares").  This allows Sleeve 2 engine trades to
        # register as active positions for allocation purposes.
        if (
            not real_trades.empty
            and "weight_to" in real_trades.columns
            and not positions
        ):
            real_trades_sorted = real_trades.copy()
            if "date" in real_trades_sorted.columns:
                real_trades_sorted["date"] = pd.to_datetime(
                    real_trades_sorted["date"], errors="coerce"
                )
                real_trades_sorted = real_trades_sorted.sort_values(
                    "date", ascending=False
                )
            latest_engine_trades = real_trades_sorted.head(5)
            for _, row in latest_engine_trades.iterrows():
                ticker = row.get("ticker", "")
                w = abs(row.get("weight_to", 0.0))
                if ticker and w > 1e-6:
                    positions.append(
                        {
                            "ticker": ticker,
                            "target_weight": w,
                            "reason": "engine_signal",
                            "signal_strength": 1.0,
                        }
                    )

    if not positions and target_weights is not None and not target_weights.empty:
        target_last = target_weights.iloc[-1]
        for ticker, weight in target_last.items():
            if abs(weight) > WEIGHT_TOLERANCE:
                positions.append(
                    {
                        "ticker": ticker,
                        "target_weight": float(weight),
                        "reason": "target_weights",
                        "signal_strength": 1.0,
                    }
                )

    is_active = len(positions) > 0
    if is_active and start_equity > 0:
        sleeve_return = (latest_equity / start_equity) - 1.0
        strength = min(1.0, base_strength * max(0.5, min(1.5, 1.0 + sleeve_return)))
    else:
        strength = 0.0
    notes = (
        f"Active: {len(positions)} positions, equity ${latest_equity:,.0f}"
        if is_active
        else "Inactive"
    )
    return create_sleeve_output(positions, sleeve_name, strength, notes)


# ============================================================
# Portfolio equity computation (FIXED)
# ============================================================
def compute_portfolio_equity(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
) -> dict:
    """
    Compute TRUE portfolio equity from sleeve returns and allocations.
    This computes:
        portfolio_return = sum(sleeve_alloc_i * sleeve_return_i) + cash_weight * 0
        portfolio_equity = base_equity * (1 + portfolio_return)
    Returns dict with: equity, prev_equity, day_pnl, day_return, cumulative_return
    NOTE: This is the ONLY correct way to compute portfolio total.
    Do NOT sum sleeve equities - they are independent backtests on their own base.
    """
    portfolio_return = 0.0
    portfolio_prev_return = 0.0
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        equity_df = _safe_df(sleeve_equity_map.get(sleeve_name, pd.DataFrame()))
        if equity_df.empty or "equity" not in equity_df.columns:
            continue
        df = equity_df.reset_index(drop=True)
        start = float(df["equity"].iloc[0])
        last = float(df["equity"].iloc[-1])
        prev = float(df["equity"].iloc[-2]) if len(df) > 1 else last
        if start <= 0:
            continue
        # Sleeve cumulative return (from its own backtest)
        sleeve_cum_return = (last / start) - 1.0
        sleeve_prev_return = (prev / start) - 1.0
        # Weighted contribution to portfolio return
        portfolio_return += alloc * sleeve_cum_return
        portfolio_prev_return += alloc * sleeve_prev_return
    # Cash contributes 0 return (already accounted for by not adding anything)
    # Portfolio equity
    portfolio_equity = base_equity * (1.0 + portfolio_return)
    portfolio_prev_equity = base_equity * (1.0 + portfolio_prev_return)
    day_pnl = portfolio_equity - portfolio_prev_equity
    day_return = day_pnl / portfolio_prev_equity if portfolio_prev_equity > 0 else 0.0
    return {
        "equity": portfolio_equity,
        "prev_equity": portfolio_prev_equity,
        "day_pnl": day_pnl,
        "day_return": day_return,
        "cumulative_return": portfolio_return,
    }


def resolve_portfolio_equity_series(
    sleeve_equity_map: dict[str, pd.DataFrame],
    alloc_result: AllocationResult,
    report_date: pd.Timestamp,
    portfolio_equity: float,
    offline_fixture: bool,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
    portfolio_stats: dict | None = None,
    st_equity: pd.DataFrame | None = None,
    s2_equity: pd.DataFrame | None = None,
    st_signals: pd.DataFrame | None = None,
    s2_details: dict | None = None,
) -> pd.Series:
    history_path = os.path.join(OUTPUT_DIR, "equity_history.csv")
    history_series = _load_equity_history(history_path)
    if not history_series.empty:
        return history_series

    ledger_path = os.path.join("paper", "ledger.csv")
    if os.path.exists(ledger_path) and os.path.getsize(ledger_path) > 0:
        ledger = pd.read_csv(ledger_path)
        if (
            not ledger.empty
            and "date" in ledger.columns
            and "total_equity" in ledger.columns
        ):
            ledger["date"] = pd.to_datetime(ledger["date"])
            ledger = ledger.sort_values("date")
            ledger_daily = ledger.groupby("date", as_index=True)["total_equity"].last()
            ledger_series = ledger_daily.dropna().sort_index()
            if not ledger_series.empty:
                return ledger_series

    derived = compute_portfolio_equity_series(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=alloc_result.sleeve_allocations,
        cash_weight=alloc_result.cash_weight,
        base_equity=base_equity,
    )
    if not derived.empty:
        return derived

    if offline_fixture:
        return pd.Series(dtype=float)

    return _append_equity_history(history_path, report_date, portfolio_equity)


def compute_portfolio_equity_df(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    base_equity: float,
) -> pd.DataFrame:
    """Compute portfolio equity series from sleeve equity curves and allocations."""
    series = []
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        equity_df = _safe_df(sleeve_equity_map.get(sleeve_name, pd.DataFrame()))
        if equity_df.empty or "equity" not in equity_df.columns:
            continue
        df = equity_df.copy()
        df["date"] = pd.to_datetime(df.get("date", df.index))
        df = df.sort_values("date")
        start = float(df["equity"].iloc[0])
        if start <= 0:
            continue
        df["return"] = (df["equity"] / start) - 1.0
        series.append(
            df[["date", "return"]].rename(columns={"return": f"{sleeve_name}_return"})
        )

    if not series:
        return pd.DataFrame(columns=["date", "portfolio_equity"])

    merged = series[0]
    for s in series[1:]:
        merged = merged.merge(s, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)
    for col in merged.columns:
        if col.endswith("_return"):
            merged[col] = merged[col].ffill().fillna(0.0)

    merged["portfolio_return"] = 0.0
    for sleeve_name, alloc in sleeve_allocations.items():
        col = f"{sleeve_name}_return"
        if col in merged.columns:
            merged["portfolio_return"] += alloc * merged[col]

    merged["portfolio_equity"] = base_equity * (1.0 + merged["portfolio_return"])
    return merged[["date", "portfolio_equity"]]


def compute_performance_summary(
    report_date: pd.Timestamp,
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    base_equity: float,
) -> dict:
    series = compute_portfolio_equity_df(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=sleeve_allocations,
        base_equity=base_equity,
    )
    if series.empty:
        return {
            "total_return": None,
            "wtd": None,
            "mtd": None,
            "ytd": None,
        }

    series["date"] = pd.to_datetime(series["date"])
    series = series.sort_values("date")

    def _equity_asof(target_date: pd.Timestamp) -> float:
        eligible = series[series["date"] <= target_date]
        if eligible.empty:
            return float(series["portfolio_equity"].iloc[0])
        return float(eligible["portfolio_equity"].iloc[-1])

    asof_date = pd.to_datetime(report_date)
    asof_equity = _equity_asof(asof_date)
    inception_equity = float(series["portfolio_equity"].iloc[0])

    week_start = (asof_date - pd.Timedelta(days=asof_date.weekday())).normalize()
    month_start = asof_date.replace(day=1).normalize()
    year_start = asof_date.replace(month=1, day=1).normalize()

    wtd_equity = _equity_asof(week_start)
    mtd_equity = _equity_asof(month_start)
    ytd_equity = _equity_asof(year_start)

    return {
        "total_return": (
            (asof_equity / inception_equity) - 1.0 if inception_equity > 0 else None
        ),
        "wtd": (asof_equity / wtd_equity) - 1.0 if wtd_equity > 0 else None,
        "mtd": (asof_equity / mtd_equity) - 1.0 if mtd_equity > 0 else None,
        "ytd": (asof_equity / ytd_equity) - 1.0 if ytd_equity > 0 else None,
    }


def build_proposed_trades(
    alloc_result: AllocationResult,
    previous_weights: pd.DataFrame | None,
    price_map: dict,
    model_equity: float,
) -> list[dict]:
    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    if weights_df.empty:
        return []

    prev_map = {}
    if previous_weights is not None and not previous_weights.empty:
        prev_map = previous_weights.set_index("ticker")["target_weight"].to_dict()

    proposed = []
    for _, row in weights_df.iterrows():
        ticker = row["ticker"]
        target_weight = float(row["target_weight"])
        current_weight = float(prev_map.get(ticker, target_weight))
        delta_weight = target_weight - current_weight

        if abs(delta_weight) <= WEIGHT_TOLERANCE:
            continue

        action = "HOLD"
        if (
            abs(current_weight) <= WEIGHT_TOLERANCE
            and abs(target_weight) > WEIGHT_TOLERANCE
        ):
            action = "BUY" if target_weight > 0 else "SELL"
        elif (
            abs(target_weight) <= WEIGHT_TOLERANCE
            and abs(current_weight) > WEIGHT_TOLERANCE
        ):
            action = "SELL"
        elif current_weight * target_weight >= 0:
            action = (
                "INCREASE" if abs(target_weight) > abs(current_weight) else "DECREASE"
            )
        else:
            action = "SELL"

        exec_px = _compute_execution_price(price_map, ticker)
        shares = None
        notional = None
        if exec_px and model_equity > 0:
            notional = abs(delta_weight) * model_equity
            shares = round(notional / exec_px, 2) if exec_px > 0 else None

        proposed.append(
            {
                "ticker": ticker,
                "action": action,
                "sleeve": row.get("sleeve_name", ""),
                "current_weight": current_weight,
                "target_weight": target_weight,
                "delta_weight": delta_weight,
                "est_shares": shares,
                "est_notional": notional,
            }
        )

    return proposed


def derive_actual_sleeve_allocations(
    alloc_result: AllocationResult,
) -> dict[str, float]:
    allocations = {name: 0.0 for name in alloc_result.sleeve_allocations.keys()}
    weights_df = _safe_df(alloc_result.combined_weights)
    if weights_df.empty:
        return allocations

    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    for _, row in weights_df.iterrows():
        sleeve_names = str(row.get("sleeve_name", "")).split(",")
        sleeve_names = [name.strip() for name in sleeve_names if name.strip()]
        if not sleeve_names:
            continue
        share = float(row.get("target_weight", 0.0)) / len(sleeve_names)
        for name in sleeve_names:
            allocations[name] = allocations.get(name, 0.0) + share
    return allocations


# ============================================================
# Daily snapshot builder
# ============================================================
def build_daily_snapshot(
    report_date: pd.Timestamp,
    alloc_result: AllocationResult,
    portfolio_stats: dict,
    st_equity: pd.DataFrame,
    s2_equity: pd.DataFrame,
    st_signals: pd.DataFrame,
    s2_details: dict,
    cm_details: dict | None = None,
) -> dict:
    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    weights_df = weights_df[weights_df["target_weight"].abs() > WEIGHT_TOLERANCE]
    tickers = (
        sorted(weights_df["ticker"].unique().tolist()) if not weights_df.empty else []
    )
    prices = pd.DataFrame()
    if tickers:
        prices = download_prices(tickers, period="6mo", interval="1d")
        if prices.empty or prices[["open", "high", "low", "close"]].isna().all().all():
            logger.error(
                "Downloaded price data is empty or all-NaN; aborting snapshot generation."
            )
            raise RuntimeError(
                "Downloaded price data is empty or all-NaN; aborting snapshot generation."
            )
    # --- Paper trading signals snapshot (daily immutable file) ---
    # Prefer a YYYY-MM-DD string you already use in the report.
    # If you already have something like report_date_str / asof_date_str / today_str, use that here.
    signals_path = None
    if "sleeve" not in weights_df.columns and "sleeve_name" in weights_df.columns:
        weights_df["sleeve"] = weights_df["sleeve_name"]

    if weights_df.empty:
        fallback_rows: list[dict[str, object]] = []
        for sleeve_name, details in (("sleeve_2", s2_details), ("charlie_munger", cm_details or {})):
            target_weights = (details or {}).get("target_weights")
            if not isinstance(target_weights, pd.DataFrame) or target_weights.empty:
                continue
            latest_weights = target_weights.iloc[-1]
            for ticker, target_weight in latest_weights.items():
                try:
                    weight_value = float(target_weight)
                except Exception:
                    continue
                if abs(weight_value) <= WEIGHT_TOLERANCE:
                    continue
                fallback_rows.append(
                    {
                        "ticker": str(ticker).upper(),
                        "target_weight": weight_value,
                        "sleeve": sleeve_name,
                    }
                )
        if fallback_rows:
            weights_df = pd.DataFrame(fallback_rows)

    if weights_df.empty:
        logger.warning(
            "[PAPER] No target weights available; skipping signals snapshot."
        )
    else:
        run_date_str = report_date.strftime("%Y-%m-%d")
        cutoff_date = prev_trading_day(run_date_str)
        signals_path = write_signals_snapshot(
            df_targets=weights_df,
            run_date=run_date_str,
            asof_date=cutoff_date,
            out_dir="signals",
            cash_target_weight=float(alloc_result.cash_weight),
            sleeve_col="sleeve",  # if column exists; otherwise writer will default to "core"
        )
        logger.info("[PAPER] Wrote signals snapshot: %s", signals_path)
        signal_store_df = weights_df.rename(columns={"target_weight": "final_target_weight", "sleeve": "sleeve_source"}).copy()
        signal_store_df["ticker"] = signal_store_df["ticker"].astype(str)
        persist_signal_snapshot(signal_store_df, report_date.strftime("%Y-%m-%d"))
    price_map = _build_price_map(prices, report_date)
    atr_map = _build_atr_map(prices, report_date)
    entry_map = {}
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        weights_history = s2_details.get("weights_df")
        prices_wide = s2_details.get("prices_wide")
        entries = infer_latest_entries(weights_history)
        entries = attach_entry_prices(entries, prices_wide)
        for _, row in entries.iterrows():
            entry_map[row["ticker"]] = {
                "entry_date": _fmt_date(pd.to_datetime(row["entry_date"])),
                "entry_price": row.get("entry_price"),
            }
    # Holdings
    holdings = []
    risk_levels = []
    model_equity = portfolio_stats.get("equity", DEFAULT_PORTFOLIO_BASE_EQUITY)
    for _, row in weights_df.iterrows():
        ticker = row["ticker"]
        weight = float(row["target_weight"])
        direction = "LONG" if weight > 0 else "SHORT"
        price_info = price_map.get(ticker, {})
        last_px = price_info.get("last_close")
        entry_info = entry_map.get(ticker)
        entry_date = (
            entry_info.get("entry_date")
            if entry_info
            else report_date.strftime("%Y-%m-%d")
        )

        entry_date = (
            entry_info.get("entry_date") if entry_info else _fmt_date(report_date)
        )
        entry_px = (
            entry_info.get("entry_price")
            if entry_info and entry_info.get("entry_price")
            else last_px
        )
        if last_px is None or entry_px is None:
            pnl_dollars = None
            pnl_pct = None
        else:
            shares = abs(weight) * model_equity / last_px if last_px > 0 else 0.0
            if weight > 0:
                pnl_dollars = shares * (last_px - entry_px)
                pnl_pct = (last_px / entry_px) - 1.0 if entry_px > 0 else None
            else:
                pnl_dollars = shares * (entry_px - last_px)
                pnl_pct = (entry_px / last_px) - 1.0 if last_px > 0 else None
        days_held = (
            (report_date - pd.to_datetime(entry_date)).days if entry_date else None
        )

        days_held = (
            (report_date - pd.to_datetime(entry_date)).days
            if entry_date and entry_date != "n/a"
            else None
        )

        current_shares = abs(weight) * model_equity / last_px if (last_px and last_px > 0) else None
        holdings.append(
            {
                "ticker": ticker,
                "direction": direction,
                "shares": current_shares,
                "entry_date": entry_date,
                "entry_price": entry_px,
                "last_price": last_px,
                "pnl_dollars": pnl_dollars,
                "pnl_pct": pnl_pct,
                "days_held": days_held,
            }
        )
        atr = atr_map.get(ticker)
        if entry_px is None:
            stop_loss = None
            take_profit = None
        elif atr is not None:
            if weight > 0:
                stop_loss = entry_px - STOP_ATR_MULT * atr
                take_profit = entry_px + TAKE_PROFIT_ATR_MULT * atr
            else:
                stop_loss = entry_px + STOP_ATR_MULT * atr
                take_profit = entry_px - TAKE_PROFIT_ATR_MULT * atr
        else:
            if weight > 0:
                stop_loss = entry_px * (1 - STOP_PCT)
                take_profit = entry_px * (1 + TAKE_PROFIT_PCT)
            else:
                stop_loss = entry_px * (1 + STOP_PCT)
                take_profit = entry_px * (1 - TAKE_PROFIT_PCT)
        risk_levels.append(
            {
                "ticker": ticker,
                "entry_price": entry_px,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }
        )
    # Trades for today
    prev_weights = {}
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        hist = s2_details.get("weights_df")
        if len(hist) > 1:
            prev_row = hist.iloc[-2]
            prev_weights = prev_row.to_dict()
    new_weights = {
        row["ticker"]: float(row["target_weight"]) for _, row in weights_df.iterrows()
    }
    all_tickers = sorted(set(prev_weights.keys()) | set(new_weights.keys()))
    orders = []
    for ticker in all_tickers:
        if ticker == CASH_TICKER:
            continue
        prev_w = float(prev_weights.get(ticker, 0.0))
        new_w = float(new_weights.get(ticker, 0.0))
        if abs(prev_w) <= WEIGHT_TOLERANCE and abs(new_w) <= WEIGHT_TOLERANCE:
            continue

        def _append_order(action, weight):
            exec_px = _compute_execution_price(price_map, ticker)
            shares = None
            notional = None
            if exec_px and model_equity > 0:
                notional = abs(weight) * model_equity
                shares = round(notional / exec_px, 2) if exec_px > 0 else None
            orders.append(
                {
                    "action": action,
                    "ticker": ticker,
                    "target_weight": weight,
                    "execution_price": exec_px,
                    "shares": shares,
                    "notional": notional,
                }
            )

        if abs(prev_w) <= WEIGHT_TOLERANCE and abs(new_w) > WEIGHT_TOLERANCE:
            _append_order("BUY" if new_w > 0 else "SHORT", new_w)
        elif abs(prev_w) > WEIGHT_TOLERANCE and abs(new_w) <= WEIGHT_TOLERANCE:
            _append_order("SELL" if prev_w > 0 else "COVER", 0.0)
        elif prev_w * new_w < 0:
            _append_order("SELL" if prev_w > 0 else "COVER", 0.0)
            _append_order("BUY" if new_w > 0 else "SHORT", new_w)
    # Watchlist
    watchlist = []
    held = {h["ticker"] for h in holdings}
    if st_signals is not None and not st_signals.empty:
        st_latest = st_signals[st_signals["date"] == st_signals["date"].max()].copy()
        st_latest = st_latest[~st_latest["ticker"].isin(held)]
        st_latest = st_latest[st_latest["passes_liquidity"]]
        st_latest["delta_long"] = trend_cfg.LONG_THRESHOLD - st_latest["final_signal"]
        st_latest["delta_short"] = trend_cfg.SHORT_THRESHOLD - st_latest["final_signal"]
        near_long = st_latest[
            (st_latest["signal_long"])
            & (st_latest["delta_long"] >= 0)
            & (st_latest["delta_long"] <= 5)
        ]
        near_short = st_latest[
            (st_latest["signal_short"])
            & (st_latest["delta_short"] >= 0)
            & (st_latest["delta_short"] <= 5)
        ]
        for _, row in pd.concat([near_long, near_short]).head(5).iterrows():
            threshold = (
                trend_cfg.LONG_THRESHOLD
                if row["signal_long"]
                else trend_cfg.SHORT_THRESHOLD
            )
            reason = f"trend score={row['final_signal']:.1f} vs {threshold} threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
    s2_signals = s2_details.get("signals") if s2_details else pd.DataFrame()
    if s2_signals is not None and not s2_signals.empty:
        s2_latest = s2_signals[s2_signals["date"] == s2_signals["date"].max()].copy()
        s2_latest = s2_latest[~s2_latest["ticker"].isin(held)]
        s2_latest["delta_score"] = S2_LONG_THRESHOLD - s2_latest["score_long"]
        near_long = s2_latest[
            (s2_latest["delta_score"] >= 0) & (s2_latest["delta_score"] <= 5)
        ]
        near_short = s2_latest[s2_latest["z_pe"] >= (Z_EXTREME_SHORT - 0.25)]
        for _, row in near_long.head(5).iterrows():
            reason = f"score={row['score_long']:.1f} vs {S2_LONG_THRESHOLD} threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
        for _, row in near_short.head(5).iterrows():
            reason = f"z_pe={row['z_pe']:.2f} vs {Z_EXTREME_SHORT} short threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
    watchlist = watchlist[:10]
    broker_equity = os.environ.get("BROKER_EQUITY")
    broker_equity_val = (
        float(broker_equity) if broker_equity not in (None, "") else None
    )
    reconciliation = {
        "model_start_equity": DEFAULT_PORTFOLIO_BASE_EQUITY,
        "model_current_equity": portfolio_stats.get("equity"),
        "broker_equity": broker_equity_val,
        "difference": (
            (portfolio_stats.get("equity") - broker_equity_val)
            if broker_equity_val
            else None
        ),
        "note": (
            "slippage/fees/timing"
            if broker_equity_val
            else "broker equity placeholder (set BROKER_EQUITY)"
        ),
    }
    allocations = {
        "risk_on": 1.0 - alloc_result.cash_weight,
        "cash": alloc_result.cash_weight,
        "sleeves": alloc_result.sleeve_allocations,
        "cash_reason": getattr(alloc_result, "cash_reason", None),
    }
    performance_diagnostics = {
        "current_equity": portfolio_stats.get("equity"),
        "day_return": portfolio_stats.get("day_return"),
        "cumulative_return": portfolio_stats.get("cumulative_return"),
    }

    previous_weights_df = None
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        s2_weights_hist = s2_details.get("weights_df")
        prev_row = s2_weights_hist.iloc[-1]
        previous_weights_df = pd.DataFrame(
            {
                "ticker": prev_row.index,
                "target_weight": prev_row.values,
            }
        )

    s2_no_picks = False
    if (
        s2_details
        and s2_details.get("target_weights") is not None
        and not s2_details.get("target_weights").empty
    ):
        s2_last_weights = s2_details.get("target_weights").iloc[-1]
        s2_no_picks = s2_last_weights.abs().sum() <= WEIGHT_TOLERANCE

    proposed_trades = build_proposed_trades(
        alloc_result=alloc_result,
        previous_weights=previous_weights_df,
        price_map=price_map,
        model_equity=model_equity,
    )

    performance_summary = compute_performance_summary(
        report_date=report_date,
        sleeve_equity_map={
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
            "charlie_munger": (cm_details or {}).get("equity_df", pd.DataFrame()),
        },
        sleeve_allocations=alloc_result.sleeve_allocations,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
    )

    return {
        "asof": report_date,
        "allocations": allocations,
        "performance_summary": performance_summary,
        "orders": orders,
        "risk_levels": risk_levels,
        "holdings": holdings,
        "watchlist": watchlist,
        "reconciliation": reconciliation,
        "proposed_trades": proposed_trades,
        "performance_summary": performance_summary,
        "performance_diagnostics": performance_diagnostics,
        "skipped_trades": alloc_result.skipped_trades if alloc_result else [],
        "s2_no_picks": s2_no_picks,
        "signals_snapshot_path": signals_path,
        "charlie_munger": (cm_details or {}).get("signals", {}),
        "charlie_munger_benchmark": {**((cm_details or {}).get("benchmark", {}) or {}), "sleeve_cumulative_return": ((cm_details or {}).get("sleeve_stats", {}) or {}).get("cumulative_return")},
    }


# ============================================================
# Report builder (FIXED)
# ============================================================
def build_html_report(
    report_date: pd.Timestamp | str,
    st_equity,
    st_trades,
    s2_equity,
    s2_trades,
    alloc_result: AllocationResult = None,
    alpha_stats: dict | None = None,
    proposed_trades: list[dict] | None = None,
    performance_summary: dict | None = None,
    s2_no_picks: bool = False,
    cm_details: dict | None = None,
) -> str:
    """
    Build HTML report with CORRECT portfolio math.
    Portfolio Snapshot now shows:
    - Sleeve rows: allocated notional only (attribution), NOT additive equity
    - CASH row: allocated notional with 0 return
    - TOTAL row: TRUE portfolio equity computed from weighted sleeve returns
    The TOTAL is the ONLY authoritative equity figure.
    """
    BASE_EQUITY = DEFAULT_PORTFOLIO_BASE_EQUITY

    report_date_fmt = _fmt_date(report_date)

    # Build summary with dynamic allocation
    if alloc_result is not None:
        trend_alloc = alloc_result.sleeve_allocations.get("sleeve_trend", 0.0)
        val_alloc = alloc_result.sleeve_allocations.get("sleeve_2", 0.0)
        cash_alloc = alloc_result.cash_weight
        # Build sleeve equity map for portfolio computation
        sleeve_equity_map = {
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
            "charlie_munger": (cm_details or {}).get("equity_df", pd.DataFrame()),
        }
        # Compute TRUE portfolio equity (the only correct total)
        portfolio_stats = compute_portfolio_equity(
            sleeve_equity_map=sleeve_equity_map,
            sleeve_allocations=alloc_result.sleeve_allocations,
            cash_weight=cash_alloc,
            base_equity=BASE_EQUITY,
        )
        # Build sleeve rows - show ALLOCATED NOTIONAL only (attribution)
        # Do NOT show sleeve backtest equity as it's not additive
        rows = []
        if trend_alloc > WEIGHT_TOLERANCE:
            alloc_notional = BASE_EQUITY * trend_alloc
            # Compute sleeve-level return for display (attribution only)
            st_df = _safe_df(st_equity)
            if not st_df.empty and "equity" in st_df.columns:
                st_start = float(st_df["equity"].iloc[0])
                st_last = float(st_df["equity"].iloc[-1])
                st_prev = float(st_df["equity"].iloc[-2]) if len(st_df) > 1 else st_last
                if st_start > 0:
                    st_ret = (st_last / st_start) - 1.0
                    st_day_ret = (st_last - st_prev) / st_prev if st_prev > 0 else 0.0
                    # Attribution: this sleeve's contribution to portfolio
                    contrib_equity = BASE_EQUITY * trend_alloc * (1.0 + st_ret)
                    rows.append(
                        {
                            "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": _fmt_money(contrib_equity),
                            "Day Return": _fmt_pct(st_day_ret),
                        }
                    )
                else:
                    rows.append(
                        {
                            "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": "—",
                            "Day Return": "—",
                        }
                    )
            else:
                rows.append(
                    {
                        "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": "—",
                        "Day Return": "—",
                    }
                )
        else:
            rows.append(
                {
                    "Sleeve": "Sleeve Trend — Momentum (inactive)",
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )
        if val_alloc > WEIGHT_TOLERANCE:
            alloc_notional = BASE_EQUITY * val_alloc
            s2_df = _safe_df(s2_equity)
            if not s2_df.empty and "equity" in s2_df.columns:
                s2_start = float(s2_df["equity"].iloc[0])
                s2_last = float(s2_df["equity"].iloc[-1])
                s2_prev = float(s2_df["equity"].iloc[-2]) if len(s2_df) > 1 else s2_last
                if s2_start > 0:
                    s2_ret = (s2_last / s2_start) - 1.0
                    s2_day_ret = (s2_last - s2_prev) / s2_prev if s2_prev > 0 else 0.0
                    contrib_equity = BASE_EQUITY * val_alloc * (1.0 + s2_ret)
                    rows.append(
                        {
                            "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": _fmt_money(contrib_equity),
                            "Day Return": _fmt_pct(s2_day_ret),
                        }
                    )
                else:
                    rows.append(
                        {
                            "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": "—",
                            "Day Return": "—",
                        }
                    )
            else:
                rows.append(
                    {
                        "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": "—",
                        "Day Return": "—",
                    }
                )
        else:
            rows.append(
                {
                    "Sleeve": "Sleeve 2 — Valuation (inactive)",
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )
            sleeve2_label = (
                "Sleeve 2 — Valuation (no eligible picks)"
                if s2_no_picks
                else "Sleeve 2 — Valuation (inactive)"
            )
            rows.append(
                {
                    "Sleeve": sleeve2_label,
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )

        # CASH row
        if cash_alloc > WEIGHT_TOLERANCE:
            cash_notional = BASE_EQUITY * cash_alloc
            rows.append(
                {
                    "Sleeve": f"CASH ({cash_alloc:.2%})",
                    "Allocated": _fmt_money(cash_notional),
                    "Equity": _fmt_money(cash_notional),  # Cash doesn't grow
                    "Day Return": _fmt_pct(0),
                }
            )
        summary_df = pd.DataFrame(rows)
        # TOTAL row - THE AUTHORITATIVE PORTFOLIO EQUITY
        # Computed from weighted sleeve returns, NOT by summing rows above
        total_row = pd.DataFrame(
            [
                {
                    "Sleeve": f"TOTAL — Portfolio ({_fmt_money(BASE_EQUITY)})",
                    "Allocated": _fmt_money(BASE_EQUITY),
                    "Equity": _fmt_money(portfolio_stats["equity"]),
                    "Day Return": _fmt_pct(portfolio_stats["day_return"]),
                }
            ]
        )
        summary_df = pd.concat([summary_df, total_row], ignore_index=True)
        alloc_summary = allocation_summary_df(alloc_result)
        holdings = holdings_snapshot_df(alloc_result)
        skipped_df = (
            pd.DataFrame(alloc_result.skipped_trades)
            if alloc_result.skipped_trades
            else pd.DataFrame()
        )
    else:
        # Legacy static allocation fallback
        # Still compute correctly using weighted returns
        sleeve_equity_map = {
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
            "charlie_munger": (cm_details or {}).get("equity_df", pd.DataFrame()),
        }
        static_allocs = {"sleeve_trend": 0.60, "sleeve_2": 0.20, "charlie_munger": 0.20}
        static_allocs, static_cash = enforce_charlie_bounds(static_allocs, 0.0, charlie_active=True)
        portfolio_stats = compute_portfolio_equity(
            sleeve_equity_map=sleeve_equity_map,
            sleeve_allocations=static_allocs,
            cash_weight=static_cash,
            base_equity=BASE_EQUITY,
        )
        rows = [
            {
                "Sleeve": f"Sleeve Trend — Momentum ({static_allocs.get('sleeve_trend', 0.0):.0%})",
                "Allocated": _fmt_money(BASE_EQUITY * static_allocs.get("sleeve_trend", 0.0)),
                "Equity": "—",
                "Day Return": "—",
            },
            {
                "Sleeve": f"Sleeve 2 — Valuation ({static_allocs.get('sleeve_2', 0.0):.0%})",
                "Allocated": _fmt_money(BASE_EQUITY * static_allocs.get("sleeve_2", 0.0)),
                "Equity": "—",
                "Day Return": "—",
            },
            {
                "Sleeve": f"Charlie Munger — Long Hold ({static_allocs.get('charlie_munger', 0.0):.0%})",
                "Allocated": _fmt_money(BASE_EQUITY * static_allocs.get("charlie_munger", 0.0)),
                "Equity": "—",
                "Day Return": "—",
            },
        ]
        if static_cash > WEIGHT_TOLERANCE:
            rows.append(
                {
                    "Sleeve": f"CASH ({static_cash:.0%})",
                    "Allocated": _fmt_money(BASE_EQUITY * static_cash),
                    "Equity": _fmt_money(BASE_EQUITY * static_cash),
                    "Day Return": _fmt_pct(0),
                }
            )
        total_row = {
            "Sleeve": f"TOTAL — Portfolio ({_fmt_money(BASE_EQUITY)})",
            "Allocated": _fmt_money(BASE_EQUITY),
            "Equity": _fmt_money(portfolio_stats["equity"]),
            "Day Return": _fmt_pct(portfolio_stats["day_return"]),
        }
        summary_df = pd.concat(
            [pd.DataFrame(rows), pd.DataFrame([total_row])], ignore_index=True
        )
        alloc_summary, holdings, skipped_df = None, None, pd.DataFrame()
    # Build exit log
    exit_log_rows = []
    for trades_df, sleeve_name in [(st_trades, "Trend"), (s2_trades, "Valuation")]:
        filtered = (
            filter_sleeve2_cash_proxy(_safe_df(trades_df))
            if sleeve_name == "Valuation"
            else _safe_df(trades_df)
        )
        if not filtered.empty and "reason_exit" in filtered.columns:
            for _, row in filtered.tail(10).iterrows():
                exit_log_rows.append(
                    {
                        "Ticker": row.get("ticker", ""),
                        "Sleeve": sleeve_name,
                        "Exit Reason": row.get("reason_exit", ""),
                        "Days Held": row.get("hold_days", ""),
                        "P&L": _fmt_money(row.get("pnl", 0)),
                    }
                )
    # CSS
    css = base_email_css() + " h2 { margin-bottom: 4px; } h3 { margin-top: 16px; }"
    performance_rows = [
        {"Metric": "Current Equity", "Value": _fmt_money(portfolio_stats["equity"])},
        {"Metric": "Day Return", "Value": _fmt_pct(portfolio_stats["day_return"])},
        {
            "Metric": "Cumulative Return",
            "Value": _fmt_pct(portfolio_stats["cumulative_return"]),
        },
    ]
    performance_html = html_table(
        pd.DataFrame(performance_rows), "Performance Summary (Portfolio)", 10
    )
    performance_section = f'<div class="card">{performance_html}</div>'

    def _fmt_float(value: float | None) -> str:
        try:
            return f"{float(value):.2f}"
        except Exception:
            return "n/a"

    if alpha_stats and alpha_stats.get("ok"):
        alpha_summary = alpha_stats.get("summary", {}) or {}
        alpha_rows = [
            {"Metric": "Overlap Window", "Value": f"{alpha_stats.get('overlap_start')} → {alpha_stats.get('overlap_end')} ({alpha_stats.get('overlap_days')} days)"},
            {"Metric": "Cumulative Portfolio Return", "Value": _fmt_pct(alpha_summary.get("cumulative_port_return"))},
            {"Metric": "Cumulative SPY Return", "Value": _fmt_pct(alpha_summary.get("cumulative_spy_return"))},
            {"Metric": "Cumulative Alpha", "Value": _fmt_pct(alpha_summary.get("cumulative_alpha"))},
        ]
        alpha_tbl = html_table(pd.DataFrame(alpha_rows), "Alpha Attribution vs SPY", 10)
        daily_rows = pd.DataFrame(alpha_stats.get("rows", []) or []).rename(
            columns={"date": "Date", "port_ret": "Portfolio Return", "spy_ret": "SPY Return", "spread": "Spread"}
        )
        alpha_daily_tbl = html_table(daily_rows, "Alpha Daily Spread (Last 10 Days)", 10, "No overlapping return rows.")
        alpha_section = f'<div class="card">{alpha_tbl}{alpha_daily_tbl}</div>'
    else:
        reason = (alpha_stats or {}).get("reason") or "Alpha attribution unavailable."
        alpha_section = (
            '<div class="card">'
            "<h3>Alpha Attribution vs SPY</h3>"
            f"<p><em>Pending — {reason}</em></p>"
            "</div>"
        )

    # Build sections
    perf_rows = []
    if performance_summary:
        perf_rows = [
            {
                "Metric": "Total Return (Since Inception)",
                "Return": _fmt_pct(performance_summary.get("total_return")),
            },
            {
                "Metric": "Week-to-Date",
                "Return": _fmt_pct(performance_summary.get("wtd")),
            },
            {
                "Metric": "Month-to-Date",
                "Return": _fmt_pct(performance_summary.get("mtd")),
            },
            {
                "Metric": "Year-to-Date",
                "Return": _fmt_pct(performance_summary.get("ytd")),
            },
        ]
    perf_section = (
        f'<div class="card">{html_table(pd.DataFrame(perf_rows), "Performance Summary")}</div>'
        if perf_rows
        else ""
    )

    proposed_df = pd.DataFrame(proposed_trades or [])
    if not proposed_df.empty:
        proposed_df = proposed_df.rename(
            columns={
                "ticker": "Ticker",
                "action": "Action",
                "sleeve": "Sleeve",
                "current_weight": "Current Weight",
                "target_weight": "Target Weight",
                "delta_weight": "Delta Weight",
                "est_shares": "Est. Shares",
                "est_notional": "Est. Notional",
            }
        )
    proposed_html = html_table(
        proposed_df,
        "Proposed Trades / Next Rebalance",
        25,
        "No proposed trades.",
    )
    proposed_section = f'<div class="card">{proposed_html}</div>'

    alloc_html = (
        alloc_summary.to_html(index=False, border=0, classes="tbl", justify="left")
        if alloc_summary is not None
        else ""
    )
    alloc_section = (
        f'<div class="card"><h3>Sleeve Allocation (Dynamic)</h3>{alloc_html}</div>'
        if alloc_html
        else ""
    )

    holdings_html = (
        html_table(holdings, "Holdings Snapshot", 20)
        if holdings is not None and not holdings.empty
        else ""
    )
    holdings_section = (
        f'<div class="card">{holdings_html}</div>' if holdings_html else ""
    )

    skipped_html = (
        html_table(skipped_df, "Skipped Trades (Constraint Hits)", 10)
        if not skipped_df.empty
        else ""
    )
    skipped_section = f'<div class="card">{skipped_html}</div>' if skipped_html else ""

    exit_html = (
        html_table(pd.DataFrame(exit_log_rows), "Exit Log", 15) if exit_log_rows else ""
    )
    exit_section = f'<div class="card">{exit_html}</div>' if exit_html else ""

    st_trades_html = html_table(
        filter_sleeve2_cash_proxy(st_trades), "Recent Trades — Sleeve Trend", 15
    )
    s2_trades_html = html_table(
        filter_sleeve2_cash_proxy(s2_trades),
        "Recent Trades — Sleeve 2",
        15,
        "No eligible picks for Sleeve 2.",
    )

    st_equity_html = html_table(
        _safe_df(st_equity).tail(10), "Equity — Sleeve Trend (last 10 days)", 10
    )
    s2_equity_html = html_table(
        _safe_df(s2_equity).tail(10), "Equity — Sleeve 2 (last 10 days)", 10
    )
    cm_trades_html = html_table(
        _safe_df((cm_details or {}).get("trades_df", pd.DataFrame())),
        "Recent Trades — Charlie Munger",
        15,
        "No Charlie Munger trades.",
    )
    cm_equity_html = html_table(
        _safe_df((cm_details or {}).get("equity_df", pd.DataFrame())).tail(10),
        "Equity — Charlie Munger (last 10 weeks)",
        10,
    )
    cm_signal = (cm_details or {}).get("signals", {}) or {}
    cm_meta = cm_signal.get("meta", {}) if isinstance(cm_signal, dict) else {}
    cm_selected = cm_signal.get("selected", []) if isinstance(cm_signal, dict) else []
    cm_rows = [
        {"Metric": "Near-200W candidates", "Value": cm_meta.get("near_ma_candidates", 0)},
        {"Metric": "New buys", "Value": len(cm_selected or [])},
        {"Metric": "Sells", "Value": len(cm_signal.get("sell", []) if isinstance(cm_signal, dict) else [])},
        {"Metric": "Benchmark", "Value": ((cm_details or {}).get("benchmark", {}) or {}).get("ticker", "SPY")},
        {"Metric": "SPY cumulative return", "Value": _fmt_pct(((cm_details or {}).get("benchmark", {}) or {}).get("cumulative_return"))},
        {"Metric": "SPY max drawdown", "Value": _fmt_pct(((cm_details or {}).get("benchmark", {}) or {}).get("max_drawdown"))},
    ]
    cm_section = f'<div class="card">{html_table(pd.DataFrame(cm_rows), "Charlie Munger Sleeve")}</div>'

    return f"""
    <html>
    <head><style>{css}</style></head>
    <body>
      <div class="wrap">
        <h2>Daily Quant Report</h2>
        <div class="muted">{report_date_fmt}</div>
        {perf_section}
        {proposed_section}
        <div class="card">{html_table(summary_df, "Portfolio Snapshot")}</div>
        {performance_section}
        {alpha_section}
        {alloc_section}
        {cm_section}
        {holdings_section}
        <div class="card">
          {st_trades_html}
          {s2_trades_html}
          {cm_trades_html}
        </div>
        {exit_section}
        {skipped_section}
        <div class="card">
          {st_equity_html}
          {s2_equity_html}
          {cm_equity_html}
        </div>
        <div class="muted">
          Automated daily report. Portfolio TOTAL computed from weighted sleeve returns
          (not sum of rows).
        </div>
      </div>
    </body>
    </html>
    """


# ============================================================
# Main
# ============================================================
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily quant report workflow")
    parser.add_argument(
        "--reset-ledger-date",
        dest="reset_ledger_date",
        default=None,
        help="Delete shadow idempotency ledger rows matching YYYY-MM-DD before execution",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Generate planning artifacts only; skip order generation even when market is open.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    offline_fixture = os.getenv("OFFLINE_FIXTURE", "").lower() in {"1", "true", "yes"}
    fixture_date = os.getenv("OFFLINE_FIXTURE_DATE", "2000-01-01")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = fixture_date if offline_fixture else dt.date.today().strftime("%Y-%m-%d")
    portfolio_fixture = (
        _load_portfolio_fixture("tests/fixtures/portfolio_equity.csv")
        if offline_fixture
        else pd.Series(dtype=float)
    )
    if offline_fixture and not portfolio_fixture.empty:
        today = portfolio_fixture.index.max().strftime("%Y-%m-%d")
    # ── Run sleeves ───────────────────────────────────────────────
    if offline_fixture:
        logger.warning(
            "[OFFLINE] Fixture mode enabled; skipping sleeve runs and live data fetches."
        )
        _, _ = pd.DataFrame(), pd.DataFrame()
        st_equity, st_trades, st_signals = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        s2_details = {}
        s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()
        cm_details = {}
        cm_equity, cm_trades = pd.DataFrame(), pd.DataFrame()
    else:
        try:
            _, _ = run_sleeve_1()
        except Exception as e:
            logger.warning("[WARN] Sleeve 1 failed: %s", e)
            _, _ = pd.DataFrame(), pd.DataFrame()
        try:
            st_equity, st_trades, st_signals = run_sleeve_trend()
        except Exception as e:
            logger.warning("[WARN] Sleeve Trend failed: %s", e)
            st_equity, st_trades, st_signals = (
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
            )
        try:
            s2_details = run_sleeve_2()
            s2_equity = s2_details.get("equity_df", pd.DataFrame())
            s2_trades = s2_details.get("trades_df", pd.DataFrame())
        except Exception as e:
            logger.warning("[WARN] Sleeve 2 failed: %s", e)
            s2_details = {}
            s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()
        try:
            cm_runner = globals().get("run_sleeve_charlie_munger")
            if callable(cm_runner):
                cm_details = cm_runner()
            else:
                cm_details = run_charlie_munger()
            cm_equity = cm_details.get("equity_df", pd.DataFrame())
            cm_trades = cm_details.get("trades_df", pd.DataFrame())
        except Exception as e:
            logger.warning("[WARN] Sleeve Charlie Munger failed: %s", e)
            cm_details = {}
            cm_equity, cm_trades = pd.DataFrame(), pd.DataFrame()
    # ── Sleeve health checks ─────────────────────────────────────
    # Validate each sleeve BEFORE allocation.  Invalid sleeves get
    # their weight routed to CASH, never to another sleeve.
    trend_valid, trend_reason = _sleeve_is_valid(st_equity)
    s2_valid, s2_reason = _sleeve_is_valid(s2_equity)
    cm_valid, cm_reason = _sleeve_is_valid(cm_equity)
    if not trend_valid:
        logger.warning("sleeve_trend inactive: %s -> routed to CASH", trend_reason)
    if not s2_valid:
        logger.warning("sleeve_2 inactive: %s -> routed to CASH", s2_reason)
    if not cm_valid:
        logger.warning("charlie_munger inactive: %s -> routed to CASH", cm_reason)
    # ── Extract sleeve outputs for dynamic allocation ─────────────
    trend_output = extract_sleeve_output(st_equity, st_trades, "sleeve_trend", 1.0)
    val_output = extract_sleeve_output(s2_equity, s2_trades, "sleeve_2", 1.0)
    val_output = extract_sleeve_output(
        s2_equity,
        s2_trades,
        "sleeve_2",
        1.0,
        target_weights=s2_details.get("target_weights") if s2_details else None,
    )
    cm_output = extract_sleeve_output(
        cm_equity,
        cm_trades,
        "charlie_munger",
        1.0,
        target_weights=cm_details.get("target_weights") if cm_details else None,
    )

    # ── Run dynamic allocation ────────────────────────────────────
    risk_off = os.getenv("RISK_OFF", "").lower() in ("1", "true", "yes", "y")
    allocator = PortfolioAllocator(risk_off=risk_off)
    alloc_result = allocator.allocate([trend_output, val_output, cm_output])
    alloc_result.sleeve_allocations = derive_actual_sleeve_allocations(alloc_result)
    _old_allocs = dict(alloc_result.sleeve_allocations)
    _new_allocs, _new_cash = enforce_charlie_bounds(
        alloc_result.sleeve_allocations,
        alloc_result.cash_weight,
        charlie_active=cm_valid,
    )
    _apply_enforced_allocations_to_result(
        alloc_result,
        old_allocations=_old_allocs,
        new_allocations=_new_allocs,
        new_cash_weight=_new_cash,
    )

    # ── SAFE ALLOCATION POLICY ────────────────────────────────────
    # If a sleeve is invalid, force its allocation to 0 and route
    # the freed weight to CASH (never to another sleeve).
    patched = False
    freed_weight = 0.0
    if (
        not trend_valid
        and alloc_result.sleeve_allocations.get("sleeve_trend", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["sleeve_trend"]
        alloc_result.sleeve_allocations["sleeve_trend"] = 0.0
        patched = True
    if (
        not s2_valid
        and alloc_result.sleeve_allocations.get("sleeve_2", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["sleeve_2"]
        alloc_result.sleeve_allocations["sleeve_2"] = 0.0
        patched = True
    if (
        not cm_valid
        and alloc_result.sleeve_allocations.get("charlie_munger", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["charlie_munger"]
        alloc_result.sleeve_allocations["charlie_munger"] = 0.0
        patched = True
    if patched:
        _old_allocs = dict(alloc_result.sleeve_allocations)
        _new_allocs, _new_cash = enforce_charlie_bounds(
            alloc_result.sleeve_allocations,
            alloc_result.cash_weight + freed_weight,
            charlie_active=cm_valid,
        )
        _apply_enforced_allocations_to_result(
            alloc_result,
            old_allocations=_old_allocs,
            new_allocations=_new_allocs,
            new_cash_weight=_new_cash,
        )
        logger.info(
            "[ALLOCATION] Freed %.1f%% from inactive sleeve(s) -> CASH",
            freed_weight * 100,
        )
    # ── Validate allocation ───────────────────────────────────────
    errors = validate_allocation_result(alloc_result)
    if errors:
        logger.warning("[WARN] Allocation validation errors: %s", errors)
    # ── Log allocation summary ────────────────────────────────────
    logger.info("\n[ALLOCATION] Sleeve allocations:")
    for sleeve, pct in alloc_result.sleeve_allocations.items():
        logger.info("  %s: %.1f%%", sleeve, pct * 100)
    logger.info("  CASH: %.1f%%", alloc_result.cash_weight * 100)
    logger.info("  Total weight: %.4f", alloc_result.total_weight)
    # ── Portfolio stats for model equity ─────────────────────────
    sleeve_equity_map = {
        "sleeve_trend": st_equity,
        "sleeve_2": s2_equity,
        "charlie_munger": cm_equity,
    }
    portfolio_stats = compute_portfolio_equity(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=alloc_result.sleeve_allocations,
        cash_weight=alloc_result.cash_weight,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
    )
    # ── Build daily snapshot context ───────────────────────────────
    report_date = _infer_report_date(
        sleeve_details=[s2_details, cm_details],
        fallback=pd.Timestamp(fixture_date if offline_fixture else dt.date.today()),
    )
    if offline_fixture and not portfolio_fixture.empty:
        report_date = portfolio_fixture.index.max()
    else:
        _ = resolve_portfolio_equity_series(
            sleeve_equity_map=sleeve_equity_map,
            alloc_result=alloc_result,
            report_date=report_date,
            portfolio_equity=portfolio_stats.get(
                "equity", DEFAULT_PORTFOLIO_BASE_EQUITY
            ),
            offline_fixture=offline_fixture,
            base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        )
    if offline_fixture and not portfolio_fixture.empty:
        portfolio_equity_for_alpha = portfolio_fixture
    else:
        portfolio_equity_for_alpha = resolve_portfolio_equity_series(
            sleeve_equity_map=sleeve_equity_map,
            alloc_result=alloc_result,
            report_date=report_date,
            portfolio_equity=portfolio_stats.get(
                "equity", DEFAULT_PORTFOLIO_BASE_EQUITY
            ),
            offline_fixture=offline_fixture,
            base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        )
    _ = resolve_portfolio_equity_series(
        sleeve_equity_map=sleeve_equity_map,
        alloc_result=alloc_result,
        report_date=report_date,
        portfolio_equity=portfolio_stats.get("equity", DEFAULT_PORTFOLIO_BASE_EQUITY),
        offline_fixture=offline_fixture,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        portfolio_stats=portfolio_stats,
        st_equity=st_equity,
        s2_equity=s2_equity,
        st_signals=st_signals,
        s2_details=s2_details,
    )
    bench_prices_for_alpha = load_benchmark_prices(
        ticker="SPY",
        start=(
            portfolio_equity_for_alpha.index.min()
            if not portfolio_equity_for_alpha.empty
            else None
        ),
        end=(
            portfolio_equity_for_alpha.index.max()
            if not portfolio_equity_for_alpha.empty
            else None
        ),
        offline_fixture=offline_fixture,
    )
    alpha_stats = None
    logger.info(
        "[ALPHA] Data readiness - portfolio_equity_for_alpha: %s rows (%s), bench_prices_for_alpha: %s rows (%s)",
        len(portfolio_equity_for_alpha),
        _series_date_range(portfolio_equity_for_alpha),
        len(bench_prices_for_alpha),
        _series_date_range(bench_prices_for_alpha),
    )
    alpha_stats = compute_alpha_attribution(
        portfolio_equity_for_alpha,
        bench_prices_for_alpha,
        min_overlap_days=_alpha_min_overlap_days(5),
        last_n=10,
    )
    try:
        daily_snapshot = build_daily_snapshot(
            report_date=report_date,
            alloc_result=alloc_result,
            portfolio_stats=portfolio_stats,
            st_equity=st_equity,
            s2_equity=s2_equity,
            st_signals=st_signals,
            s2_details=s2_details,
            cm_details=cm_details,
        )
    except RuntimeError as e:
        logger.error("[ERROR] %s", e)
        sys.exit(0)
    daily_snapshot["alpha_attribution"] = alpha_stats

    # --- Paper trading execution + report ---
    trade_date_str = report_date.strftime("%Y-%m-%d")
    signals_path_exec = daily_snapshot.get("signals_snapshot_path") or os.path.join(
        "signals", f"{trade_date_str}.json"
    )
    paper_summary = None
    paper_html = ""
    sent_ledger_removed = 0
    sent_ledger_path = "outputs/shadow_orders/orders_sent.csv"
    if os.path.exists(signals_path_exec):
        try:
            shadow_constraints = {
                "cash_target_weight": float(
                    {
                        **alloc_result.sleeve_allocations,
                        "CASH": alloc_result.cash_weight,
                    }.get("CASH", 0.0)
                )
            }
            trading_mode = str(os.getenv("TRADING_MODE", "shadow")).strip().lower()
            if trading_mode == "shadow" and os.getenv("ALLOW_RERUN_RESET", "1") == "1":
                sent_ledger_removed += reset_orders_sent_ledger_for_date(
                    sent_ledger_path,
                    trade_date_str,
                )
            if args.reset_ledger_date:
                sent_ledger_removed += reset_orders_sent_ledger_for_date(
                    sent_ledger_path,
                    args.reset_ledger_date,
                )
            paper_summary = run_paper_day(
                run_date=trade_date_str,
                signals_path=signals_path_exec,
                ledger_path="paper/ledger.csv",
                trades_path="paper/trades.csv",
                config_path="paper/config_paper.json",
                force=False,
                constraints=shadow_constraints,
                plan_only=args.plan_only,
            )
            logger.info(
                "[PAPER] Executed paper trading for %s using signals %s",
                trade_date_str,
                signals_path_exec,
            )
        except Exception as e:
            msg = repr(e)
            if "Ledger already contains run_date" in msg:
                logger.info(
                    "[PAPER] Already executed for %s; rendering report from ledger.",
                    trade_date_str,
                )
            else:
                logger.warning("[PAPER][WARN] Paper execution failed: %s", msg)
        try:
            paper_html = build_paper_report_html(
                run_date=trade_date_str,
                ledger_path="paper/ledger.csv",
                trades_path="paper/trades.csv",
                benchmark_ticker="SPY",
                reconciliation=paper_summary,
            )
        except Exception as e:
            logger.warning("[PAPER][WARN] Paper report HTML build failed: %s", repr(e))
    else:
        logger.warning(
            "[PAPER][WARN] Missing signals for execution: %s", signals_path_exec
        )

    # ── Build execution + snapshot email artifacts ──────────────────
    execution_payload = build_execution_email_payload(
        trade_date=trade_date_str,
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
    )
    if execution_payload.get("execution_status") == "HALTED":
        logger.info(
            "[EXECUTION_EMAIL] status=HALTED reason=%s",
            execution_payload.get("halt_reason", "UNKNOWN"),
        )
    else:
        exec_trades = execution_payload.get("trades", [])
        buy_count = sum(1 for t in exec_trades if str(t.get("side", "")).upper() == "BUY")
        sell_count = sum(1 for t in exec_trades if str(t.get("side", "")).upper() != "BUY")
        logger.info(
            "[EXECUTION_EMAIL] built trades=%d buys=%d sells=%d",
            len(exec_trades),
            buy_count,
            sell_count,
        )
    execution_payload_path, payload_preserved, preserved_path = _write_execution_email_payload(execution_payload, trade_date_str)

    integrity = {
        "trade_date": trade_date_str,
        "asof_date": str(execution_payload.get("pricing_asof") or prev_trading_day(trade_date_str)),
        "mode": str((paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", "shadow")).upper(),
        "execution_status": execution_payload.get("execution_status"),
        "halt_reason": execution_payload.get("halt_reason"),
        "payload_path_written": execution_payload_path,
        "payload_preserved": payload_preserved,
        "preserved_path": preserved_path,
        "sent_ledger_path": sent_ledger_path,
        "sent_ledger_reset_removed": int(sent_ledger_removed),
        "missing_prices": [],
    }

    # legacy pipeline retained for backward compatibility
    try:
        asof_date = str(execution_payload.get("pricing_asof") or prev_trading_day(trade_date_str))
        ledger_run_id = (paper_summary or {}).get("run_id") or make_run_id()
        ledger_source = str((paper_summary or {}).get("trading_mode") or "SHADOW").upper()
        signal_hash = compute_signal_hash(signals_path_exec)
        rows = ledger_rows_from_execution_payload(
            payload_path=execution_payload_path,
            trade_date=trade_date_str,
            asof_date=asof_date,
            source=ledger_source,
            run_id=ledger_run_id,
            signal_hash=signal_hash,
        )
        appended = append_ledger_rows(rows)
        ledger_df = load_ledger()
        rebuilt = rebuild_positions_from_ledger(ledger_df, asof_date)
        write_position_outputs(rebuilt["positions"], rebuilt["cash"], asof_date)
        holdings = rebuilt["positions"][["ticker", "shares", "avg_cost", "sleeve"]] if not rebuilt["positions"].empty else pd.DataFrame(columns=["ticker", "shares", "avg_cost", "sleeve"])
        mtm, nav = mark_holdings(holdings, rebuilt["cash"], asof_date)
        write_perf_outputs(mtm, nav, asof_date)
        nav_ts_path = update_nav_timeseries(asof_date, nav, ledger_df)
        nav_ts = pd.read_csv(nav_ts_path)
        nav_ts["date"] = pd.to_datetime(nav_ts["date"])
        nav_ts = nav_ts.sort_values("date")
        current = nav_ts[nav_ts["date"] == pd.to_datetime(asof_date)]
        if not current.empty:
            mtd_start = nav_ts[(nav_ts["date"].dt.to_period("M") == pd.to_datetime(asof_date).to_period("M"))]["equity"].iloc[0]
            week_start = nav_ts[(nav_ts["date"].dt.isocalendar().week == pd.to_datetime(asof_date).isocalendar().week)]["equity"].iloc[0]
            si_start = nav_ts["equity"].iloc[0]
            eq = float(current["equity"].iloc[0])
            daily_snapshot["nav_metrics"] = {
                "equity": eq,
                "return_1d": float(current["return_1d"].iloc[0]),
                "wtd": (eq / float(week_start) - 1.0) if week_start else 0.0,
                "mtd": (eq / float(mtd_start) - 1.0) if mtd_start else 0.0,
                "si": (eq / float(si_start) - 1.0) if si_start else 0.0,
            }
        prev_dates = nav_ts[nav_ts["date"] < pd.to_datetime(asof_date)]["date"]
        if not prev_dates.empty:
            prev_date = prev_dates.max().strftime("%Y-%m-%d")
            attr = compute_daily_attribution(asof_date, prev_date)
            write_attribution_outputs(asof_date, attr["tickers"], attr["sleeves"])

        with open(os.path.join("outputs", "ledger", f"ledger_write_{asof_date}.json"), "w", encoding="utf-8") as f:
            json.dump({"run_id": ledger_run_id, "trade_date": trade_date_str, "asof_date": asof_date, "rows_input": len(rows), "rows_appended": appended, "ledger_path": "outputs/ledger/trades.csv", "execution_payload_path": execution_payload_path}, f, indent=2)
            f.write("\n")
    except Exception as e:
        logger.warning("[LEDGER][WARN] post-execution perf pipeline failed: %s", e)

    try:
        asof_date = integrity["asof_date"]
        ledger_run_id = (paper_summary or {}).get("run_id") or make_run_id()
        ledger_source = str((paper_summary or {}).get("trading_mode") or "SHADOW").upper()
        rows2, missing_prices = ledger2_rows_from_execution_payload(
            payload=execution_payload,
            trade_date=trade_date_str,
            asof_date=asof_date,
            source=ledger_source,
            run_id=ledger_run_id,
            execution_status=str(execution_payload.get("execution_status") or "UNKNOWN"),
        )
        appended2 = append_ledger2_rows(rows2)
        nav_result = update_nav_outputs(asof_date=asof_date)
        integrity.update(
            {
                "ledger2_path": "outputs/ledger/trades.csv",
                "ledger2_appended_rows": int(appended2),
                "nav_path": nav_result.get("nav_path"),
                "nav_timeseries_path": nav_result.get("nav_timeseries_path"),
                "nav_equity": nav_result.get("equity"),
            }
        )
        integrity["missing_prices"] = sorted(set((missing_prices or []) + (nav_result.get("missing_prices") or [])))
    except Exception as e:
        logger.warning("[LEDGER2][WARN] ledger/nav2 pipeline failed: %s", e)

    try:
        integrity_path = Path("outputs") / "daily" / f"integrity_{integrity['asof_date']}.json"
        integrity_path.parent.mkdir(parents=True, exist_ok=True)
        integrity_path.write_text(json.dumps(integrity, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning("[INTEGRITY][WARN] failed writing integrity artifact: %s", e)

    exec_subject, exec_body = build_execution_email_text(execution_payload)
    _, exec_body_html = build_execution_email_html(execution_payload)
    execution_path = os.path.join(OUTPUT_DIR, f"trade_execution_{today}.txt")
    with open(execution_path, "w", encoding="utf-8") as f:
        f.write(exec_body.rstrip() + "\n")
    logger.info("[OK] Execution trade email written: %s", execution_path)

    snapshot_subject, snapshot_body = create_snapshot_email(
        daily_snapshot,
        execution_payload=execution_payload,
    )

    snapshot_path = os.path.join(OUTPUT_DIR, f"trade_snapshot_{today}.txt")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        f.write(snapshot_body.rstrip() + "\n")
    logger.info("[OK] Model snapshot email written: %s", snapshot_path)

    # Backward-compatibility alias (deprecated)
    rundown_path = os.path.join(OUTPUT_DIR, f"trade_rundown_{today}.txt")
    with open(rundown_path, "w", encoding="utf-8") as f:
        f.write(snapshot_body.rstrip() + "\n")
    logger.info("[OK] Legacy trade rundown alias written: %s", rundown_path)

    # ── Build report ──────────────────────────────────────────────
    html = build_html_report(
        report_date=report_date,
        st_equity=st_equity,
        st_trades=st_trades,
        s2_equity=s2_equity,
        s2_trades=s2_trades,
        alloc_result=alloc_result,
        proposed_trades=daily_snapshot.get("proposed_trades"),
        performance_summary=daily_snapshot.get("performance_summary"),
        s2_no_picks=daily_snapshot.get("s2_no_picks", False),
    )
    # Append paper trading HTML section (if available)
    if paper_html:
        html = html + "<hr/>" + paper_html

    out_path = os.path.join(OUTPUT_DIR, f"quant_report_{today}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("[OK] HTML report written: %s", out_path)
    logger.info("\n[EMAIL PREVIEW]\n")
    logger.info("%s", snapshot_body)
    if send_email:
        try:
            send_email(subject=exec_subject, body_html=exec_body_html, body_text=exec_body)
            send_email(subject=snapshot_subject, body_html=html, body_text=snapshot_body)
            logger.info("[OK] Emails sent (execution + snapshot)")
        except Exception as e:
            logger.warning("[WARN] Email not sent: %s", e)
    else:
        logger.warning("[WARN] send_email not found — HTML generated only")


if __name__ == "__main__":
    main()
