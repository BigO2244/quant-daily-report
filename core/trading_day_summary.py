"""
Trading Day Summary — observability artifact for post-run CIO review.

Aggregates execution, portfolio, email, dashboard, and benchmark signals into
canonical per-run and latest-mirror JSON files so the CIO can answer in seconds:
  - Did trades execute?
  - Did both buys and sells occur?
  - What is the ending cash balance?
  - Were exactly 3 emails sent?
  - Did the dashboard generate?
  - How are we performing vs SPY?

All reads are best-effort and non-blocking.  Missing source files produce
null-valued fields rather than hard failures.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from brokers.alpaca_snapshot import summarize_pretrade_broker_policy
from core.execution_audit import safe_artifact_name

logger = logging.getLogger(__name__)

SUMMARY_PATH = Path("outputs/trading_day_summary.json")
RUN_SUMMARY_FILENAME = "trading_day_summary.json"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[SUMMARY] could not read %s: %s", path, exc)
    return None


def _last_csv_row(path: Path) -> Optional[Dict[str, str]]:
    """Return the last non-empty row of a CSV file as a dict, or None."""
    try:
        if not path.exists():
            return None
        rows = []
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if any(v.strip() for v in row.values()):
                    rows.append(row)
        return rows[-1] if rows else None
    except Exception as exc:
        logger.warning("[SUMMARY] could not read CSV %s: %s", path, exc)
        return None


def _second_to_last_csv_row(path: Path) -> Optional[Dict[str, str]]:
    """Return the second-to-last non-empty row of a CSV, or None."""
    try:
        if not path.exists():
            return None
        rows = []
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if any(v.strip() for v in row.values()):
                    rows.append(row)
        return rows[-2] if len(rows) >= 2 else None
    except Exception as exc:
        logger.warning("[SUMMARY] could not read CSV %s: %s", path, exc)
        return None


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_execution_summary(execution_results: Dict[str, Any]) -> Dict[str, Any]:
    accepted = int(execution_results.get("accepted_count") or 0)
    duplicates = int(execution_results.get("duplicate_count") or 0)
    status = str(execution_results.get("status") or "UNKNOWN")

    responses = execution_results.get("broker_responses") or []
    buy_orders = sum(
        1 for r in responses
        if str(r.get("side", "")).upper() == "BUY"
        and str(r.get("status", "")).upper() in {"ACCEPTED", "IDEMPOTENT_REPLAY"}
    )
    sell_orders = sum(
        1 for r in responses
        if str(r.get("side", "")).upper() in {"SELL", "CLOSE", "REDUCE"}
        and str(r.get("status", "")).upper() in {"ACCEPTED", "IDEMPOTENT_REPLAY"}
    )

    # executed = broker actually has accepted orders (either this run or prior replay)
    executed = accepted > 0 or status == "IDEMPOTENT_REPLAY"

    return {
        "orders_submitted": int(execution_results.get("submitted_count") or 0),
        "orders_accepted": accepted,
        "duplicate_orders": duplicates,
        "buy_orders": buy_orders,
        "sell_orders": sell_orders,
        "executed": executed,
        "status": status,
    }


def _build_portfolio_state(audit: Dict[str, Any]) -> Dict[str, Any]:
    executor = audit.get("executor") or {}
    broker_state = executor.get("broker_state_after") or {}
    cash = _float(broker_state.get("cash"))
    positions = broker_state.get("positions") or []

    market_value: Optional[float] = None
    if positions:
        try:
            total = 0.0
            for p in positions:
                mv = _float(p.get("market_value"))
                if mv is not None:
                    total += mv
                else:
                    price = _float(p.get("current_price") or p.get("avg_entry_price"))
                    qty = _float(p.get("qty"))
                    if price is not None and qty is not None:
                        total += abs(price * qty)
            market_value = round(total, 2) if total else None
        except Exception as exc:
            logger.warning("[SUMMARY] market_value compute failed: %s", exc)

    return {
        "cash_after": round(cash, 2) if cash is not None else None,
        "positions_count": len(positions),
        "portfolio_market_value": market_value,
    }


def _build_email_summary(operator_summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    The system is designed for exactly 3 operator-facing emails per run:
      1. market_conditions  (engine_run)
      2. pre_trade_analysis (engine_run)
      3. trading_confirmation (execute_orders)

    operator_summary.confirmation_email_sent is the only direct signal available.
    If confirmation was sent, the full pipeline completed normally → infer 3.
    """
    if not operator_summary:
        return {"emails_sent": None, "expected": 3, "status": "UNKNOWN", "confirmation_sent": None}

    confirmation_sent = bool(operator_summary.get("confirmation_email_sent"))
    emails_sent: Optional[int] = 3 if confirmation_sent else None
    status = "OK" if emails_sent == 3 else "UNKNOWN" if emails_sent is None else (
        "EMAIL_LOOP_WARNING" if emails_sent > 3 else "INCOMPLETE"
    )

    return {
        "emails_sent": emails_sent,
        "expected": 3,
        "status": status,
        "confirmation_sent": confirmation_sent,
    }


def _build_dashboard(workspace_root: Path) -> Dict[str, Any]:
    candidates = [
        workspace_root / "web" / "dashboard" / "dashboard_data.json",
        workspace_root / "quant_dashboard.html",
        workspace_root / "outputs" / "dashboard" / "dashboard_data.json",
    ]
    generated = any(p.exists() for p in candidates)
    found_path = next((str(p) for p in candidates if p.exists()), None)
    return {"generated": generated, "path": found_path}


def _build_broker_context(run_root: Path, operator_summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    broker_dir = run_root / "broker"
    pretrade_account = _read_json(broker_dir / "pretrade_account_snapshot.json") or {}
    pretrade_positions = _read_json(broker_dir / "pretrade_positions.json") or {}
    posttrade_account = _read_json(broker_dir / "posttrade_account_snapshot.json") or {}
    posttrade_positions = _read_json(broker_dir / "posttrade_positions.json") or {}
    pretrade_account_obj = dict(pretrade_account.get("account") or {})
    preflight = summarize_pretrade_broker_policy({"account": pretrade_account_obj}) if pretrade_account_obj else {}

    return {
        "broker_authoritative_state": bool((operator_summary or {}).get("broker_authoritative_state")),
        "broker_pretrade_snapshot_ok": (operator_summary or {}).get("broker_pretrade_snapshot_ok"),
        "broker_posttrade_snapshot_ok": (operator_summary or {}).get("broker_posttrade_snapshot_ok"),
        "duplicate_guard_status": (operator_summary or {}).get("duplicate_guard_status"),
        "broker_preflight_status": (operator_summary or {}).get("broker_preflight_status") or preflight.get("broker_preflight_status"),
        "broker_preflight_account_status": (operator_summary or {}).get("broker_preflight_account_status") or preflight.get("broker_preflight_account_status"),
        "broker_preflight_cash": (operator_summary or {}).get("broker_preflight_cash") if (operator_summary or {}).get("broker_preflight_cash") is not None else preflight.get("broker_preflight_cash"),
        "broker_preflight_equity": (operator_summary or {}).get("broker_preflight_equity") if (operator_summary or {}).get("broker_preflight_equity") is not None else preflight.get("broker_preflight_equity"),
        "broker_preflight_buying_power": (operator_summary or {}).get("broker_preflight_buying_power") if (operator_summary or {}).get("broker_preflight_buying_power") is not None else preflight.get("broker_preflight_buying_power"),
        "broker_preflight_restriction_flags": dict((operator_summary or {}).get("broker_preflight_restriction_flags") or preflight.get("broker_preflight_restriction_flags") or {}),
        "broker_preflight_warning_flags": list((operator_summary or {}).get("broker_preflight_warning_flags") or preflight.get("broker_preflight_warning_flags") or []),
        "broker_reject_status": (operator_summary or {}).get("broker_reject_status"),
        "broker_reject_message": (operator_summary or {}).get("broker_reject_message"),
        "post_execution_recon_status": (operator_summary or {}).get("post_execution_recon_status"),
        "affected_symbols": list((operator_summary or {}).get("affected_symbols") or []),
        "repair_suggestions": list((operator_summary or {}).get("repair_suggestions") or []),
        "duplicate_fill_suspicions_count": int((operator_summary or {}).get("duplicate_fill_suspicions_count") or 0),
        "pretrade_positions_count": pretrade_positions.get("positions_count"),
        "pretrade_account_status": pretrade_account_obj.get("status"),
        "pretrade_cash": pretrade_account_obj.get("cash"),
        "pretrade_equity": pretrade_account_obj.get("equity") or pretrade_account_obj.get("portfolio_value"),
        "pretrade_buying_power": pretrade_account_obj.get("buying_power"),
        "posttrade_positions_count": posttrade_positions.get("positions_count"),
        "posttrade_cash": posttrade_account.get("cash"),
        "posttrade_equity": posttrade_account.get("equity"),
    }


def _build_benchmark(workspace_root: Path) -> Dict[str, Any]:
    null_result: Dict[str, Any] = {
        "portfolio_value": None,
        "spy_value": None,
        "portfolio_return_pct": None,
        "spy_return_pct": None,
        "performance_vs_spy_pct": None,
    }

    nav_path = workspace_root / "outputs" / "perf" / "nav_timeseries.csv"
    spy_path = workspace_root / "outputs" / "perf" / "benchmark_close_history.csv"

    nav_row = _last_csv_row(nav_path)
    spy_row = _last_csv_row(spy_path)

    if not nav_row and not spy_row:
        return null_result

    portfolio_value = _float(nav_row.get("equity")) if nav_row else None
    portfolio_return_pct: Optional[float] = None
    if nav_row:
        ret_1d = _float(nav_row.get("return_1d"))
        if ret_1d is not None:
            portfolio_return_pct = round(ret_1d * 100, 4)

    spy_value = _float(spy_row.get("spy_close")) if spy_row else None
    spy_return_pct: Optional[float] = None
    if spy_row:
        spy_ret = _float(spy_row.get("spy_return"))
        if spy_ret is not None:
            spy_return_pct = round(spy_ret * 100, 4)

    perf_vs_spy: Optional[float] = None
    if portfolio_return_pct is not None and spy_return_pct is not None:
        perf_vs_spy = round(portfolio_return_pct - spy_return_pct, 4)

    return {
        "portfolio_value": round(portfolio_value, 2) if portfolio_value is not None else None,
        "spy_value": round(spy_value, 2) if spy_value is not None else None,
        "portfolio_return_pct": portfolio_return_pct,
        "spy_return_pct": spy_return_pct,
        "performance_vs_spy_pct": perf_vs_spy,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_trading_day_summary(
    run_root: Path,
    run_id: str,
    trade_date: str,
    *,
    workspace_root: Optional[Path] = None,
    audit_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build the trading day summary dict from existing run artifacts.

    All source reads are non-blocking.  Missing files produce null fields.

    Args:
        run_root:       Path to this run's artifact directory.
        run_id:         Canonical run identifier (raw, unsanitized).
        trade_date:     Trading date YYYY-MM-DD.
        workspace_root: Repo root for locating shared files (defaults to cwd).
        audit_dir:      Override for execution_audit directory.

    Returns:
        Summary dict suitable for JSON serialization.
    """
    ws = workspace_root or Path(".")
    audit_base = audit_dir or Path("outputs/execution_audit")

    # 1. Execution results
    results_path = run_root / "execution_results.json"
    execution_results = _read_json(results_path) or {}

    # 2. Execution audit (broker_state_after lives here)
    safe_id = safe_artifact_name(run_id)
    audit_path = audit_base / f"{safe_id}.json"
    audit = _read_json(audit_path) or {}

    # 3. Operator summary (email signal)
    operator_summary = _read_json(run_root / "operator_summary.json")

    # Build sections
    exec_summary = _build_execution_summary(execution_results)
    portfolio_state = _build_portfolio_state(audit)
    email_summary = _build_email_summary(operator_summary)
    dashboard = _build_dashboard(ws)
    benchmark = _build_benchmark(ws)
    broker_context = _build_broker_context(run_root, operator_summary)

    return {
        "run_id": run_id,
        "trade_date": trade_date,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "execution_summary": exec_summary,
        "portfolio_state": portfolio_state,
        "broker_context": broker_context,
        "email_summary": email_summary,
        "dashboard": dashboard,
        "benchmark": benchmark,
    }


def write_trading_day_summary(
    run_root: Path,
    run_id: str,
    trade_date: str,
    *,
    workspace_root: Optional[Path] = None,
    audit_dir: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Optional[Path]:
    """
    Build and write trading-day summary artifacts.

    Canonical artifact:
        outputs/runs/<RUN_ID>/trading_day_summary.json
    Latest mirror (for local tools like scripts/morning_report.py):
        outputs/trading_day_summary.json (or output_path override)

    Non-blocking: logs a warning and returns None on any failure.

    Returns:
        Canonical per-run path where summary was written, or None on failure.
    """
    try:
        summary = build_trading_day_summary(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            workspace_root=workspace_root,
            audit_dir=audit_dir,
        )
        canonical_dest = Path(run_root) / RUN_SUMMARY_FILENAME
        latest_dest = output_path or SUMMARY_PATH
        payload = json.dumps(summary, indent=2) + "\n"

        canonical_dest.parent.mkdir(parents=True, exist_ok=True)
        canonical_dest.write_text(payload, encoding="utf-8")

        latest_dest.parent.mkdir(parents=True, exist_ok=True)
        latest_dest.write_text(payload, encoding="utf-8")

        logger.info("[SUMMARY] trading_day_summary canonical written: %s", canonical_dest)
        logger.info("[SUMMARY] trading_day_summary latest mirror written: %s", latest_dest)
        return canonical_dest
    except Exception as exc:
        logger.warning("[SUMMARY] failed to write trading_day_summary: %s", exc)
        return None
