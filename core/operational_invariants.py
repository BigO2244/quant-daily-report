from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from paper.run_manager import safe_write_text
from paper.trading_calendar import XNYS_CALENDAR_NAME, is_trading_day


STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"

RELIABILITY_GREEN = "RELIABILITY_GREEN"
RELIABILITY_YELLOW = "RELIABILITY_YELLOW"
RELIABILITY_RED = "RELIABILITY_RED"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

DEFAULT_CASH_WEIGHT_TOLERANCE = 0.02

_OK_RECONCILIATION_STATUSES = {
    "OK",
    "PASS",
    "CLEAN",
    "OK_RECONCILED",
    "RECONCILED",
    "RECONCILED_SUCCESS",
    "SUCCESS",
}
_UNRESOLVED_ORDER_STATUSES = {
    "ACCEPTED",
    "NEW",
    "OPEN",
    "PARTIALLY_FILLED",
    "PENDING",
    "PENDING_CANCEL",
    "PENDING_NEW",
    "PENDING_REPLACE",
    "SUBMITTED",
}
_TERMINAL_NEEDS_REASON = {
    "NO_ACTION",
    "SKIPPED",
    "SKIPPED_DUPLICATE",
    "MARKET_CLOSED",
    "HALTED",
    "FAILED",
    "FAIL",
    "ERROR",
    "PARTIAL",
}


def _read_json_checked(path: Path) -> tuple[dict[str, Any], str | None]:
    """Read a JSON object, distinguishing *missing* from *present-but-unreadable*.

    Returns ``(payload, error)``:
    - missing file            -> ``({}, None)``  (legitimately absent / optional)
    - corrupt / unparseable    -> ``({}, "unreadable_json:<ExcType>")``
    - present but not a JSON obj -> ``({}, "non_object_json")``
    - valid object             -> ``(payload, None)``

    Unlike a bare ``except: return {}`` swallow, a present-but-broken artifact is
    surfaced as an explicit error so the reliability report can fail closed on it
    instead of silently treating a corrupt run as a clean no-op.
    """
    if not path.exists():
        return {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # corrupt / unreadable content is NOT a silent {}
        return {}, f"unreadable_json:{type(exc).__name__}"
    if not isinstance(payload, dict):
        return {}, "non_object_json"
    return payload, None


def _read_json(path: Path) -> dict[str, Any]:
    payload, _error = _read_json_checked(path)
    return payload


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _clean_reason(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return None
    return text


def _result(
    *,
    invariant_id: str,
    status: str,
    severity: str,
    reason_code: str,
    human_summary: str,
    operator_action: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "invariant_id": invariant_id,
        "status": status,
        "severity": severity,
        "reason_code": reason_code,
        "human_summary": human_summary,
        "operator_action": operator_action,
        "evidence": dict(evidence or {}),
    }


def _counts(
    *,
    execution_payload: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    operator_summary: Mapping[str, Any],
) -> dict[str, int]:
    planned_payload_trade_count = _to_int(
        execution_payload.get("planned_payload_trade_count")
        or execution_results.get("planned_payload_trade_count")
        or operator_summary.get("planned_payload_trade_count")
    )
    executable_trades_count = _to_int(
        execution_payload.get("execution_eligible_trades_count")
        or execution_payload.get("executable_trades_count")
        or execution_results.get("executable_trades_count")
        or operator_summary.get("execution_eligible_trades_count")
        or operator_summary.get("executable_trades_count")
    )
    submitted_count = _to_int(
        execution_results.get("submitted_count")
        or execution_payload.get("submitted_count")
        or operator_summary.get("submitted_count")
        or operator_summary.get("orders_submitted_count")
    )
    accepted_count = _to_int(
        execution_results.get("accepted_count")
        or execution_payload.get("accepted_count")
        or operator_summary.get("accepted_count")
    )
    filled_count = _to_int(
        execution_results.get("orders_filled_count")
        or execution_payload.get("orders_filled_count")
        or execution_results.get("filled_count")
        or execution_payload.get("filled_count")
        or execution_results.get("posttrade_filled_orders_count")
        or execution_payload.get("posttrade_filled_orders_count")
    )
    unresolved_count = _to_int(
        execution_results.get("posttrade_unresolved_orders_count")
        or execution_payload.get("posttrade_unresolved_orders_count")
        or operator_summary.get("posttrade_unresolved_orders_count")
    )
    upstream_intent_count = max(
        planned_payload_trade_count,
        _to_int(
            execution_payload.get("planner_intended_trades_count")
            or execution_payload.get("proposed_trades_intent_count")
            or operator_summary.get("planner_intended_trades_count")
            or operator_summary.get("proposed_trades_count")
        ),
        len(_as_list(execution_payload.get("trades"))),
    )
    return {
        "planned_payload_trade_count": planned_payload_trade_count,
        "executable_trades_count": executable_trades_count,
        "submitted_count": submitted_count,
        "accepted_count": accepted_count,
        "filled_count": filled_count,
        "unresolved_count": unresolved_count,
        "upstream_intent_count": upstream_intent_count,
    }


def _order_statuses(*payloads: Mapping[str, Any]) -> list[str]:
    statuses: list[str] = []
    for payload in payloads:
        for key in ("order_lifecycle", "posttrade_resolved_orders", "broker_responses"):
            for row in _as_list(payload.get(key)):
                if not isinstance(row, Mapping):
                    continue
                nested_order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
                status = _upper(
                    row.get("resolved_order_status")
                    or row.get("latest_status")
                    or row.get("status")
                    or nested_order.get("status")
                )
                if status:
                    statuses.append(status.split(".")[-1])
    return statuses


def _execution_reason(
    *,
    execution_payload: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    operator_summary: Mapping[str, Any],
) -> str | None:
    for key in (
        "reason_code",
        "halt_reason",
        "reason",
        "execution_reason",
        "status_reason",
        "buy_phase_block_reason",
        "buy_phase_decision_reason",
        "broker_reject_status",
        "broker_reject_message",
        "retry_reason",
        "continuation_reason",
        "pretrade_halt_reason",
    ):
        for source in (execution_payload, execution_results, operator_summary):
            reason = _clean_reason(source.get(key))
            if reason:
                return reason
    return None


def _market_calendar_evidence(trade_date: Any) -> dict[str, Any]:
    parsed = _parse_trade_date(trade_date)
    if parsed is None:
        return {
            "calendar_name": XNYS_CALENDAR_NAME,
            "trade_date": str(trade_date or ""),
            "is_trading_day": None,
            "reason": "MARKET_CALENDAR_DATE_INVALID",
        }
    date_str = parsed.isoformat()
    open_session = is_trading_day(date_str)
    return {
        "calendar_name": XNYS_CALENDAR_NAME,
        "trade_date": date_str,
        "is_trading_day": open_session,
        "reason": "MARKET_OPEN_DAY" if open_session else "MARKET_CLOSED_DAY",
    }


def _target_cash_values(
    *,
    execution_payload: Mapping[str, Any],
    operator_summary: Mapping[str, Any],
    target_attainment: Mapping[str, Any],
) -> tuple[float | None, float | None, float | None]:
    target = _to_float(
        target_attainment.get("target_cash_weight")
        if target_attainment.get("target_cash_weight") is not None
        else execution_payload.get("target_cash_weight")
        if execution_payload.get("target_cash_weight") is not None
        else execution_payload.get("cash_target_weight")
        if execution_payload.get("cash_target_weight") is not None
        else operator_summary.get("cash_target_weight")
    )
    actual = _to_float(
        target_attainment.get("achieved_cash_weight")
        if target_attainment.get("achieved_cash_weight") is not None
        else execution_payload.get("achieved_cash_weight")
        if execution_payload.get("achieved_cash_weight") is not None
        else operator_summary.get("achieved_cash_weight")
    )
    drift = _to_float(target_attainment.get("cash_target_drift"))
    if drift is None and target is not None and actual is not None:
        drift = actual - target
    return target, actual, drift


def _reconciliation_status(
    *,
    reconciliation: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    operator_summary: Mapping[str, Any],
    target_attainment: Mapping[str, Any],
) -> str | None:
    for source, keys in (
        (reconciliation, ("drift_status", "comparison_status", "status", "reconciliation_status")),
        (target_attainment, ("reconciliation_status",)),
        (operator_summary, ("post_execution_recon_status",)),
        (execution_payload, ("posttrade_recon_status",)),
        (execution_results, ("posttrade_recon_status",)),
    ):
        for key in keys:
            text = str(source.get(key) or "").strip()
            if text:
                return text
    return None


def _is_reconciliation_clean(status: str | None) -> bool:
    return _upper(status) in _OK_RECONCILIATION_STATUSES


def _reconciliation_mismatch_fields(reconciliation: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "model_equity",
        "broker_equity",
        "broker_minus_model_equity_delta",
        "equity_delta",
        "model_cash",
        "broker_cash",
        "cash_delta",
        "mismatched_symbols",
        "position_mismatches",
        "missing_in_model",
        "missing_in_broker",
    )
    return {key: reconciliation.get(key) for key in keys if key in reconciliation}


def _source_probe(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
    }


def _find_sleeve_numeric_traces(run_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    audit_root = run_root / "audit"
    for path in sorted(audit_root.glob("sleeve_numeric_trace_*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        first_event = payload.get("first_event") if isinstance(payload.get("first_event"), Mapping) else {}
        rows.append(
            {
                "path": str(path),
                "status": payload.get("status"),
                "reason_code": payload.get("reason_code"),
                "sleeve_id": payload.get("sleeve_id") or first_event.get("sleeve_id"),
                "field": first_event.get("field"),
                "first_event": first_event,
            }
        )
    return rows


def _summary_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "pass": 0,
        "warn": 0,
        "fail": 0,
        "info": 0,
        "warning": 0,
        "critical": 0,
    }
    for row in results:
        status = str(row.get("status") or "").lower()
        severity = str(row.get("severity") or "").lower()
        if status in counts:
            counts[status] += 1
        if severity in counts:
            counts[severity] += 1
    return counts


def _top_non_pass_result(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for status in (STATUS_FAIL, STATUS_WARN):
        for row in results:
            if row.get("status") == status:
                return row
    return None


def classify_reliability_readiness(
    *,
    score: int,
    invariant_results: list[Mapping[str, Any]],
) -> str:
    fail_count = sum(1 for row in invariant_results if row.get("status") == STATUS_FAIL)
    if score < 80 or fail_count > 0:
        return RELIABILITY_RED
    if score >= 95:
        return RELIABILITY_GREEN
    return RELIABILITY_YELLOW


def score_execution_integrity(invariant_results: list[Mapping[str, Any]]) -> int:
    score = 100
    for row in invariant_results:
        status = str(row.get("status") or "").upper()
        severity = str(row.get("severity") or "").lower()
        if status == STATUS_FAIL and severity == SEVERITY_CRITICAL:
            score -= 35
        elif status == STATUS_FAIL:
            score -= 20
        elif status == STATUS_WARN or severity == SEVERITY_WARNING:
            score -= 8
    return max(0, min(100, score))


def _history_path(outputs_root: str | Path = "outputs") -> Path:
    return Path(outputs_root) / "reliability" / "reliability_history.json"


def _read_history(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    rows = payload.get("history") if isinstance(payload, Mapping) else None
    if isinstance(rows, list):
        return [dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def _mean_score(rows: list[Mapping[str, Any]]) -> float | None:
    scores = [
        float(row["score"])
        for row in rows
        if isinstance(row.get("score"), (int, float))
    ]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


def _parse_trade_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _history_entry_from_report(report: Mapping[str, Any]) -> dict[str, Any]:
    summary_counts = report.get("summary_counts") if isinstance(report.get("summary_counts"), Mapping) else {}
    return {
        "trade_date": str(report.get("trade_date") or ""),
        "run_id": str(report.get("run_id") or ""),
        "score": int(report.get("score") or 0),
        "classification": str(report.get("classification") or ""),
        "fail_count": _to_int(summary_counts.get("fail")),
        "warn_count": _to_int(summary_counts.get("warn")),
        "top_failure_reason": report.get("top_failure_reason"),
    }


def compute_reliability_trend_metrics(
    history_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(row) for row in history_rows]
    rows.sort(key=lambda row: (str(row.get("trade_date") or ""), str(row.get("run_id") or "")))
    trailing_5 = rows[-5:]
    trailing_20 = rows[-20:]

    clean_run_streak = 0
    for row in reversed(rows):
        if row.get("classification") == RELIABILITY_GREEN:
            clean_run_streak += 1
        else:
            break

    last_fail: dict[str, Any] | None = None
    for row in reversed(rows):
        if _to_int(row.get("fail_count")) > 0 or row.get("classification") == RELIABILITY_RED:
            last_fail = row
            break

    current_date = _parse_trade_date(rows[-1].get("trade_date")) if rows else None
    last_fail_date = _parse_trade_date(last_fail.get("trade_date")) if last_fail else None
    days_since_last_fail = (
        (current_date - last_fail_date).days
        if current_date is not None and last_fail_date is not None
        else None
    )

    denominator = len(trailing_20)
    fail_count = sum(1 for row in trailing_20 if _to_int(row.get("fail_count")) > 0)
    warn_count = sum(
        1
        for row in trailing_20
        if _to_int(row.get("warn_count")) > 0 and _to_int(row.get("fail_count")) == 0
    )

    return {
        "trailing_5_day_score": _mean_score(trailing_5),
        "trailing_20_day_score": _mean_score(trailing_20),
        "fail_frequency": round(fail_count / denominator, 4) if denominator else 0.0,
        "warn_frequency": round(warn_count / denominator, 4) if denominator else 0.0,
        "clean_run_streak": clean_run_streak,
        "days_since_last_fail": days_since_last_fail,
        "last_fail_reason": last_fail.get("top_failure_reason") if last_fail else None,
        "history_window_count": len(trailing_20),
    }


def build_reliability_readiness_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    trend = report.get("trend_metrics") if isinstance(report.get("trend_metrics"), Mapping) else {}
    return {
        "schema_version": "reliability_readiness.v1",
        "trade_date": str(report.get("trade_date") or ""),
        "run_id": str(report.get("run_id") or ""),
        "current_classification": report.get("classification"),
        "current_score": report.get("score"),
        "clean_run_streak": trend.get("clean_run_streak"),
        "days_since_last_fail": trend.get("days_since_last_fail"),
        "last_fail_reason": trend.get("last_fail_reason"),
        "trailing_20_day_score": trend.get("trailing_20_day_score"),
        "reliability_report": report.get("source_artifact_paths", {}).get("execution_reliability_report")
        if isinstance(report.get("source_artifact_paths"), Mapping)
        else None,
    }


def build_execution_reliability_report(
    *,
    run_root: str | Path,
    trade_date: str,
    run_id: str,
    cash_weight_tolerance: float = DEFAULT_CASH_WEIGHT_TOLERANCE,
    outputs_root: str | Path = "outputs",
) -> dict[str, Any]:
    root = Path(run_root)
    execution_payload_path = root / "execution_payload.json"
    execution_results_path = root / "execution_results.json"
    operator_summary_path = root / "operator_summary.json"
    execution_integrity_path = root / "audit" / "execution_integrity.json"
    target_attainment_path = root / "audit" / f"execution_target_attainment_{trade_date}.json"
    posttrade_recon_path = root / "broker" / f"recon_posttrade_{trade_date}.json"
    precompute_payload_path = Path("outputs") / "precompute" / trade_date / "planned_execution_payload.json"
    precompute_contract_path = Path("outputs") / "precompute" / trade_date / "contract.json"

    _artifact_reads = {
        "execution_payload": _read_json_checked(execution_payload_path),
        "execution_results": _read_json_checked(execution_results_path),
        "operator_summary": _read_json_checked(operator_summary_path),
        "execution_integrity": _read_json_checked(execution_integrity_path),
        "target_attainment": _read_json_checked(target_attainment_path),
        "reconciliation": _read_json_checked(posttrade_recon_path),
        "precompute_payload": _read_json_checked(precompute_payload_path),
        "precompute_contract": _read_json_checked(precompute_contract_path),
    }
    execution_payload = _artifact_reads["execution_payload"][0]
    execution_results = _artifact_reads["execution_results"][0]
    operator_summary = _artifact_reads["operator_summary"][0]
    execution_integrity = _artifact_reads["execution_integrity"][0]
    target_attainment = _artifact_reads["target_attainment"][0]
    reconciliation = _artifact_reads["reconciliation"][0]
    precompute_payload = _artifact_reads["precompute_payload"][0]
    precompute_contract = _artifact_reads["precompute_contract"][0]
    artifact_read_errors = [
        {"artifact": name, "error": error}
        for name, (_payload, error) in _artifact_reads.items()
        if error is not None
    ]

    counts = _counts(
        execution_payload=execution_payload,
        execution_results=execution_results,
        operator_summary=operator_summary,
    )
    reason = _execution_reason(
        execution_payload=execution_payload,
        execution_results=execution_results,
        operator_summary=operator_summary,
    )
    market_calendar = _market_calendar_evidence(trade_date)
    expected_market_closed_day = market_calendar.get("reason") == "MARKET_CLOSED_DAY"
    results: list[dict[str, Any]] = []

    if artifact_read_errors:
        results.append(
            _result(
                invariant_id="execution_artifact_readable",
                status=STATUS_FAIL,
                severity=SEVERITY_CRITICAL,
                reason_code="execution_artifact_unreadable",
                human_summary=(
                    "A required execution artifact is present but unreadable/corrupt; "
                    "a corrupt run must not be silently classified as a clean no-op."
                ),
                operator_action=(
                    "Inspect the corrupt artifact(s) and treat the run as failed until the "
                    "evidence is restored; do not conclude NO_ACTION or success."
                ),
                evidence={"artifact_read_errors": artifact_read_errors},
            )
        )

    if expected_market_closed_day:
        results.append(
            _result(
                invariant_id="planned_payload_nonempty_zero_execution",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="MARKET_CLOSED_DAY",
                human_summary="Zero submitted orders are expected on a full market-closed day.",
                operator_action="No operator action required.",
                evidence=counts | {"market_calendar": market_calendar},
            )
        )
    elif counts["planned_payload_trade_count"] > 0 and (
        counts["executable_trades_count"] == 0 or counts["submitted_count"] == 0
    ):
        results.append(
            _result(
                invariant_id="planned_payload_nonempty_zero_execution",
                status=STATUS_FAIL,
                severity=SEVERITY_CRITICAL,
                reason_code="planned_payload_trades_dropped_before_execution",
                human_summary="A nonempty planned execution payload produced zero executable or submitted trades.",
                operator_action="Halt the run, inspect planned payload handoff, and do not treat the day as legitimate NO_ACTION.",
                evidence=counts,
            )
        )
    else:
        results.append(
            _result(
                invariant_id="planned_payload_nonempty_zero_execution",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="planned_payload_execution_count_consistent",
                human_summary="Planned payload and execution counts are consistent with the observed run outcome.",
                operator_action="No operator action required.",
                evidence=counts,
            )
        )

    if counts["submitted_count"] > 0 and counts["accepted_count"] == 0:
        results.append(
            _result(
                invariant_id="submitted_orders_without_acceptance",
                status=STATUS_FAIL,
                severity=SEVERITY_CRITICAL,
                reason_code="submitted_orders_not_accepted_by_broker",
                human_summary="Orders were submitted but none were accepted by the broker.",
                operator_action="Inspect broker responses and retry only after the rejection or connectivity cause is classified.",
                evidence=counts | {"broker_responses": _as_list(execution_results.get("broker_responses"))[:5]},
            )
        )
    else:
        results.append(
            _result(
                invariant_id="submitted_orders_without_acceptance",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="broker_acceptance_count_consistent",
                human_summary="Submitted and accepted order counts do not indicate a total acceptance failure.",
                operator_action="No operator action required.",
                evidence=counts,
            )
        )

    statuses = _order_statuses(execution_payload, execution_results)
    unresolved_statuses = sorted({status for status in statuses if status in _UNRESOLVED_ORDER_STATUSES})
    if counts["accepted_count"] > 0 and counts["filled_count"] == 0 and (
        counts["unresolved_count"] > 0 or unresolved_statuses
    ):
        results.append(
            _result(
                invariant_id="accepted_orders_zero_fills_unresolved",
                status=STATUS_WARN,
                severity=SEVERITY_WARNING,
                reason_code="accepted_orders_unfilled_with_unresolved_status",
                human_summary="Broker accepted orders but no fills are confirmed and unresolved order status remains.",
                operator_action="Refresh broker order state and reconciliation before concluding execution success or no-action.",
                evidence=counts | {"unresolved_statuses": unresolved_statuses},
            )
        )
    else:
        results.append(
            _result(
                invariant_id="accepted_orders_zero_fills_unresolved",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="accepted_fill_resolution_consistent",
                human_summary="Accepted order fill state does not show an unresolved zero-fill condition.",
                operator_action="No operator action required.",
                evidence=counts | {"unresolved_statuses": unresolved_statuses},
            )
        )

    target_cash_weight, actual_cash_weight, cash_drift = _target_cash_values(
        execution_payload=execution_payload,
        operator_summary=operator_summary,
        target_attainment=target_attainment,
    )
    target_status = str(target_attainment.get("status") or "").strip()
    underdeployment_reason = _clean_reason(target_attainment.get("underdeployment_reason_code"))
    underdeployment_classification = _clean_reason(target_attainment.get("underdeployment_classification"))
    target_operator_action = _clean_reason(target_attainment.get("operator_action"))
    cash_drift_abs = abs(cash_drift) if cash_drift is not None else None
    if cash_drift_abs is not None and cash_drift_abs > float(cash_weight_tolerance):
        reason_code = underdeployment_reason or "target_cash_materially_differs_from_actual_cash"
        results.append(
            _result(
                invariant_id="target_cash_actual_cash_drift",
                status=STATUS_WARN,
                severity=SEVERITY_WARNING,
                reason_code=reason_code,
                human_summary="Actual cash weight materially differs from intended target cash weight after execution.",
                operator_action=target_operator_action
                or "Inspect target-attainment and posttrade cash artifacts; classify as pending fills, stale snapshot, or execution underdeployment.",
                evidence={
                    "target_cash_weight": target_cash_weight,
                    "actual_cash_weight": actual_cash_weight,
                    "cash_target_drift": cash_drift,
                    "cash_weight_tolerance": float(cash_weight_tolerance),
                    "target_attainment_status": target_status or None,
                    "underdeployment_classification": underdeployment_classification,
                    "underdeployment_reason_code": underdeployment_reason,
                    "residual_undeployed_cash": target_attainment.get("residual_undeployed_cash"),
                    "pending_buy_count": target_attainment.get("pending_buy_count"),
                    "partial_buy_count": target_attainment.get("partial_buy_count"),
                    "posttrade_unresolved_orders_count": target_attainment.get("posttrade_unresolved_orders_count"),
                },
            )
        )
    else:
        results.append(
            _result(
                invariant_id="target_cash_actual_cash_drift",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="target_cash_within_tolerance_or_unavailable",
                human_summary="Cash target attainment is within tolerance or not available for this observe-first report.",
                operator_action="No operator action required unless target-attainment artifacts are expected for this run.",
                evidence={
                    "target_cash_weight": target_cash_weight,
                    "actual_cash_weight": actual_cash_weight,
                    "cash_target_drift": cash_drift,
                    "cash_weight_tolerance": float(cash_weight_tolerance),
                    "target_attainment_status": target_status or None,
                },
            )
        )

    recon_status = _reconciliation_status(
        reconciliation=reconciliation,
        execution_payload=execution_payload,
        execution_results=execution_results,
        operator_summary=operator_summary,
        target_attainment=target_attainment,
    )
    recon_mismatch = bool(recon_status and not _is_reconciliation_clean(recon_status))
    if recon_mismatch:
        results.append(
            _result(
                invariant_id="model_broker_reconciliation",
                status=STATUS_FAIL,
                severity=SEVERITY_CRITICAL,
                reason_code="model_broker_reconciliation_mismatch",
                human_summary="Model and broker reconciliation is not clean.",
                operator_action="Stop treating execution as reconciled, inspect broker/model mismatch fields, and repair model state only from broker-authoritative evidence.",
                evidence={"reconciliation_status": recon_status} | _reconciliation_mismatch_fields(reconciliation),
            )
        )
    else:
        results.append(
            _result(
                invariant_id="model_broker_reconciliation",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="model_broker_reconciliation_clean_or_not_required",
                human_summary="No model/broker reconciliation mismatch is visible in available artifacts.",
                operator_action="No operator action required.",
                evidence={"reconciliation_status": recon_status},
            )
        )

    precompute_statuses = [
        _upper(precompute_payload.get("bundle_status")),
        _upper(precompute_contract.get("bundle_status")),
        _upper(execution_payload.get("bundle_status")),
        _upper(operator_summary.get("bundle_status")),
    ]
    stale_markers = [
        status
        for status in precompute_statuses
        if not expected_market_closed_day and ("STALE" in status or status in {"FAILED", "MISSING"})
    ]
    expected_precompute = not expected_market_closed_day and (
        _upper(execution_payload.get("execution_source")) == "PLANNED_PAYLOAD_EXACT"
        or counts["planned_payload_trade_count"] > 0
    )
    missing_precompute = expected_precompute and not precompute_payload_path.exists()
    if missing_precompute or stale_markers:
        results.append(
            _result(
                invariant_id="required_precompute_artifacts",
                status=STATUS_FAIL,
                severity=SEVERITY_CRITICAL,
                reason_code="missing_or_stale_required_precompute_artifact",
                human_summary="A required precompute artifact is missing, failed, or stale.",
                operator_action="Fail closed until the precompute bundle and planned execution payload are regenerated for the trade date.",
                evidence={
                    "expected_precompute": expected_precompute,
                    "stale_markers": stale_markers,
                    "market_calendar": market_calendar,
                    "planned_execution_payload": _source_probe(precompute_payload_path),
                    "precompute_contract": _source_probe(precompute_contract_path),
                },
            )
        )
    else:
        results.append(
            _result(
                invariant_id="required_precompute_artifacts",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="required_precompute_artifacts_available_or_not_required",
                human_summary="Required precompute artifacts are available or not required by this run.",
                operator_action="No operator action required.",
                evidence={
                    "expected_precompute": expected_precompute,
                    "market_calendar": market_calendar,
                    "planned_execution_payload": _source_probe(precompute_payload_path),
                    "precompute_contract": _source_probe(precompute_contract_path),
                },
            )
        )

    sleeve_traces = _find_sleeve_numeric_traces(root)
    blocking_sleeve_traces = [
        trace for trace in sleeve_traces if _upper(trace.get("status")) in {"BLOCKING", "FAIL", "FAILED"}
    ]
    if blocking_sleeve_traces:
        first = blocking_sleeve_traces[0]
        results.append(
            _result(
                invariant_id="sleeve_numeric_finiteness",
                status=STATUS_FAIL,
                severity=SEVERITY_CRITICAL,
                reason_code=str(first.get("reason_code") or "sleeve_numeric_non_finite"),
                human_summary="A sleeve/equity/allocation numeric trace recorded a non-finite state.",
                operator_action="Keep affected sleeve output out of execution and inspect the first numeric trace event before allowing cash routing to stand.",
                evidence={
                    "sleeve_id": first.get("sleeve_id"),
                    "field": first.get("field"),
                    "first_event": first.get("first_event"),
                    "trace_path": first.get("path"),
                    "trace_count": len(blocking_sleeve_traces),
                },
            )
        )
    else:
        results.append(
            _result(
                invariant_id="sleeve_numeric_finiteness",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="no_blocking_sleeve_numeric_trace",
                human_summary="No blocking non-finite sleeve numeric trace is visible in the run audit artifacts.",
                operator_action="No operator action required.",
                evidence={"trace_count": len(sleeve_traces)},
            )
        )

    terminal_status = _upper(
        execution_payload.get("execution_status")
        or execution_payload.get("status")
        or execution_results.get("status")
        or operator_summary.get("terminal_status")
    )
    operator_status = str(execution_payload.get("operator_execution_status") or operator_summary.get("operator_execution_status") or "").strip().lower()
    needs_reason = (
        terminal_status in _TERMINAL_NEEDS_REASON
        or operator_status in {"failed", "partial", "skipped"}
    )
    legitimate_empty_no_action = terminal_status == "NO_ACTION" and counts["upstream_intent_count"] == 0
    if needs_reason and not legitimate_empty_no_action and not reason:
        results.append(
            _result(
                invariant_id="terminal_status_requires_reason",
                status=STATUS_FAIL,
                severity=SEVERITY_CRITICAL,
                reason_code="terminal_execution_status_missing_reason",
                human_summary="A terminal skipped, no-action, halted, failed, or partial status has no explicit reason.",
                operator_action="Do not publish operator reporting until the terminal reason_code and human explanation are classified.",
                evidence={
                    "terminal_status": terminal_status,
                    "operator_execution_status": operator_status,
                    "upstream_intent_count": counts["upstream_intent_count"],
                    "reason": reason,
                },
            )
        )
    else:
        results.append(
            _result(
                invariant_id="terminal_status_requires_reason",
                status=STATUS_PASS,
                severity=SEVERITY_INFO,
                reason_code="terminal_status_reason_present_or_legitimate_no_action",
                human_summary="Terminal status has an explicit reason or is a legitimate empty no-action run.",
                operator_action="No operator action required.",
                evidence={
                    "terminal_status": terminal_status,
                    "operator_execution_status": operator_status,
                    "upstream_intent_count": counts["upstream_intent_count"],
                    "reason": reason,
                },
            )
        )

    for finding in _as_list(execution_integrity.get("findings")):
        if not isinstance(finding, Mapping):
            continue
        severity = STATUS_FAIL if _upper(finding.get("severity")) == "FAIL" else STATUS_WARN
        results.append(
            _result(
                invariant_id=f"execution_integrity.{finding.get('code') or 'finding'}",
                status=severity,
                severity=SEVERITY_CRITICAL if severity == STATUS_FAIL else SEVERITY_WARNING,
                reason_code=str(finding.get("code") or "execution_integrity_finding"),
                human_summary=str(finding.get("message") or "Execution integrity emitted a finding."),
                operator_action="Inspect audit/execution_integrity.json and classify the lineage/count discrepancy before treating execution as clean.",
                evidence={"execution_integrity_status": execution_integrity.get("status")},
            )
        )

    score = score_execution_integrity(results)
    classification = classify_reliability_readiness(
        score=score,
        invariant_results=results,
    )
    if any(row["status"] == STATUS_FAIL for row in results):
        overall_status = STATUS_FAIL
    elif any(row["status"] == STATUS_WARN for row in results):
        overall_status = STATUS_WARN
    else:
        overall_status = STATUS_PASS
    recommended_actions = []
    seen_actions = set()
    for row in results:
        if row["status"] == STATUS_PASS:
            continue
        action = str(row.get("operator_action") or "").strip()
        if action and action not in seen_actions:
            seen_actions.add(action)
            recommended_actions.append(action)
    top_result = _top_non_pass_result(results)
    summary_counts = _summary_counts(results)
    history_entry = {
        "trade_date": str(trade_date),
        "run_id": str(run_id),
        "score": score,
        "classification": classification,
        "fail_count": summary_counts["fail"],
        "warn_count": summary_counts["warn"],
        "top_failure_reason": top_result.get("reason_code") if top_result else None,
    }
    trend_metrics = compute_reliability_trend_metrics(
        _read_history(_history_path(outputs_root)) + [history_entry]
    )

    source_artifacts = {
        "execution_payload": str(execution_payload_path),
        "execution_results": str(execution_results_path),
        "operator_summary": str(operator_summary_path),
        "execution_integrity": str(execution_integrity_path),
        "execution_target_attainment": str(target_attainment_path),
        "posttrade_reconciliation": str(posttrade_recon_path),
        "planned_execution_payload": str(precompute_payload_path),
        "precompute_contract": str(precompute_contract_path),
        "sleeve_numeric_traces": [trace["path"] for trace in sleeve_traces],
    }
    return {
        "schema_version": "execution_reliability_report.v1",
        "run_id": str(run_id),
        "trade_date": str(trade_date),
        "score": score,
        "classification": classification,
        "readiness_classification": classification,
        "overall_status": overall_status,
        "top_failure_invariant_id": top_result.get("invariant_id") if top_result else None,
        "top_failure_reason": top_result.get("reason_code") if top_result else None,
        "top_failure_summary": top_result.get("human_summary") if top_result else None,
        "invariant_results": results,
        "summary_counts": summary_counts,
        "trend_metrics": trend_metrics,
        "recommended_operator_actions": recommended_actions,
        "operator_action_required": bool(recommended_actions),
        "market_calendar": market_calendar,
        "source_artifact_paths": source_artifacts,
    }


def write_execution_reliability_report(
    *,
    run_root: str | Path,
    trade_date: str,
    run_id: str,
    cash_weight_tolerance: float = DEFAULT_CASH_WEIGHT_TOLERANCE,
    outputs_root: str | Path = "outputs",
) -> Path:
    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=trade_date,
        run_id=run_id,
        cash_weight_tolerance=cash_weight_tolerance,
        outputs_root=outputs_root,
    )
    out_path = Path(run_root) / "audit" / f"execution_reliability_report_{trade_date}.json"
    report["source_artifact_paths"]["execution_reliability_report"] = str(out_path)
    safe_write_text(
        out_path,
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=True,
    )
    history_path = _history_path(outputs_root)
    history_rows = _read_history(history_path)
    history_rows.append(_history_entry_from_report(report))
    history_payload = {
        "schema_version": "reliability_history.v1",
        "history": history_rows,
    }
    safe_write_text(
        history_path,
        json.dumps(history_payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=True,
    )
    readiness_path = Path(outputs_root) / "reliability" / "reliability_readiness.json"
    readiness_payload = build_reliability_readiness_payload(report)
    readiness_payload["reliability_report"] = str(out_path)
    readiness_payload["reliability_history"] = str(history_path)
    safe_write_text(
        readiness_path,
        json.dumps(readiness_payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=True,
    )
    return out_path
