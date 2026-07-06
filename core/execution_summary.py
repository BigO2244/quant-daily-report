"""
Execution summary builder and export writer.

Builds deterministic per-run execution summaries from authoritative data sources
and maintains rolling CSV export of execution history for observability.

Non-blocking implementation: failures in summary generation do not halt trading.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# Column order for CSV export (required + optional columns)
EXECUTION_HISTORY_COLUMNS = [
    "date",
    "run_id",
    "buys",
    "sells",
    "total_assets_held",
    "net_asset_value",
    "cash",
    "planner_status",
    "payload_status",
    "execution_status",
    "broker_status",
    "reconciliation_status",
    "signals_generated",
    "planned_payload_trade_count",
    "executable_filter_passed_count",
    "executable_trades_count",
    "final_executable_trades_count",
    "intended_orders_count",
    "orders_submitted",
    "orders_accepted",
    "orders_filled",
    "orders_rejected",
    "candidate_trade_lifecycle_artifact",
    "candidate_trade_lifecycle_reasons",
    "turnover",
    "trade_decision",
    "primary_reason",
]


def _extract_tickers_by_side(
    execution_payload: dict | None,
    side: str,
) -> str:
    """
    Extract and serialize ticker symbols by trade side.
    
    Args:
        execution_payload: Execution payload dict with 'trades' list
        side: Trade side (BUY or SELL)
    
    Returns:
        Pipe-delimited sorted unique ticker string (e.g., "AAPL|MSFT|NVDA")
        or empty string if no trades found.
    """
    if not isinstance(execution_payload, dict):
        return ""
    
    trades = execution_payload.get("trades") or []
    if not isinstance(trades, list):
        return ""
    
    tickers = set()
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        trade_side = str(trade.get("side") or "").upper()
        if trade_side != side.upper():
            continue
        ticker = str(trade.get("ticker") or "").strip().upper()
        if ticker:
            tickers.add(ticker)
    
    return "|".join(sorted(tickers)) if tickers else ""


def _infer_status(
    health_payload: dict | None,
    execution_payload: dict | None,
    paper_summary: dict | None,
    status_field: str,
) -> str:
    """
    Infer best-effort status from available sources.
    
    Checks health, execution, and paper summary payloads in order.
    Returns UNKNOWN if no status can be inferred.
    """
    candidate_payloads = [
        (health_payload, ["status"]),  # health has "status": PASS/FAIL
        (execution_payload, ["status", "execution_status"]),  # execution status
        (paper_summary, ["status"]),  # paper summary status
    ]
    
    for payload, fields in candidate_payloads:
        if not isinstance(payload, dict):
            continue
        for field in fields:
            value = payload.get(field)
            if value:
                return str(value).upper()
    
    return "UNKNOWN"


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _to_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count_filled_responses(responses: list[Any]) -> int | None:
    if not responses:
        return None
    filled_statuses = {"FILLED", "FILLED_ESTIMATE"}
    count = 0
    for raw in responses:
        row = raw if isinstance(raw, dict) else {}
        status = str(row.get("latest_status") or row.get("status") or "").upper()
        try:
            filled_qty = float(row.get("filled_qty") or 0.0)
        except (TypeError, ValueError):
            filled_qty = 0.0
        if status in filled_statuses or filled_qty > 1e-12:
            count += 1
    return count


def _extract_submitted_tickers_by_side(
    execution_payload: dict | None,
    execution_results: dict | None,
    side: str,
) -> str:
    lifecycle_candidates = _as_list(
        _as_dict(execution_results).get("candidate_trade_lifecycle")
        or _as_dict(execution_payload).get("candidate_trade_lifecycle")
    )
    tickers = {
        str(row.get("ticker") or "").strip().upper()
        for row in lifecycle_candidates
        if isinstance(row, dict)
        and bool(row.get("submitted"))
        and str(row.get("side") or "").upper() == side.upper()
        and str(row.get("ticker") or "").strip()
    }
    if lifecycle_candidates:
        return "|".join(sorted(tickers))

    broker_rows = _as_list(_as_dict(execution_results).get("broker_responses"))
    tickers = {
        str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        for row in broker_rows
        if isinstance(row, dict)
        and str(row.get("side") or "").upper() == side.upper()
        and str(row.get("ticker") or row.get("symbol") or "").strip()
    }
    if broker_rows:
        return "|".join(sorted(tickers))

    return _extract_tickers_by_side(execution_payload, side)


def _candidate_lifecycle_reason_summary(
    execution_payload: dict | None,
    execution_results: dict | None,
) -> str:
    lifecycle_candidates = _as_list(
        _as_dict(execution_results).get("candidate_trade_lifecycle")
        or _as_dict(execution_payload).get("candidate_trade_lifecycle")
    )
    parts: list[str] = []
    for raw in lifecycle_candidates:
        row = raw if isinstance(raw, dict) else {}
        reason = str(row.get("decision_reason") or row.get("suppression_or_clipping_reason") or "").strip()
        ticker = str(row.get("ticker") or "").strip().upper()
        side = str(row.get("side") or "").strip().upper()
        if ticker and side and reason:
            parts.append(f"{ticker} {side}:{reason}")
    return "|".join(parts)


def build_execution_summary(
    run_id: str,
    trade_date: str,
    execution_payload: dict | None = None,
    paper_summary: dict | None = None,
    execution_results: dict | None = None,
    health_payload: dict | None = None,
    nav_snapshot: dict | None = None,
    reconciliation_result: dict | None = None,
) -> dict:
    """
    Build a deterministic execution summary from available sources.
    
    Args:
        run_id: Unique run identifier
        trade_date: Trading date (YYYY-MM-DD format)
        execution_payload: Execution payload from planner/executor
        paper_summary: Paper trading summary
        execution_results: Broker execution results artifact
        health_payload: Health check result
        nav_snapshot: NAV snapshot with equity/cash/holdings
        reconciliation_result: Reconciliation result
    
    Returns:
        Dict with summary fields (deterministic, non-blocking on missing data)
    """
    execution_results_safe = execution_results or {}

    # Extract submitted tickers when broker/lifecycle data is available; fall
    # back to the execution payload for pre-broker/no-action runs.
    buys = _extract_submitted_tickers_by_side(execution_payload, execution_results_safe, "BUY")
    sells = _extract_submitted_tickers_by_side(execution_payload, execution_results_safe, "SELL")
    
    # Extract counts from execution payload
    trades_list = (execution_payload or {}).get("trades") or []
    buy_count = sum(1 for t in trades_list if isinstance(t, dict) and str(t.get("side", "")).upper() == "BUY")
    sell_count = sum(1 for t in trades_list if isinstance(t, dict) and str(t.get("side", "")).upper() == "SELL")
    
    # Holdings and NAV (prefer nav_snapshot if available)
    nav_snap = nav_snapshot or {}
    total_assets_held = nav_snap.get("position_count")
    if total_assets_held is None:
        # Fallback: count non-cash holdings from nav snapshot
        holdings = nav_snap.get("holdings") or []
        if isinstance(holdings, list):
            total_assets_held = len([h for h in holdings if str(h.get("ticker", "")).upper() != "CASH"])
    
    net_asset_value = nav_snap.get("equity")
    cash = nav_snap.get("cash")
    
    # Turnover from nav snapshot or paper summary
    turnover = nav_snap.get("turnover_pct") or (paper_summary or {}).get("turnover_pct")
    if turnover is not None:
        turnover = f"{float(turnover) * 100:.2f}%" if isinstance(turnover, (int, float)) else str(turnover)
    
    # Signals generated (from paper summary)
    paper_summary_safe = paper_summary or {}
    signals_generated = len((paper_summary_safe.get("signals", []) or []))
    
    lifecycle_summary = _as_dict(
        execution_results_safe.get("candidate_trade_lifecycle_summary")
        or _as_dict(execution_payload).get("candidate_trade_lifecycle_summary")
        or paper_summary_safe.get("candidate_trade_lifecycle_summary")
    )
    submission_summary = _as_dict(paper_summary_safe.get("alpaca_submission_summary"))
    execution_filter = _as_dict(paper_summary_safe.get("execution_filter"))

    planned_payload_trade_count = _to_int_or_none(
        _first_present(
            execution_results_safe.get("planned_payload_trade_count"),
            _as_dict(execution_payload).get("planned_payload_trade_count"),
            paper_summary_safe.get("planned_payload_trade_count"),
            lifecycle_summary.get("precompute_candidates"),
        )
    )
    final_executable_trades_count = _to_int_or_none(
        _first_present(
            execution_results_safe.get("executable_trades_count"),
            _as_dict(execution_payload).get("execution_eligible_trades_count"),
            _as_dict(execution_payload).get("executable_trades_count"),
        )
    )
    executable_filter_passed_count = _to_int_or_none(
        _first_present(
            lifecycle_summary.get("passed_executable_filter"),
            execution_filter.get("kept"),
            final_executable_trades_count,
        )
    )
    executable_trades_count = final_executable_trades_count
    intended_orders_count = _to_int_or_none(
        _first_present(
            execution_results_safe.get("intended_orders_count"),
            _as_dict(execution_payload).get("intended_orders_count"),
            lifecycle_summary.get("intended_orders"),
            submission_summary.get("initial_intended_orders"),
            _as_dict(execution_payload).get("planner_intended_trades_count"),
        )
    )
    lifecycle_reason_summary = _candidate_lifecycle_reason_summary(execution_payload, execution_results_safe)

    # Orders submitted/accepted/filled/rejected (from execution results first).
    health_safe = health_payload or {}
    orders_submitted = _to_int_or_none(
        _first_present(
            execution_results_safe.get("submitted_count"),
            execution_results_safe.get("orders_submitted_count"),
            _as_dict(execution_payload).get("submitted_count"),
            _as_dict(execution_payload).get("orders_submitted_count"),
            submission_summary.get("submit_success"),
            health_safe.get("orders_submitted_count"),
        )
    )
    orders_accepted = _to_int_or_none(
        _first_present(
            execution_results_safe.get("accepted_count"),
            _as_dict(execution_payload).get("accepted_count"),
            lifecycle_summary.get("accepted"),
            orders_submitted,
        )
    )
    orders_filled = _to_int_or_none(
        _first_present(
            execution_results_safe.get("orders_filled_count"),
            execution_results_safe.get("filled_count"),
            _as_dict(execution_payload).get("orders_filled_count"),
            paper_summary_safe.get("orders_filled_count"),
            lifecycle_summary.get("filled"),
            _count_filled_responses(_as_list(execution_results_safe.get("broker_responses"))),
            health_safe.get("executed_trade_count"),
        )
    )
    orders_rejected = _to_int_or_none(
        _first_present(
            execution_results_safe.get("rejected_count"),
            _as_dict(execution_payload).get("rejected_count"),
            submission_summary.get("submit_failed"),
        )
    )
    
    # Status inference
    planner_status = _infer_status(
        health_payload, execution_payload, paper_summary, "planner_status"
    )
    if health_safe.get("error"):
        planner_status = "FAILED"
    elif execution_payload and int((execution_payload or {}).get("executable_trades_count", 0)) > 0:
        planner_status = "SUCCESS"
    else:
        planner_status = planner_status if planner_status != "UNKNOWN" else "SUCCESS"  # Assume success if no error
    
    payload_status = "CREATED" if execution_payload else "MISSING"
    
    execution_payload_status = _first_present(
        execution_results_safe.get("status"),
        (execution_payload or {}).get("status"),
        (execution_payload or {}).get("execution_status"),
        "UNKNOWN",
    )
    execution_payload_status_upper = str(execution_payload_status).upper()
    if execution_payload_status_upper in {"EXECUTED", "RECONCILED_SUCCESS", "SUCCESS"}:
        execution_status = "SUCCESS"
    elif execution_payload_status_upper in ("HALTED", "SKIPPED_DUPLICATE", "NO_ACTION", "PARTIAL"):
        execution_status = execution_payload_status_upper
    elif health_safe.get("status") == "FAIL":
        execution_status = "FAILED"
    else:
        execution_status = "UNKNOWN"
    
    broker_status = "OK" if not health_safe.get("error") else "ERROR"
    
    reconciliation_data = reconciliation_result or {}
    recon_status = "PASS" if not health_safe.get("error") else "FAIL"
    
    # Trade decision
    trade_decision = "UNKNOWN"
    if execution_payload or execution_results_safe:
        exec_status = str(
            (execution_results_safe or {}).get("status")
            or (execution_payload or {}).get("status")
            or (execution_payload or {}).get("execution_status")
            or ""
        ).upper()
        if exec_status in {"EXECUTED", "RECONCILED_SUCCESS", "SUCCESS"} or int(orders_submitted or 0) > 0:
            trade_decision = "EXECUTED"
        elif exec_status in ("HALTED", "BLOCKED"):
            trade_decision = "EXECUTION_BLOCKED"
        elif int(executable_filter_passed_count or executable_trades_count or 0) == 0:
            trade_decision = "NO_TRADES_MODEL"
        else:
            trade_decision = "UNKNOWN"
    
    # Primary reason (best-effort)
    primary_reason = "UNKNOWN"
    if execution_payload:
        halt_reason = execution_payload.get("halt_reason")
        if halt_reason:
            primary_reason = str(halt_reason).lower()
        elif int(orders_submitted or 0) > 0:
            primary_reason = "orders_submitted"
        elif lifecycle_summary.get("suppressed"):
            primary_reason = "candidate_suppression"
        elif int(executable_filter_passed_count or executable_trades_count or 0) > 0:
            primary_reason = "executable_not_submitted"
        else:
            primary_reason = execution_payload.get("status_reason") or "unknown"
    
    return {
        "run_id": str(run_id),
        "date": str(trade_date),
        "buys": buys,
        "sells": sells,
        "buys_count": int(buy_count),
        "sells_count": int(sell_count),
        "total_assets_held": total_assets_held,
        "net_asset_value": net_asset_value,
        "cash": cash,
        "turnover": turnover,
        "planner_status": planner_status,
        "payload_status": payload_status,
        "execution_status": execution_status,
        "broker_status": broker_status,
        "reconciliation_status": recon_status,
        "signals_generated": signals_generated,
        "planned_payload_trade_count": planned_payload_trade_count,
        "executable_filter_passed_count": executable_filter_passed_count,
        "executable_trades_count": executable_trades_count,
        "final_executable_trades_count": final_executable_trades_count,
        "intended_orders_count": intended_orders_count,
        "orders_submitted": orders_submitted,
        "orders_accepted": orders_accepted,
        "orders_filled": orders_filled,
        "orders_rejected": orders_rejected,
        "candidate_trade_lifecycle_artifact": _first_present(
            _as_dict(execution_payload).get("candidate_trade_lifecycle_artifact"),
            execution_results_safe.get("candidate_trade_lifecycle_artifact"),
            paper_summary_safe.get("candidate_trade_lifecycle_artifact"),
            lifecycle_summary.get("artifact_path"),
        ),
        "candidate_trade_lifecycle_reasons": lifecycle_reason_summary,
        "trade_decision": trade_decision,
        "primary_reason": primary_reason,
    }


def write_execution_summary_text(
    summary: dict,
    run_dir: Path,
) -> Path:
    """
    Write per-run execution summary text artifact.
    
    Args:
        summary: Summary dict from build_execution_summary()
        run_dir: Run directory path (e.g., outputs/runs/{run_id}/)
    
    Returns:
        Path where summary was written
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    
    summary_path = run_dir / "execution_summary.txt"
    
    lines = [
        f"RUN_ID: {summary.get('run_id', 'UNKNOWN')}",
        f"DATE: {summary.get('date', 'UNKNOWN')}",
        "",
        "PIPELINE STATUS",
        f"  Planner: {summary.get('planner_status', 'UNKNOWN')}",
        f"  Execution Payload: {summary.get('payload_status', 'UNKNOWN')}",
        f"  Executor: {summary.get('execution_status', 'UNKNOWN')}",
        f"  Broker: {summary.get('broker_status', 'UNKNOWN')}",
        f"  Reconciliation: {summary.get('reconciliation_status', 'UNKNOWN')}",
        "",
        "TRADING SUMMARY",
        f"  Buys: {summary.get('buys') or '(none)'}",
        f"  Sells: {summary.get('sells') or '(none)'}",
        f"  Signals Generated: {summary.get('signals_generated', 'UNKNOWN')}",
        f"  Planned Payload Trades: {summary.get('planned_payload_trade_count', 'UNKNOWN')}",
        f"  Executable Filter Passed: {summary.get('executable_filter_passed_count', 'UNKNOWN')}",
        f"  Intended Orders: {summary.get('intended_orders_count', 'UNKNOWN')}",
        f"  Final Executable Trades: {summary.get('final_executable_trades_count', 'UNKNOWN')}",
        f"  Orders Submitted: {summary.get('orders_submitted', 'UNKNOWN')}",
        f"  Orders Accepted: {summary.get('orders_accepted', 'UNKNOWN')}",
        f"  Orders Filled: {summary.get('orders_filled', 'UNKNOWN')}",
        f"  Orders Rejected: {summary.get('orders_rejected', 'UNKNOWN')}",
        f"  Candidate Lifecycle: {summary.get('candidate_trade_lifecycle_artifact') or '(none)'}",
        f"  Candidate Lifecycle Reasons: {summary.get('candidate_trade_lifecycle_reasons') or '(none)'}",
        "",
        "PORTFOLIO SNAPSHOT",
        f"  Total Assets Held: {summary.get('total_assets_held', 'UNKNOWN')}",
        f"  Net Asset Value: ${summary.get('net_asset_value', 'UNKNOWN'):,.2f}" if isinstance(summary.get('net_asset_value'), (int, float)) else f"  Net Asset Value: {summary.get('net_asset_value', 'UNKNOWN')}",
        f"  Cash: ${summary.get('cash', 'UNKNOWN'):,.2f}" if isinstance(summary.get('cash'), (int, float)) else f"  Cash: {summary.get('cash', 'UNKNOWN')}",
        f"  Turnover: {summary.get('turnover', 'UNKNOWN')}",
        "",
        "DECISION",
        f"  Trade Decision: {summary.get('trade_decision', 'UNKNOWN')}",
        f"  Primary Reason: {summary.get('primary_reason', 'UNKNOWN')}",
    ]
    
    text_content = "\n".join(lines) + "\n"
    summary_path.write_text(text_content, encoding="utf-8")
    logger.info("[EXECUTION_SUMMARY] wrote %s", summary_path)
    return summary_path


def write_execution_summary_csv(
    summary: dict,
    run_dir: Path,
) -> Path:
    """
    Write per-run execution summary CSV artifact.

    Args:
        summary: Summary dict from build_execution_summary()
        run_dir: Run directory path (e.g., outputs/runs/{run_id}/)

    Returns:
        Path where CSV was written
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    csv_path = run_dir / "execution_summary.csv"
    row_data = {
        col: ("" if summary.get(col) is None else summary.get(col))
        for col in EXECUTION_HISTORY_COLUMNS
    }

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXECUTION_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerow(row_data)

    logger.info("[EXECUTION_SUMMARY] wrote %s", csv_path)
    return csv_path


def write_latest_execution_summary(
    summary: dict,
) -> Path:
    """
    Write latest execution summary pointer copy to outputs/latest_execution_summary.txt.
    
    Args:
        summary: Summary dict from build_execution_summary()
    
    Returns:
        Path where latest summary was written
    """
    latest_path = Path("outputs") / "latest_execution_summary.txt"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    
    lines = [
        f"RUN_ID: {summary.get('run_id', 'UNKNOWN')}",
        f"DATE: {summary.get('date', 'UNKNOWN')}",
        "",
        "PIPELINE STATUS",
        f"  Planner: {summary.get('planner_status', 'UNKNOWN')}",
        f"  Execution Payload: {summary.get('payload_status', 'UNKNOWN')}",
        f"  Executor: {summary.get('execution_status', 'UNKNOWN')}",
        f"  Broker: {summary.get('broker_status', 'UNKNOWN')}",
        f"  Reconciliation: {summary.get('reconciliation_status', 'UNKNOWN')}",
        "",
        "TRADING SUMMARY",
        f"  Buys: {summary.get('buys') or '(none)'}",
        f"  Sells: {summary.get('sells') or '(none)'}",
        f"  Signals Generated: {summary.get('signals_generated', 'UNKNOWN')}",
        f"  Planned Payload Trades: {summary.get('planned_payload_trade_count', 'UNKNOWN')}",
        f"  Executable Filter Passed: {summary.get('executable_filter_passed_count', 'UNKNOWN')}",
        f"  Intended Orders: {summary.get('intended_orders_count', 'UNKNOWN')}",
        f"  Final Executable Trades: {summary.get('final_executable_trades_count', 'UNKNOWN')}",
        f"  Orders Submitted: {summary.get('orders_submitted', 'UNKNOWN')}",
        f"  Orders Accepted: {summary.get('orders_accepted', 'UNKNOWN')}",
        f"  Orders Filled: {summary.get('orders_filled', 'UNKNOWN')}",
        f"  Orders Rejected: {summary.get('orders_rejected', 'UNKNOWN')}",
        f"  Candidate Lifecycle: {summary.get('candidate_trade_lifecycle_artifact') or '(none)'}",
        f"  Candidate Lifecycle Reasons: {summary.get('candidate_trade_lifecycle_reasons') or '(none)'}",
        "",
        "PORTFOLIO SNAPSHOT",
        f"  Total Assets Held: {summary.get('total_assets_held', 'UNKNOWN')}",
        f"  Net Asset Value: ${summary.get('net_asset_value', 'UNKNOWN'):,.2f}" if isinstance(summary.get('net_asset_value'), (int, float)) else f"  Net Asset Value: {summary.get('net_asset_value', 'UNKNOWN')}",
        f"  Cash: ${summary.get('cash', 'UNKNOWN'):,.2f}" if isinstance(summary.get('cash'), (int, float)) else f"  Cash: {summary.get('cash', 'UNKNOWN')}",
        f"  Turnover: {summary.get('turnover', 'UNKNOWN')}",
        "",
        "DECISION",
        f"  Trade Decision: {summary.get('trade_decision', 'UNKNOWN')}",
        f"  Primary Reason: {summary.get('primary_reason', 'UNKNOWN')}",
    ]
    
    text_content = "\n".join(lines) + "\n"
    latest_path.write_text(text_content, encoding="utf-8")
    logger.info("[EXECUTION_SUMMARY] wrote latest: %s", latest_path)
    return latest_path


def update_execution_history_csv(
    summary: dict,
    csv_path: str | Path = "outputs/execution_history.csv",
) -> Path:
    """
    Create or append/update rolling CSV export of execution history.
    
    If row with same run_id exists, updates it (replace semantics).
    Otherwise appends new row.
    
    Args:
        summary: Summary dict from build_execution_summary()
        csv_path: Path to execution_history.csv
    
    Returns:
        Path to CSV file
    """
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    
    run_id = str(summary.get("run_id", ""))
    
    # Build row with required + optional columns
    row_data = {
        "date": str(summary.get("date", "")),
        "run_id": run_id,
        "buys": str(summary.get("buys", "")),
        "sells": str(summary.get("sells", "")),
        "total_assets_held": summary.get("total_assets_held"),
        "net_asset_value": summary.get("net_asset_value"),
        "cash": summary.get("cash"),
        "planner_status": str(summary.get("planner_status", "")),
        "payload_status": str(summary.get("payload_status", "")),
        "execution_status": str(summary.get("execution_status", "")),
        "broker_status": str(summary.get("broker_status", "")),
        "reconciliation_status": str(summary.get("reconciliation_status", "")),
        "signals_generated": summary.get("signals_generated"),
        "planned_payload_trade_count": summary.get("planned_payload_trade_count"),
        "executable_filter_passed_count": summary.get("executable_filter_passed_count"),
        "executable_trades_count": summary.get("executable_trades_count"),
        "final_executable_trades_count": summary.get("final_executable_trades_count"),
        "intended_orders_count": summary.get("intended_orders_count"),
        "orders_submitted": summary.get("orders_submitted"),
        "orders_accepted": summary.get("orders_accepted"),
        "orders_filled": summary.get("orders_filled"),
        "orders_rejected": summary.get("orders_rejected"),
        "candidate_trade_lifecycle_artifact": str(summary.get("candidate_trade_lifecycle_artifact") or ""),
        "candidate_trade_lifecycle_reasons": str(summary.get("candidate_trade_lifecycle_reasons") or ""),
        "turnover": str(summary.get("turnover", "")),
        "trade_decision": str(summary.get("trade_decision", "")),
        "primary_reason": str(summary.get("primary_reason", "")),
    }
    
    # Read existing data
    existing_rows: list[dict] = []
    if csv_path.exists() and csv_path.stat().st_size > 0:
        try:
            df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
            existing_rows = df.to_dict(orient="records")
        except Exception as e:
            logger.warning("[EXECUTION_HISTORY] failed to read CSV, treating as empty: %s", e)
    
    # Check if run_id already exists (for update semantics)
    found_index = -1
    for i, row in enumerate(existing_rows):
        if str(row.get("run_id", "")) == run_id:
            found_index = i
            break
    
    if found_index >= 0:
        # Update existing row
        existing_rows[found_index] = row_data
        logger.info("[EXECUTION_HISTORY] updated existing row for run_id=%s", run_id)
    else:
        # Append new row
        existing_rows.append(row_data)
        logger.info("[EXECUTION_HISTORY] appended new row for run_id=%s", run_id)
    
    # Sort by date then run_id for stable output
    try:
        df_out = pd.DataFrame(existing_rows)
        # Ensure date column is properly ordered
        df_out["date"] = pd.to_datetime(
            df_out["date"], errors="coerce", format="%Y-%m-%d"
        )
        df_out = df_out.sort_values(["date", "run_id"], na_position="last").reset_index(drop=True)
        df_out["date"] = df_out["date"].dt.strftime("%Y-%m-%d")
    except Exception as e:
        logger.warning("[EXECUTION_HISTORY] failed to sort, writing unsorted: %s", e)
        df_out = pd.DataFrame(existing_rows)
    
    # Write with all columns in defined order
    csv_columns = [c for c in EXECUTION_HISTORY_COLUMNS if c in df_out.columns]
    # Add any extra columns that might be in the dataframe
    for col in df_out.columns:
        if col not in csv_columns:
            csv_columns.append(col)
    
    df_out[csv_columns].to_csv(csv_path, index=False)
    logger.info("[EXECUTION_HISTORY] wrote CSV: %s rows=%d", csv_path, len(df_out))
    return csv_path


def write_execution_artifacts(
    run_id: str,
    trade_date: str,
    run_dir: Path,
    execution_payload: dict | None = None,
    paper_summary: dict | None = None,
    execution_results: dict | None = None,
    health_payload: dict | None = None,
    nav_snapshot: dict | None = None,
    reconciliation_result: dict | None = None,
) -> dict[str, Path | str]:
    """
    Main entry point: build summary and write all artifacts (non-blocking).
    
    Args:
        run_id: Unique run identifier
        trade_date: Trading date (YYYY-MM-DD)
        run_dir: Run directory path
        execution_payload: Execution payload dict
        paper_summary: Paper summary dict  
        execution_results: Broker execution results artifact
        health_payload: Health check payload
        nav_snapshot: NAV snapshot
        reconciliation_result: Reconciliation result
    
    Returns:
        Dict with paths to written artifacts (summary_path, latest_path, csv_path)
    
    This function is non-blocking: failures in any single artifact write
    log warnings but do not raise exceptions.
    """
    run_dir = Path(run_dir)
    results = {}
    if execution_results is None:
        results_path = run_dir / "execution_results.json"
        try:
            if results_path.exists():
                loaded_results = json.loads(results_path.read_text(encoding="utf-8"))
                if isinstance(loaded_results, dict):
                    execution_results = loaded_results
        except Exception as exc:
            logger.warning("[EXECUTION_SUMMARY] failed to read execution_results: %s", exc)
    
    try:
        # Build master summary
        summary = build_execution_summary(
            run_id=run_id,
            trade_date=trade_date,
            execution_payload=execution_payload,
            paper_summary=paper_summary,
            execution_results=execution_results,
            health_payload=health_payload,
            nav_snapshot=nav_snapshot,
            reconciliation_result=reconciliation_result,
        )
        results["summary"] = summary
        
        # Write per-run text summary
        try:
            summary_path = write_execution_summary_text(summary, run_dir)
            results["summary_path"] = str(summary_path)
            logger.info("[EXECUTION_SUMMARY] per-run artifact written successfully")
        except Exception as e:
            logger.warning("[EXECUTION_SUMMARY] failed to write per-run summary: %s", e)
            results["summary_path_error"] = str(e)

        # Write per-run CSV summary
        try:
            csv_run_path = write_execution_summary_csv(summary, run_dir)
            results["csv_run_path"] = str(csv_run_path)
            logger.info("[EXECUTION_SUMMARY] per-run CSV artifact written successfully")
        except Exception as e:
            logger.warning("[EXECUTION_SUMMARY] failed to write per-run CSV summary: %s", e)
            results["csv_run_path_error"] = str(e)

        # Write latest pointer copy
        try:
            latest_path = write_latest_execution_summary(summary)
            results["latest_path"] = str(latest_path)
            logger.info("[EXECUTION_SUMMARY] latest pointer written successfully")
        except Exception as e:
            logger.warning("[EXECUTION_SUMMARY] failed to write latest pointer: %s", e)
            results["latest_path_error"] = str(e)
        
        # Update rolling CSV
        try:
            csv_path = update_execution_history_csv(summary)
            results["csv_path"] = str(csv_path)
            logger.info("[EXECUTION_SUMMARY] CSV export updated successfully")
        except Exception as e:
            logger.warning("[EXECUTION_SUMMARY] failed to update CSV export: %s", e)
            results["csv_path_error"] = str(e)
        
    except Exception as e:
        logger.warning("[EXECUTION_SUMMARY] critical failure in summary builder: %s", e)
        results["builder_error"] = str(e)
    
    return results
