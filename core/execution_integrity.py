from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from paper.run_manager import safe_write_text


STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"

DEFAULT_CASH_DRIFT_THRESHOLD = 0.025


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _order_symbol(order: Mapping[str, Any]) -> str:
    return _upper(order.get("ticker") or order.get("symbol"))


def _order_side(order: Mapping[str, Any]) -> str:
    return _upper(order.get("side") or order.get("action"))


def _order_qty(order: Mapping[str, Any]) -> str:
    value = order.get("shares")
    if value is None:
        value = order.get("quantity")
    if value is None:
        value = order.get("qty")
    if value is None or value == "":
        return ""
    try:
        qty = float(value)
    except (TypeError, ValueError):
        return str(value)
    if qty.is_integer():
        return str(int(qty))
    return f"{qty:.8f}".rstrip("0").rstrip(".")


def _order_key(order: Mapping[str, Any]) -> str:
    order_id = str(order.get("order_id") or order.get("client_order_id") or "").strip()
    if order_id:
        return f"order_id:{order_id}"
    return f"{_order_symbol(order)}:{_order_side(order)}:{_order_qty(order)}"


def _order_id(order: Mapping[str, Any]) -> str:
    return str(order.get("order_id") or order.get("client_order_id") or "").strip()


def _semantic_order_key(order: Mapping[str, Any]) -> str:
    return f"{_order_symbol(order)}:{_order_side(order)}:{_order_qty(order)}"


def _orders_match_for_lineage(
    order: Mapping[str, Any],
    candidates_by_key: Mapping[str, Mapping[str, Any]],
    candidates_by_semantic_key: Mapping[str, list[Mapping[str, Any]]],
) -> bool:
    if _order_key(order) in candidates_by_key:
        return True
    order_id = _order_id(order)
    semantic_key = _semantic_order_key(order)
    for candidate in candidates_by_semantic_key.get(semantic_key, []):
        if not order_id or not _order_id(candidate):
            return True
    return False


def _order_ref(order: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ticker": _order_symbol(order),
        "side": _order_side(order),
        "shares": order.get("shares", order.get("quantity", order.get("qty"))),
        "order_id": order.get("order_id") or order.get("client_order_id"),
    }


def _side_count(orders: list[Any], side: str) -> int:
    side = side.upper()
    return sum(
        1
        for order in orders
        if isinstance(order, Mapping) and _order_side(order) == side
    )


def _explicit_reasons(
    intended_orders: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
    operator_summary: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    block_reasons: list[str] = []
    defer_reasons: list[str] = []
    for value in _as_list(intended_orders.get("block_reasons")):
        if str(value).strip():
            block_reasons.append(str(value))
    for key in (
        "buy_phase_block_reason",
        "halt_reason",
        "execution_reason",
        "broker_reject_status",
        "broker_reject_message",
        "pretrade_halt_reason",
    ):
        source = execution_payload if key in execution_payload else operator_summary
        value = source.get(key)
        if str(value or "").strip():
            block_reasons.append(f"{key}:{value}")
    for key in (
        "retry_reason",
        "continuation_reason",
        "cash_rebalance_status",
    ):
        source = execution_payload if key in execution_payload else operator_summary
        value = source.get(key)
        if str(value or "").strip():
            defer_reasons.append(f"{key}:{value}")
    if bool(operator_summary.get("capital_constraint_triggered")):
        defer_reasons.append("capital_constraint_triggered")
    clipped = _to_int(operator_summary.get("clipped_or_deferred_buys_count"))
    if clipped > 0:
        defer_reasons.append(f"clipped_or_deferred_buys_count:{clipped}")
    return sorted(set(block_reasons)), sorted(set(defer_reasons))


def _response_status(response: Any) -> str:
    if not isinstance(response, Mapping):
        return ""
    status = response.get("status")
    if status is None and isinstance(response.get("order"), Mapping):
        status = response["order"].get("status")
    return _upper(status)


def _accepted_response_count(responses: list[Any]) -> int:
    rejected_markers = {"REJECTED", "FAILED", "ERROR", "CANCELED", "CANCELLED"}
    count = 0
    for response in responses:
        status = _response_status(response)
        if not status:
            continue
        if any(marker in status for marker in rejected_markers):
            continue
        count += 1
    return count


def _rejected_response_count(responses: list[Any]) -> int:
    rejected_markers = {"REJECTED", "FAILED", "ERROR"}
    count = 0
    for response in responses:
        status = _response_status(response)
        if any(marker in status for marker in rejected_markers):
            count += 1
    return count


def _add_finding(
    findings: list[dict[str, str]],
    *,
    severity: str,
    code: str,
    message: str,
) -> None:
    findings.append(
        {
            "severity": severity,
            "code": code,
            "message": message,
        }
    )


def validate_execution_integrity(
    *,
    trade_date: str,
    run_id: str,
    intended_orders: Mapping[str, Any] | None,
    execution_payload: Mapping[str, Any] | None,
    execution_results: Mapping[str, Any] | None,
    operator_summary: Mapping[str, Any] | None = None,
    cash_drift_threshold: float = DEFAULT_CASH_DRIFT_THRESHOLD,
) -> dict[str, Any]:
    intended_orders = dict(intended_orders or {})
    execution_payload = dict(execution_payload or {})
    execution_results = dict(execution_results or {})
    operator_summary = dict(operator_summary or {})

    intended = [
        dict(order)
        for order in _as_list(intended_orders.get("orders_intended"))
        if isinstance(order, Mapping)
    ]
    payload_orders = [
        dict(order)
        for order in _as_list(execution_payload.get("trades"))
        if isinstance(order, Mapping)
    ]
    broker_responses = _as_list(execution_results.get("broker_responses"))

    continuation_mode = str(
        execution_payload.get("continuation_mode")
        or operator_summary.get("continuation_mode")
        or ""
    ).strip()
    continuation_source = str(
        execution_payload.get("continuation_source")
        or operator_summary.get("continuation_source")
        or ""
    ).strip()
    continuation_path = str(
        execution_payload.get("continuation_intended_orders_path")
        or operator_summary.get("continuation_intended_orders_path")
        or ""
    ).strip()
    continuation_eligible = bool(
        execution_payload.get("continuation_eligible")
        or operator_summary.get("continuation_eligible")
        or continuation_mode
    )
    continuation_side = "BUY" if continuation_mode == "buy_only" else ""

    explicit_block_reasons, explicit_defer_reasons = _explicit_reasons(
        intended_orders,
        execution_payload,
        operator_summary,
    )
    has_explicit_exception = bool(
        explicit_block_reasons
        or explicit_defer_reasons
        or continuation_mode
        or continuation_source
        or continuation_path
    )

    intended_count = _to_int(intended_orders.get("orders_intended_count"))
    if intended_count == 0 and intended:
        intended_count = len(intended)
    payload_count = len(payload_orders)
    payload_declared_count = _to_int(
        execution_payload.get("execution_eligible_trades_count")
        or execution_payload.get("executable_trades_count")
    )
    submitted_count = _to_int(
        execution_results.get("submitted_count")
        or execution_payload.get("submitted_count")
    )
    accepted_count = _to_int(
        execution_results.get("accepted_count")
        or execution_payload.get("accepted_count")
    )
    rejected_count = _to_int(
        execution_results.get("rejected_count")
        or execution_payload.get("rejected_count")
    )
    broker_response_count = len(broker_responses)
    pending_buy_count = _to_int(
        execution_payload.get("pending_buy_count")
        or operator_summary.get("pending_buy_count")
    )
    submitted_buy_count = _to_int(
        execution_payload.get("submitted_buy_count")
        or operator_summary.get("submitted_buy_count")
    )

    intended_keys = {_order_key(order): order for order in intended}
    payload_keys = {_order_key(order): order for order in payload_orders}
    payload_semantic_keys: dict[str, list[Mapping[str, Any]]] = {}
    for order in payload_orders:
        payload_semantic_keys.setdefault(_semantic_order_key(order), []).append(order)
    comparable_intended = list(intended)
    if continuation_side:
        comparable_intended = [
            order for order in intended if _order_side(order) == continuation_side
        ]
    missing_intended = [
        _order_ref(order)
        for order in sorted(
            comparable_intended,
            key=lambda candidate: (_semantic_order_key(candidate), _order_key(candidate)),
        )
        if not _orders_match_for_lineage(order, payload_keys, payload_semantic_keys)
    ]
    missing_buy_orders = [
        order for order in missing_intended if _order_side(order) == "BUY"
    ]
    comparable_intended_keys = {_order_key(order): order for order in comparable_intended}
    comparable_intended_semantic_keys: dict[str, list[Mapping[str, Any]]] = {}
    for order in comparable_intended:
        comparable_intended_semantic_keys.setdefault(_semantic_order_key(order), []).append(order)
    unexpected_payload_orders = [
        _order_ref(order)
        for order in sorted(
            payload_orders,
            key=lambda candidate: (_semantic_order_key(candidate), _order_key(candidate)),
        )
        if intended
        and not _orders_match_for_lineage(
            order,
            comparable_intended_keys,
            comparable_intended_semantic_keys,
        )
    ]

    findings: list[dict[str, str]] = []
    if intended_count != payload_count and continuation_mode:
        pass
    elif intended_count != payload_count and not has_explicit_exception:
        _add_finding(
            findings,
            severity="FAIL",
            code="intended_payload_count_mismatch",
            message=(
                f"intended_orders_count={intended_count} does not match "
                f"execution_payload_trade_count={payload_count}"
            ),
        )
    elif intended_count != payload_count and has_explicit_exception:
        _add_finding(
            findings,
            severity="WARN",
            code="intended_payload_count_mismatch_with_exception",
            message=(
                f"intended_orders_count={intended_count} differs from "
                f"execution_payload_trade_count={payload_count} with explicit "
                "block/defer/continuation metadata"
            ),
        )

    if missing_buy_orders and not has_explicit_exception:
        _add_finding(
            findings,
            severity="FAIL",
            code="intended_buy_missing_from_payload",
            message="One or more intended BUY orders are absent from execution_payload.trades",
        )
    elif missing_buy_orders:
        _add_finding(
            findings,
            severity="WARN",
            code="intended_buy_missing_from_payload_with_exception",
            message="Intended BUY orders are absent from payload but explicit exception metadata is present",
        )

    if pending_buy_count > 0 and submitted_buy_count == 0:
        operator_status = str(operator_summary.get("terminal_status") or "").lower()
        execution_status = _upper(execution_payload.get("execution_status") or execution_payload.get("status"))
        severity = (
            "FAIL"
            if operator_status == "success" or execution_status in {"EXECUTED", "READY"}
            else "WARN"
        )
        _add_finding(
            findings,
            severity=severity,
            code="pending_buys_without_submitted_buys",
            message=(
                f"pending_buy_count={pending_buy_count} but submitted_buy_count=0; "
                "run must not be interpreted as clean buy success"
            ),
        )

    if payload_declared_count and payload_declared_count != payload_count and not has_explicit_exception:
        _add_finding(
            findings,
            severity="FAIL",
            code="execution_eligible_count_mismatch",
            message=(
                f"execution_eligible_trades_count={payload_declared_count} does not "
                f"match execution_payload.trades length={payload_count}"
            ),
        )
    elif payload_declared_count and payload_declared_count != payload_count:
        _add_finding(
            findings,
            severity="WARN",
            code="execution_eligible_count_mismatch_with_exception",
            message="Execution eligible count differs from payload order list with explicit exception metadata",
        )

    expected_responses = accepted_count + rejected_count
    if broker_response_count != expected_responses:
        _add_finding(
            findings,
            severity="WARN",
            code="broker_response_count_mismatch",
            message=(
                f"broker_response_count={broker_response_count} does not match "
                f"accepted_count+rejected_count={expected_responses}"
            ),
        )
    if submitted_count != accepted_count:
        _add_finding(
            findings,
            severity="WARN",
            code="submitted_accepted_count_mismatch",
            message=f"submitted_count={submitted_count} differs from accepted_count={accepted_count}",
        )

    response_accepted_count = _accepted_response_count(broker_responses)
    response_rejected_count = _rejected_response_count(broker_responses)
    if broker_responses and response_accepted_count and accepted_count != response_accepted_count:
        _add_finding(
            findings,
            severity="WARN",
            code="accepted_count_response_mismatch",
            message=(
                f"accepted_count={accepted_count} differs from accepted broker "
                f"responses={response_accepted_count}"
            ),
        )
    if broker_responses and response_rejected_count and rejected_count != response_rejected_count:
        _add_finding(
            findings,
            severity="WARN",
            code="rejected_count_response_mismatch",
            message=(
                f"rejected_count={rejected_count} differs from rejected broker "
                f"responses={response_rejected_count}"
            ),
        )

    if continuation_mode and not continuation_source and not continuation_path:
        _add_finding(
            findings,
            severity="WARN",
            code="continuation_source_missing",
            message="Continuation run is missing source artifact metadata",
        )
    if continuation_mode == "buy_only" and payload_orders and _side_count(payload_orders, "SELL") > 0:
        _add_finding(
            findings,
            severity="FAIL",
            code="buy_only_continuation_contains_sell",
            message="Buy-only continuation payload contains SELL orders",
        )

    cash_target_weight = _to_float(
        execution_payload.get("cash_target_weight")
        if execution_payload.get("cash_target_weight") is not None
        else operator_summary.get("cash_target_weight")
    )
    achieved_cash_weight = _to_float(
        execution_payload.get("achieved_cash_weight")
        if execution_payload.get("achieved_cash_weight") is not None
        else operator_summary.get("achieved_cash_weight")
    )
    cash_drift_warning = False
    if cash_target_weight is not None and achieved_cash_weight is not None:
        drift = abs(float(achieved_cash_weight) - float(cash_target_weight))
        if drift > float(cash_drift_threshold):
            cash_drift_warning = True
            _add_finding(
                findings,
                severity="WARN",
                code="cash_target_drift",
                message=(
                    f"achieved_cash_weight={achieved_cash_weight:.6f} differs from "
                    f"cash_target_weight={cash_target_weight:.6f} by {drift:.6f}"
                ),
            )

    status = STATUS_OK
    if any(finding["severity"] == "FAIL" for finding in findings):
        status = STATUS_FAIL
    elif findings:
        status = STATUS_WARN

    return {
        "status": status,
        "trade_date": str(trade_date),
        "run_id": str(run_id),
        "intended_orders_count": int(intended_count),
        "intended_buy_count": _side_count(intended, "BUY"),
        "intended_sell_count": _side_count(intended, "SELL"),
        "execution_payload_trade_count": int(payload_count),
        "execution_payload_buy_count": _side_count(payload_orders, "BUY"),
        "execution_payload_sell_count": _side_count(payload_orders, "SELL"),
        "submitted_count": int(submitted_count),
        "accepted_count": int(accepted_count),
        "rejected_count": int(rejected_count),
        "broker_response_count": int(broker_response_count),
        "pending_buy_count": int(pending_buy_count),
        "continuation_eligible": bool(continuation_eligible),
        "continuation_mode": continuation_mode or None,
        "continuation_side": continuation_side or None,
        "continuation_source": continuation_source or None,
        "continuation_intended_orders_path": continuation_path or None,
        "missing_intended_orders": missing_intended,
        "missing_buy_orders": missing_buy_orders,
        "unexpected_payload_orders": unexpected_payload_orders,
        "explicit_block_reasons": explicit_block_reasons,
        "explicit_defer_reasons": explicit_defer_reasons,
        "cash_target_weight": cash_target_weight,
        "achieved_cash_weight": achieved_cash_weight,
        "cash_drift_warning": bool(cash_drift_warning),
        "findings": findings,
    }


def write_execution_integrity_audit(
    *,
    run_root: str | Path,
    trade_date: str,
    run_id: str,
    intended_orders_path: str | Path | None = None,
    cash_drift_threshold: float = DEFAULT_CASH_DRIFT_THRESHOLD,
) -> Path:
    root = Path(run_root)
    intended_path = (
        Path(intended_orders_path)
        if intended_orders_path
        else root / "broker" / f"intended_orders_{trade_date}.json"
    )
    audit = validate_execution_integrity(
        trade_date=trade_date,
        run_id=run_id,
        intended_orders=_read_json(intended_path),
        execution_payload=_read_json(root / "execution_payload.json"),
        execution_results=_read_json(root / "execution_results.json"),
        operator_summary=_read_json(root / "operator_summary.json"),
        cash_drift_threshold=cash_drift_threshold,
    )
    audit["source_artifacts"] = {
        "intended_orders": str(intended_path),
        "execution_payload": str(root / "execution_payload.json"),
        "execution_results": str(root / "execution_results.json"),
        "operator_summary": str(root / "operator_summary.json"),
    }
    out_path = root / "audit" / "execution_integrity.json"
    safe_write_text(
        out_path,
        json.dumps(audit, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=True,
    )
    return out_path
