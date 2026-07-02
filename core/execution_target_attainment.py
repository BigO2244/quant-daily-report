from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "execution_target_attainment.v1"

OK_TARGET_ATTAINED = "OK_TARGET_ATTAINED"
WARN_CASH_DRIFT = "WARN_CASH_DRIFT"
WARN_RECONCILED_BUT_UNDERDEPLOYED = "WARN_RECONCILED_BUT_UNDERDEPLOYED"
WARN_UNDERDEPLOYED_PENDING_BUY_FILLS = "WARN_UNDERDEPLOYED_PENDING_BUY_FILLS"
WARN_POSTTRADE_SNAPSHOT_STALE_OR_PRE_BUY = "WARN_POSTTRADE_SNAPSHOT_STALE_OR_PRE_BUY"
FAIL_EXECUTION_INCOMPLETE = "FAIL_EXECUTION_INCOMPLETE"
UNKNOWN_INSUFFICIENT_ARTIFACTS = "UNKNOWN_INSUFFICIENT_ARTIFACTS"

UNDERDEPLOYMENT_NONE = "none"
UNDERDEPLOYMENT_PENDING_INCOMPLETE_FILL = "pending_incomplete_fill_timing"
UNDERDEPLOYMENT_AVOIDABLE_REBUDGET = "avoidable_rebudget_underdeployment"
UNDERDEPLOYMENT_BROKER_RISK_BLOCKED = "legitimate_broker_or_risk_blocked_underdeployment"
UNDERDEPLOYMENT_REPORTING_MISMATCH = "reporting_accounting_mismatch"

DEFAULT_CASH_WEIGHT_DRIFT_TOLERANCE = 0.02
DEFAULT_NOTIONAL_DRIFT_TOLERANCE_FLOOR = 25.0
DEFAULT_NOTIONAL_DRIFT_TOLERANCE_EQUITY_PCT = 0.0025

_RECONCILED_STATUSES = {"OK", "PASS", "OK_RECONCILED", "RECONCILED", "SUCCESS"}
_INCOMPLETE_EXECUTION_STATUSES = {"FAILED", "HALTED", "PARTIAL", "ERROR"}
_FILLED_STATUSES = {"FILLED", "PARTIALLY_FILLED"}
_PENDING_STATUSES = {"PENDING_NEW", "NEW", "ACCEPTED", "PENDING_REPLACE", "PENDING_CANCEL"}
_SELL_SIDES = {"SELL", "CLOSE", "REDUCE"}
_BUY_INCOMPLETE_PHASE_STATUSES = {"BUY_PHASE_PARTIAL", "BUY_PHASE_TIMEOUT"}
_HARD_CONSTRAINT_REASONS = {
    "buy_blocked_insufficient_buying_power",
    "buy_blocked_risk_cash_target",
    "buy_blocked_asset_validation_failed",
    "buy_blocked_pending_sells_required_for_cash",
    "min_trade_dollars",
    "min_trade_dollars_after_budget_clip",
    "whole_share_sub_one_drop",
    "max_trades_per_day",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _to_int(value: Any) -> int:
    numeric = _to_float(value)
    return int(numeric) if numeric is not None else 0


def _round(value: Any, places: int = 6) -> float | None:
    numeric = _to_float(value)
    return round(numeric, places) if numeric is not None else None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _order_side(order: Mapping[str, Any]) -> str:
    return _upper(order.get("side") or order.get("action"))


def _order_symbol(order: Mapping[str, Any]) -> str:
    return _upper(order.get("ticker") or order.get("symbol"))


def _order_qty(order: Mapping[str, Any]) -> float | None:
    value = order.get("quantity")
    if value is None:
        value = order.get("shares")
    if value is None:
        value = order.get("qty")
    return _to_float(value)


def _order_price(order: Mapping[str, Any]) -> float | None:
    return _to_float(
        order.get("price")
        if order.get("price") is not None
        else order.get("entry_price")
        if order.get("entry_price") is not None
        else order.get("filled_avg_price")
        if order.get("filled_avg_price") is not None
        else order.get("average_price")
    )


def _order_notional(order: Mapping[str, Any], *, filled: bool = False) -> float:
    notional = _to_float(order.get("filled_notional") if filled else order.get("notional"))
    if notional is not None:
        return abs(notional)
    qty = _to_float(order.get("filled_quantity") if filled else None)
    if qty is None:
        qty = _to_float(order.get("filled_qty") if filled else None)
    if qty is None:
        qty = _order_qty(order)
    price = _order_price(order)
    if qty is None or price is None:
        return 0.0
    return abs(qty * price)


def _side_matches(order: Mapping[str, Any], side: str) -> bool:
    actual = _order_side(order)
    expected = side.upper()
    if expected == "SELL":
        return actual in _SELL_SIDES
    return actual == expected


def _side_orders(rows: list[Any], side: str) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if isinstance(row, Mapping) and _side_matches(row, side)]


def _notional(rows: list[Any], side: str, *, filled: bool = False) -> float:
    return round(sum(_order_notional(row, filled=filled) for row in _side_orders(rows, side)), 6)


def _filled_count(rows: list[Any], side: str) -> int:
    count = 0
    for row in _side_orders(rows, side):
        status = _normalized_order_status(row)
        filled_qty = _to_float(row.get("filled_quantity") if row.get("filled_quantity") is not None else row.get("filled_qty"))
        if status in _FILLED_STATUSES or (filled_qty is not None and filled_qty > 0.0):
            count += 1
    return count


def _pending_count(rows: list[Any], side: str) -> int:
    count = 0
    for row in _side_orders(rows, side):
        status = _normalized_order_status(row)
        filled_qty = _to_float(row.get("filled_quantity") if row.get("filled_quantity") is not None else row.get("filled_qty"))
        if status in _PENDING_STATUSES and float(filled_qty or 0.0) <= 0.0:
            count += 1
    return count


def _partial_count(rows: list[Any], side: str) -> int:
    count = 0
    for row in _side_orders(rows, side):
        status = _normalized_order_status(row)
        filled_qty = _to_float(row.get("filled_quantity") if row.get("filled_quantity") is not None else row.get("filled_qty"))
        submitted_qty = _order_qty(row)
        if status == "PARTIALLY_FILLED":
            count += 1
            continue
        if (
            filled_qty is not None
            and submitted_qty is not None
            and filled_qty > 1e-12
            and filled_qty + max(1e-8, submitted_qty * 1e-6) < submitted_qty
        ):
            count += 1
    return count


def _normalized_order_status(order: Mapping[str, Any]) -> str:
    status = _upper(order.get("resolved_order_status") or order.get("latest_status") or order.get("status"))
    return status.split(".")[-1] if "." in status else status


def _timestamp(payload: Mapping[str, Any]) -> str | None:
    for key in ("captured_at", "timestamp", "updated_at", "created_at", "asof", "as_of", "generated_at"):
        if str(payload.get(key) or "").strip():
            return str(payload.get(key))
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _seconds_between(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_timestamp(start)
    end_dt = _parse_timestamp(end)
    if start_dt is None or end_dt is None:
        return None
    return (end_dt - start_dt).total_seconds()


def _reconciliation_status(
    recon: Mapping[str, Any],
    operator_summary: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
) -> str | None:
    for value in (
        recon.get("drift_status"),
        recon.get("comparison_status"),
        recon.get("status"),
        operator_summary.get("post_execution_recon_status"),
        execution_payload.get("posttrade_recon_status"),
    ):
        if str(value or "").strip():
            return str(value)
    return None


def _reconciliation_passed(status: str | None) -> bool:
    return _upper(status) in _RECONCILED_STATUSES


def _execution_complete(
    *,
    execution_status: str | None,
    submitted_count: int,
    accepted_count: int,
    rejected_count: int,
) -> bool:
    if rejected_count > 0:
        return False
    if submitted_count != accepted_count:
        return False
    return _upper(execution_status) not in _INCOMPLETE_EXECUTION_STATUSES


def _run_dirs(outputs_root: Path, trade_date: str | None) -> list[Path]:
    root = outputs_root / "runs"
    if not root.exists():
        return []
    pattern = f"{trade_date}T*" if trade_date else "*"
    return sorted(
        [
            path
            for path in root.glob(pattern)
            if path.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}T", path.name)
        ],
        key=lambda item: item.name,
        reverse=True,
    )


def _select_run_dir(outputs_root: Path, *, trade_date: str | None, run_id: str | None) -> Path | None:
    if run_id:
        exact = outputs_root / "runs" / run_id
        if exact.is_dir():
            return exact
        matches = [path for path in _run_dirs(outputs_root, trade_date) if path.name == run_id]
        if matches:
            return matches[0]
    candidates = _run_dirs(outputs_root, trade_date)
    return candidates[0] if candidates else None


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _latest_rebudget_path(run_root: Path, trade_date: str | None) -> Path:
    if trade_date:
        return run_root / "broker" / f"post_sell_rebudget_{trade_date}.json"
    candidates = sorted((run_root / "broker").glob("post_sell_rebudget_*.json"), key=lambda item: item.name, reverse=True)
    return candidates[0] if candidates else run_root / "broker" / "post_sell_rebudget_UNKNOWN.json"


def _source_probe(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
    }


def _cash_source(
    *,
    posttrade_account: Mapping[str, Any],
    posttrade_account_path: Path,
    recon: Mapping[str, Any],
    recon_path: Path,
    post_sell_rebudget: Mapping[str, Any],
    rebudget_path: Path,
) -> dict[str, Any]:
    if posttrade_account and _to_float(posttrade_account.get("cash")) is not None:
        return {
            "cash": _to_float(posttrade_account.get("cash")),
            "equity": _to_float(posttrade_account.get("equity") or posttrade_account.get("portfolio_value")),
            "source": "posttrade_account_snapshot",
            "path": str(posttrade_account_path),
            "timestamp": _timestamp(posttrade_account),
        }
    if recon and _to_float(recon.get("broker_cash")) is not None:
        return {
            "cash": _to_float(recon.get("broker_cash")),
            "equity": _to_float(recon.get("broker_equity")),
            "source": "reconciliation",
            "path": str(recon_path),
            "timestamp": _timestamp(recon),
        }
    return {
        "cash": _to_float(post_sell_rebudget.get("ending_cash")),
        "equity": _to_float(post_sell_rebudget.get("post_sell_equity")),
        "source": "post_sell_rebudget_ending_cash",
        "path": str(rebudget_path),
        "timestamp": _timestamp(post_sell_rebudget),
    }


def _order_key(order: Mapping[str, Any]) -> str:
    return f"{_order_symbol(order)}:{_order_side(order)}:{_order_qty(order)}"


def _symbol_side_key(order: Mapping[str, Any]) -> str:
    return f"{_order_symbol(order)}:{_order_side(order)}"


def _reason_from_order(order: Mapping[str, Any], default: str = "") -> str:
    for key in (
        "reason",
        "reason_code",
        "block_reason",
        "skip_reason",
        "decision_reason",
        "suppression_or_clipping_reason",
    ):
        value = str(order.get(key) or "").strip()
        if value:
            return value
    return default


def _intended_buys_by_symbol_side(intended_orders: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _symbol_side_key(order): order
        for order in _side_orders(_as_list(intended_orders.get("orders_intended")), "BUY")
    }


def _resized_intended_buys(
    intended_orders: Mapping[str, Any],
    post_sell_rebudget: Mapping[str, Any],
) -> list[dict[str, Any]]:
    intended_by_key = _intended_buys_by_symbol_side(intended_orders)
    rows: list[dict[str, Any]] = []
    for row in _as_list(post_sell_rebudget.get("final_buy_orders_submitted")):
        if not isinstance(row, Mapping) or _order_side(row) != "BUY":
            continue
        key = _symbol_side_key(row)
        intended = intended_by_key.get(key)
        if not intended:
            continue
        intended_qty = _order_qty(intended)
        submitted_qty = _order_qty(row)
        if intended_qty is not None and submitted_qty is not None and abs(intended_qty - submitted_qty) <= 1e-9:
            continue
        rows.append(
            {
                "ticker": _order_symbol(intended),
                "side": "BUY",
                "shares": intended.get("shares", intended.get("quantity", intended.get("qty"))),
                "submitted_shares": row.get("shares", row.get("quantity", row.get("qty"))),
                "notional": _round(_order_notional(intended), 2),
                "submitted_notional": _round(_order_notional(row), 2),
                "reason": "resized_by_post_sell_rebudget",
                "rebudget_reason": _reason_from_order(row, default="post_sell_rebudget"),
            }
        )
    return rows


def _suppressed_intended_buys(
    intended_orders: Mapping[str, Any],
    post_sell_rebudget: Mapping[str, Any],
) -> list[dict[str, Any]]:
    intended_by_key = _intended_buys_by_symbol_side(intended_orders)
    rows: list[dict[str, Any]] = []
    for row in _as_list(post_sell_rebudget.get("skipped_buy_orders")):
        if not isinstance(row, Mapping) or _order_side(row) != "BUY":
            continue
        key = _symbol_side_key(row)
        intended = intended_by_key.get(key)
        if not intended:
            continue
        rows.append(
            {
                "ticker": _order_symbol(intended),
                "side": "BUY",
                "shares": intended.get("shares", intended.get("quantity", intended.get("qty"))),
                "notional": _round(_order_notional(intended), 2),
                "reason": _reason_from_order(
                    row,
                    default="buy_blocked_insufficient_buying_power",
                ),
                "skipped_notional": _round(_order_notional(row), 2),
            }
        )
    return rows


def _symbol_side_keys(rows: list[Any]) -> set[str]:
    return {
        _symbol_side_key(row)
        for row in rows
        if isinstance(row, Mapping) and _symbol_side_key(row) != ":"
    }


def _missing_intended_buys(
    intended_orders: Mapping[str, Any],
    submitted_orders: list[Any],
    *,
    excluded_symbol_side_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    excluded_symbol_side_keys = set(excluded_symbol_side_keys or set())
    intended = _side_orders(_as_list(intended_orders.get("orders_intended")), "BUY")
    submitted_keys = {_order_key(order) for order in _side_orders(submitted_orders, "BUY")}
    out = []
    for order in intended:
        if _symbol_side_key(order) in excluded_symbol_side_keys:
            continue
        if _order_key(order) in submitted_keys:
            continue
        out.append(
            {
                "ticker": _order_symbol(order),
                "side": _order_side(order),
                "shares": order.get("shares", order.get("quantity", order.get("qty"))),
                "notional": _round(_order_notional(order), 2),
            }
        )
    return out


def _underdeployment_classification(
    *,
    material_underdeployment: bool,
    pending_incomplete_buy_fills: bool,
    expected_post_buy_cash: float | None,
    target_cash_dollars: float | None,
    notional_tolerance: float,
    dollar_drift: float | None,
    suppressed_intended_buys: list[dict[str, Any]],
    skipped_buy_orders: list[Any],
) -> tuple[str, str, str]:
    if not material_underdeployment:
        return UNDERDEPLOYMENT_NONE, "target_cash_within_tolerance", "No operator action required."

    if pending_incomplete_buy_fills:
        return (
            UNDERDEPLOYMENT_PENDING_INCOMPLETE_FILL,
            "underdeployment_pending_incomplete_buy_fills",
            (
                "Refresh broker order state and do not treat the run as clean until "
                "partial or pending buy orders are filled, canceled, or explicitly reconciled."
            ),
        )

    suppressed_reasons = {
        str(row.get("reason") or row.get("block_reason") or "").strip()
        for row in suppressed_intended_buys
        if isinstance(row, Mapping)
    }
    skipped_reasons = {
        str(row.get("block_reason") or row.get("reason") or "").strip()
        for row in skipped_buy_orders
        if isinstance(row, Mapping)
    }
    constraint_reasons = {reason for reason in suppressed_reasons | skipped_reasons if reason}
    if constraint_reasons and all(reason in _HARD_CONSTRAINT_REASONS for reason in constraint_reasons):
        return (
            UNDERDEPLOYMENT_BROKER_RISK_BLOCKED,
            "underdeployment_broker_or_risk_blocked",
            (
                "Preserve the broker/risk constraint evidence and review whether the cash "
                "target should be considered blocked rather than cleanly attained."
            ),
        )

    if (
        expected_post_buy_cash is not None
        and target_cash_dollars is not None
        and expected_post_buy_cash > target_cash_dollars + float(notional_tolerance)
    ):
        return (
            UNDERDEPLOYMENT_AVOIDABLE_REBUDGET,
            "avoidable_rebudget_underdeployment",
            (
                "Review post-sell rebudget allocation before the next cycle; residual buy "
                "budget remained above the cash target without a hard broker or risk block."
            ),
        )

    if dollar_drift is not None and abs(dollar_drift) > float(notional_tolerance):
        return (
            UNDERDEPLOYMENT_REPORTING_MISMATCH,
            "underdeployment_reporting_accounting_mismatch",
            (
                "Reconcile submitted, filled, and posttrade cash artifacts before treating "
                "the target-attainment result as execution truth."
            ),
        )

    return (
        UNDERDEPLOYMENT_AVOIDABLE_REBUDGET,
        "avoidable_rebudget_underdeployment",
        (
            "Review post-sell rebudget allocation before the next cycle; material cash "
            "underdeployment remains unexplained by hard constraints."
        ),
    )


def build_execution_target_attainment(
    *,
    outputs_root: str | Path = "outputs",
    trade_date: str | None = None,
    run_id: str | None = None,
    cash_weight_drift_tolerance: float = DEFAULT_CASH_WEIGHT_DRIFT_TOLERANCE,
    notional_drift_tolerance: float | None = None,
) -> dict[str, Any]:
    root = Path(outputs_root)
    run_root = _select_run_dir(root, trade_date=trade_date, run_id=run_id)
    if run_root is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": UNKNOWN_INSUFFICIENT_ARTIFACTS,
            "run_id": run_id,
            "trade_date": trade_date,
            "warnings": ["missing execution run directory"],
            "source_artifacts": {"outputs_root": str(root)},
        }

    execution_payload_path = run_root / "execution_payload.json"
    execution_results_path = run_root / "execution_results.json"
    operator_summary_path = run_root / "operator_summary.json"
    integrity_path = run_root / "audit" / "execution_integrity.json"
    execution_timeline_path = run_root / "execution_timeline.json"

    execution_payload = _read_json(execution_payload_path)
    execution_results = _read_json(execution_results_path)
    operator_summary = _read_json(operator_summary_path)
    integrity = _read_json(integrity_path)
    execution_timeline = _read_json(execution_timeline_path)

    resolved_trade_date = (
        trade_date
        or str(execution_payload.get("trade_date") or execution_results.get("trade_date") or operator_summary.get("trade_date") or "") 
        or None
    )
    resolved_run_id = str(execution_payload.get("run_id") or execution_results.get("run_id") or operator_summary.get("run_id") or run_root.name)
    rebudget_path = _latest_rebudget_path(run_root, resolved_trade_date)
    recon_path = (
        run_root / "broker" / f"recon_posttrade_{resolved_trade_date}.json"
        if resolved_trade_date
        else _first_existing(sorted((run_root / "broker").glob("recon_posttrade_*.json"), reverse=True)) or run_root / "broker" / "recon_posttrade_UNKNOWN.json"
    )
    intended_orders_path = (
        run_root / "broker" / f"intended_orders_{resolved_trade_date}.json"
        if resolved_trade_date
        else run_root / "broker" / "intended_orders_UNKNOWN.json"
    )
    posttrade_account_path = _first_existing(
        [
            run_root / "broker" / "posttrade_account_snapshot.json",
            run_root / "broker" / "posttrade_account.json",
        ]
    ) or run_root / "broker" / "posttrade_account_snapshot.json"

    post_sell_rebudget = _read_json(rebudget_path)
    recon = _read_json(recon_path)
    intended_orders = _read_json(intended_orders_path)
    posttrade_account = _read_json(posttrade_account_path)

    execution_status = str(
        operator_summary.get("terminal_status")
        or execution_results.get("status")
        or execution_payload.get("execution_status")
        or execution_payload.get("status")
        or "UNKNOWN"
    )
    reconciliation_status = _reconciliation_status(recon, operator_summary, execution_payload)

    submitted_count = _to_int(execution_results.get("submitted_count") or execution_payload.get("submitted_count"))
    accepted_count = _to_int(execution_results.get("accepted_count") or execution_payload.get("accepted_count"))
    rejected_count = _to_int(execution_results.get("rejected_count") or execution_payload.get("rejected_count"))
    submitted_buy_count = _to_int(
        execution_payload.get("submitted_buy_count")
        or execution_results.get("submitted_buy_count")
        or operator_summary.get("submitted_buy_count")
    )
    submitted_sell_count = _to_int(
        execution_payload.get("submitted_sell_count")
        or execution_results.get("submitted_sell_count")
        or operator_summary.get("submitted_sell_count")
    )

    submitted_orders = (
        _as_list(post_sell_rebudget.get("final_buy_orders_submitted"))
        + _as_list(execution_payload.get("trades"))
        + _as_list(execution_results.get("broker_responses"))
    )
    order_lifecycle = _as_list(execution_payload.get("order_lifecycle") or execution_results.get("order_lifecycle"))
    resolved_orders = _as_list(execution_payload.get("posttrade_resolved_orders"))
    if not resolved_orders and isinstance(execution_payload.get("broker_reconciliation"), Mapping):
        resolved_orders = _as_list(execution_payload["broker_reconciliation"].get("posttrade_resolved_orders"))
    filled_source_orders = resolved_orders or order_lifecycle or submitted_orders
    buy_fill_status_source = (
        "posttrade_resolved_orders"
        if resolved_orders
        else "execution_results_order_lifecycle"
        if order_lifecycle
        else "submitted_orders"
        if submitted_orders
        else "none"
    )

    execution_result_filled_buy_count = (
        _to_int(execution_results.get("filled_buy_count"))
        if execution_results.get("filled_buy_count") is not None
        else None
    )
    execution_result_pending_buy_count = (
        _to_int(execution_results.get("pending_buy_count"))
        if execution_results.get("pending_buy_count") is not None
        else None
    )
    execution_result_partial_buy_count = (
        _to_int(execution_results.get("partial_buy_count"))
        if execution_results.get("partial_buy_count") is not None
        else None
    )
    filled_buy_count = (
        execution_result_filled_buy_count
        if execution_result_filled_buy_count is not None
        else _filled_count(filled_source_orders, "BUY")
    )
    filled_sell_count = _filled_count(filled_source_orders, "SELL")
    pending_buy_count = (
        execution_result_pending_buy_count
        if execution_result_pending_buy_count is not None
        else _pending_count(filled_source_orders, "BUY")
    )
    partial_buy_count = (
        execution_result_partial_buy_count
        if execution_result_partial_buy_count is not None
        else _partial_count(filled_source_orders, "BUY")
    )
    buy_phase_status = str(
        execution_results.get("buy_phase_status")
        or execution_payload.get("buy_phase_status")
        or operator_summary.get("buy_phase_status")
        or ""
    ).strip()
    buy_phase_completion_reason = str(
        execution_results.get("buy_phase_completion_reason")
        or execution_payload.get("buy_phase_completion_reason")
        or operator_summary.get("buy_phase_completion_reason")
        or ""
    ).strip()
    posttrade_unresolved_orders_count = _to_int(
        execution_results.get("posttrade_unresolved_orders_count")
        or execution_payload.get("posttrade_unresolved_orders_count")
        or operator_summary.get("posttrade_unresolved_orders_count")
    )
    if partial_buy_count > 0 and _upper(buy_phase_status) in _BUY_INCOMPLETE_PHASE_STATUSES:
        pending_buy_count = max(pending_buy_count, partial_buy_count)
    submitted_buy_notional = (
        _to_float(post_sell_rebudget.get("final_submitted_buy_notional"))
        or _notional(submitted_orders, "BUY")
        or _to_float((execution_payload.get("cash_gate_diagnostics") or {}).get("buy_notional_submitted_immediate") if isinstance(execution_payload.get("cash_gate_diagnostics"), Mapping) else None)
    )
    filled_buy_notional = _notional(filled_source_orders, "BUY", filled=True) if filled_source_orders else None
    cash_gate_diagnostics = execution_payload.get("cash_gate_diagnostics")
    cash_gate_skipped_notional = (
        _to_float(cash_gate_diagnostics.get("buy_notional_skipped_or_deferred"))
        if isinstance(cash_gate_diagnostics, Mapping)
        else None
    )
    skipped_deferred_buy_notional = _notional(
        _as_list(post_sell_rebudget.get("skipped_buy_orders")),
        "BUY",
    ) + float(cash_gate_skipped_notional or 0.0)
    resized_intended_buys = (
        _as_list(integrity.get("resized_buy_orders"))
        or _as_list(integrity.get("resized_intended_orders"))
        or _resized_intended_buys(intended_orders, post_sell_rebudget)
    )
    suppressed_intended_buys = (
        _as_list(integrity.get("suppressed_buy_orders"))
        or _as_list(integrity.get("suppressed_intended_orders"))
        or _suppressed_intended_buys(intended_orders, post_sell_rebudget)
    )
    accepted_exception_keys = _symbol_side_keys(resized_intended_buys) | _symbol_side_keys(suppressed_intended_buys)
    raw_integrity_missing = integrity.get("missing_buy_orders")
    if isinstance(raw_integrity_missing, list):
        missing_intended_buys = [
            row
            for row in raw_integrity_missing
            if isinstance(row, Mapping) and _symbol_side_key(row) not in accepted_exception_keys
        ]
    else:
        missing_intended_buys = _missing_intended_buys(
            intended_orders,
            submitted_orders,
            excluded_symbol_side_keys=accepted_exception_keys,
        )

    actual_cash_source = _cash_source(
        posttrade_account=posttrade_account,
        posttrade_account_path=posttrade_account_path,
        recon=recon,
        recon_path=recon_path,
        post_sell_rebudget=post_sell_rebudget,
        rebudget_path=rebudget_path,
    )
    actual_posttrade_cash = _to_float(actual_cash_source.get("cash"))
    actual_posttrade_equity = _to_float(actual_cash_source.get("equity"))
    posttrade_snapshot_lag_seconds = _seconds_between(
        str(execution_results.get("buy_submit_completed_at") or execution_payload.get("buy_submit_completed_at") or "") or None,
        actual_cash_source.get("timestamp"),
    )
    posttrade_cash_equals_post_sell_cash = (
        actual_posttrade_cash is not None
        and _to_float(post_sell_rebudget.get("post_sell_cash")) is not None
        and abs(actual_posttrade_cash - float(post_sell_rebudget.get("post_sell_cash") or 0.0)) <= 0.01
    )
    target_cash_weight = (
        _to_float(post_sell_rebudget.get("target_cash_weight"))
        if post_sell_rebudget.get("target_cash_weight") is not None
        else _to_float(execution_payload.get("cash_target_weight") or execution_payload.get("target_cash_weight") or integrity.get("cash_target_weight"))
    )
    achieved_cash_weight = (
        _to_float(execution_payload.get("achieved_cash_weight"))
        if execution_payload.get("achieved_cash_weight") is not None
        else _to_float(operator_summary.get("achieved_cash_weight"))
        if operator_summary.get("achieved_cash_weight") is not None
        else (actual_posttrade_cash / actual_posttrade_equity if actual_posttrade_cash is not None and actual_posttrade_equity else None)
    )
    cash_target_drift = (
        achieved_cash_weight - target_cash_weight
        if achieved_cash_weight is not None and target_cash_weight is not None
        else None
    )
    cash_drift_warning = abs(cash_target_drift) > float(cash_weight_drift_tolerance) if cash_target_drift is not None else False
    expected_post_buy_cash = _to_float(post_sell_rebudget.get("estimated_ending_cash"))
    target_cash_dollars = (
        actual_posttrade_equity * target_cash_weight
        if actual_posttrade_equity is not None and target_cash_weight is not None
        else _to_float(post_sell_rebudget.get("risk_cash_target"))
    )
    residual_undeployed_cash = (
        actual_posttrade_cash - target_cash_dollars
        if actual_posttrade_cash is not None and target_cash_dollars is not None
        else None
    )
    dollar_drift = (
        actual_posttrade_cash - expected_post_buy_cash
        if actual_posttrade_cash is not None and expected_post_buy_cash is not None
        else None
    )
    equity_for_tolerance = actual_posttrade_equity or _to_float(post_sell_rebudget.get("post_sell_equity"))
    resolved_notional_tolerance = (
        float(notional_drift_tolerance)
        if notional_drift_tolerance is not None
        else max(
            DEFAULT_NOTIONAL_DRIFT_TOLERANCE_FLOOR,
            float(equity_for_tolerance or 0.0) * DEFAULT_NOTIONAL_DRIFT_TOLERANCE_EQUITY_PCT,
        )
    )
    notional_drift_warning = abs(dollar_drift) > resolved_notional_tolerance if dollar_drift is not None else False
    pending_incomplete_buy_fills = bool(
        submitted_buy_count > 0
        and (
            pending_buy_count > 0
            or partial_buy_count > 0
            or posttrade_unresolved_orders_count > 0
            or _upper(buy_phase_status) in _BUY_INCOMPLETE_PHASE_STATUSES
        )
    )
    posttrade_cash_snapshot_stale = bool(
        submitted_buy_count > 0
        and pending_buy_count > 0
        and filled_buy_count < submitted_buy_count
        and posttrade_cash_equals_post_sell_cash
        and expected_post_buy_cash is not None
        and dollar_drift is not None
        and abs(dollar_drift) > resolved_notional_tolerance
    )
    explicit_posttrade_snapshot_stage = str(
        execution_results.get("posttrade_snapshot_stage")
        or execution_payload.get("posttrade_snapshot_stage")
        or ""
    ).strip()
    if explicit_posttrade_snapshot_stage:
        posttrade_cash_snapshot_stage = explicit_posttrade_snapshot_stage
    elif actual_cash_source.get("source") == "post_sell_rebudget_ending_cash":
        posttrade_cash_snapshot_stage = "post_sell"
    elif pending_incomplete_buy_fills and _upper(buy_phase_status) == "BUY_PHASE_TIMEOUT":
        posttrade_cash_snapshot_stage = "buy_timeout"
    elif posttrade_cash_snapshot_stale:
        posttrade_cash_snapshot_stage = "pre_buy"
    elif submitted_buy_count > 0 and filled_buy_count >= submitted_buy_count:
        posttrade_cash_snapshot_stage = "post_buy"
    elif _reconciliation_passed(reconciliation_status):
        posttrade_cash_snapshot_stage = "post_reconciliation"
    else:
        posttrade_cash_snapshot_stage = "unknown"

    missing_required = []
    if not execution_payload_path.exists() and not execution_results_path.exists():
        missing_required.append("execution_payload_or_results")
    if not rebudget_path.exists():
        missing_required.append("post_sell_rebudget")
    if (
        not posttrade_account_path.exists()
        and recon.get("broker_cash") is None
        and post_sell_rebudget.get("ending_cash") is None
    ):
        missing_required.append("posttrade_account_data")
    if target_cash_weight is None or achieved_cash_weight is None:
        missing_required.append("cash_weights")

    material_underdeployment = bool(
        cash_target_drift is not None
        and cash_target_drift > float(cash_weight_drift_tolerance)
    )
    underdeployment_classification, underdeployment_reason_code, operator_action = (
        _underdeployment_classification(
            material_underdeployment=material_underdeployment,
            pending_incomplete_buy_fills=pending_incomplete_buy_fills,
            expected_post_buy_cash=expected_post_buy_cash,
            target_cash_dollars=target_cash_dollars,
            notional_tolerance=resolved_notional_tolerance,
            dollar_drift=dollar_drift,
            suppressed_intended_buys=suppressed_intended_buys,
            skipped_buy_orders=_as_list(post_sell_rebudget.get("skipped_buy_orders")),
        )
    )

    if missing_required:
        status = UNKNOWN_INSUFFICIENT_ARTIFACTS
    elif not _execution_complete(
        execution_status=execution_status,
        submitted_count=submitted_count,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
    ):
        status = FAIL_EXECUTION_INCOMPLETE
    elif material_underdeployment and pending_incomplete_buy_fills:
        status = WARN_UNDERDEPLOYED_PENDING_BUY_FILLS
    elif posttrade_cash_snapshot_stale:
        status = WARN_POSTTRADE_SNAPSHOT_STALE_OR_PRE_BUY
    elif _reconciliation_passed(reconciliation_status) and cash_target_drift is not None and cash_target_drift > float(cash_weight_drift_tolerance):
        status = WARN_RECONCILED_BUT_UNDERDEPLOYED
    elif cash_drift_warning or notional_drift_warning:
        status = WARN_CASH_DRIFT
    else:
        status = OK_TARGET_ATTAINED

    warnings = []
    if missing_required:
        warnings.append("insufficient artifacts: " + ", ".join(sorted(set(missing_required))))
    if not missing_required and cash_drift_warning:
        warnings.append("cash_target_drift")
    if not missing_required and notional_drift_warning:
        warnings.append("post_buy_cash_drift")
    if posttrade_cash_snapshot_stale:
        warnings.append("posttrade cash snapshot appears stale or pre-buy")
    if pending_incomplete_buy_fills:
        warnings.append("pending or incomplete buy fills")
    if status == WARN_RECONCILED_BUT_UNDERDEPLOYED:
        warnings.append("reconciliation passed despite target cash miss")
    if material_underdeployment and underdeployment_reason_code != "target_cash_within_tolerance":
        warnings.append(underdeployment_reason_code)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": resolved_run_id,
        "trade_date": resolved_trade_date,
        "execution_status": execution_status,
        "reconciliation_status": reconciliation_status,
        "reconciliation_passed": _reconciliation_passed(reconciliation_status),
        "submitted_count": submitted_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "submitted_buy_count": submitted_buy_count,
        "filled_buy_count": filled_buy_count,
        "pending_buy_count": pending_buy_count,
        "partial_buy_count": partial_buy_count,
        "posttrade_unresolved_orders_count": posttrade_unresolved_orders_count,
        "buy_phase_status": buy_phase_status or None,
        "buy_phase_completion_reason": buy_phase_completion_reason or None,
        "submitted_sell_count": submitted_sell_count,
        "filled_sell_count": filled_sell_count,
        "target_cash_weight": _round(target_cash_weight),
        "achieved_cash_weight": _round(achieved_cash_weight),
        "cash_target_drift": _round(cash_target_drift),
        "cash_drift_warning": bool(cash_drift_warning),
        "target_cash_dollars": _round(target_cash_dollars, 2),
        "residual_undeployed_cash": _round(residual_undeployed_cash, 2),
        "underdeployment_classification": underdeployment_classification,
        "underdeployment_reason_code": underdeployment_reason_code,
        "operator_action": operator_action,
        "action_required": bool(
            material_underdeployment
            or status
            in {
                WARN_UNDERDEPLOYED_PENDING_BUY_FILLS,
                WARN_RECONCILED_BUT_UNDERDEPLOYED,
                WARN_CASH_DRIFT,
                WARN_POSTTRADE_SNAPSHOT_STALE_OR_PRE_BUY,
                FAIL_EXECUTION_INCOMPLETE,
            }
        ),
        "pre_sell_cash": _round(post_sell_rebudget.get("pre_sell_cash"), 2),
        "post_sell_cash": _round(post_sell_rebudget.get("post_sell_cash"), 2),
        "expected_post_buy_cash": _round(expected_post_buy_cash, 2),
        "actual_posttrade_cash": _round(actual_posttrade_cash, 2),
        "actual_posttrade_cash_source": actual_cash_source.get("source"),
        "actual_posttrade_cash_source_path": actual_cash_source.get("path"),
        "actual_posttrade_cash_timestamp": actual_cash_source.get("timestamp"),
        "posttrade_cash_snapshot_stale": bool(posttrade_cash_snapshot_stale),
        "posttrade_cash_snapshot_stage": posttrade_cash_snapshot_stage,
        "posttrade_snapshot_lag_seconds": _round(posttrade_snapshot_lag_seconds, 3),
        "post_buy_cash_drift": _round(dollar_drift, 2),
        "submitted_buy_notional": _round(submitted_buy_notional, 2),
        "filled_buy_notional": _round(filled_buy_notional, 2),
        "buy_fill_status_source": buy_fill_status_source,
        "skipped_deferred_buy_notional": _round(skipped_deferred_buy_notional, 2),
        "resized_intended_buys": resized_intended_buys,
        "suppressed_intended_buys": suppressed_intended_buys,
        "missing_intended_buys": missing_intended_buys,
        "reconciled_but_target_miss": bool(status == WARN_RECONCILED_BUT_UNDERDEPLOYED),
        "tolerances": {
            "cash_weight_drift_tolerance": float(cash_weight_drift_tolerance),
            "notional_drift_tolerance": round(resolved_notional_tolerance, 2),
        },
        "warnings": sorted(set(warnings)),
        "insufficient_artifacts": sorted(set(missing_required)),
        "source_artifacts": {
            "execution_payload": _source_probe(execution_payload_path),
            "execution_results": _source_probe(execution_results_path),
            "operator_summary": _source_probe(operator_summary_path),
            "execution_integrity": _source_probe(integrity_path),
            "execution_timeline": _source_probe(execution_timeline_path),
            "intended_orders": _source_probe(intended_orders_path),
            "post_sell_rebudget": _source_probe(rebudget_path),
            "posttrade_account": _source_probe(posttrade_account_path),
            "reconciliation": _source_probe(recon_path),
        },
        "timeline_status": execution_timeline.get("status"),
    }
