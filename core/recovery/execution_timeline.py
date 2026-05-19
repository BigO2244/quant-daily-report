from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TimelineEventType(str, Enum):
    PRECOMPUTE_VALIDATED = "PRECOMPUTE_VALIDATED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    SELL_ORDERS_SUBMITTED = "SELL_ORDERS_SUBMITTED"
    PARTIAL_FILL_OBSERVED = "PARTIAL_FILL_OBSERVED"
    SELL_PHASE_TIMEOUT = "SELL_PHASE_TIMEOUT"
    BUY_PHASE_BLOCKED = "BUY_PHASE_BLOCKED"
    POSTTRADE_CAPTURE_STARTED = "POSTTRADE_CAPTURE_STARTED"
    RECON_CAPTURE_FAILED = "RECON_CAPTURE_FAILED"
    EVENTUAL_SETTLEMENT_OBSERVED = "EVENTUAL_SETTLEMENT_OBSERVED"
    RECOVERY_CANDIDATE_IDENTIFIED = "RECOVERY_CANDIDATE_IDENTIFIED"
    RECOVERY_EXECUTED = "RECOVERY_EXECUTED"
    RECOVERY_RECONCILED = "RECOVERY_RECONCILED"


@dataclass(frozen=True)
class TimelineEvent:
    event_type: TimelineEventType
    timestamp: str | None
    source: str
    details: dict[str, Any]

    def to_artifact(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "source": self.source,
            "details": self.details,
        }


def normalize_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _sort_key(event: TimelineEvent) -> tuple[str, str]:
    return (event.timestamp or "9999-12-31T23:59:59Z", event.event_type.value)


def reconstruct_execution_timeline(
    *,
    source_run_id: str,
    trade_date: str,
    execution_payload: dict[str, Any] | None = None,
    precompute_validation: dict[str, Any] | None = None,
    broker_orders: list[dict[str, Any]] | None = None,
    fills: list[dict[str, Any]] | None = None,
    recovery_summary: dict[str, Any] | None = None,
    posttrade_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution_payload = dict(execution_payload or {})
    precompute_validation = dict(precompute_validation or {})
    recovery_summary = dict(recovery_summary or {})
    posttrade_reconciliation = dict(posttrade_reconciliation or {})
    broker_orders = list(broker_orders or [])
    fills = list(fills or [])
    events: list[TimelineEvent] = []

    if precompute_validation.get("status") == "OK":
        events.append(
            TimelineEvent(
                TimelineEventType.PRECOMPUTE_VALIDATED,
                normalize_timestamp(precompute_validation.get("validated_at")),
                "precompute_bundle_validation",
                {"status": precompute_validation.get("status")},
            )
        )

    execution_started = execution_payload.get("actual_execution_start_et") or execution_payload.get("created_at")
    if execution_started or execution_payload:
        events.append(
            TimelineEvent(
                TimelineEventType.EXECUTION_STARTED,
                normalize_timestamp(execution_started),
                "execution_payload",
                {
                    "execution_status": execution_payload.get("execution_status"),
                    "submitted_count": execution_payload.get("submitted_count"),
                    "accepted_count": execution_payload.get("accepted_count"),
                },
            )
        )

    sell_orders = [
        order for order in broker_orders if str(order.get("side") or "").lower() == "sell"
    ]
    if sell_orders:
        first_submit = min(
            (normalize_timestamp(order.get("submitted_at")) for order in sell_orders if order.get("submitted_at")),
            default=None,
        )
        events.append(
            TimelineEvent(
                TimelineEventType.SELL_ORDERS_SUBMITTED,
                first_submit,
                "broker_orders",
                {"sell_order_count": len(sell_orders)},
            )
        )

    partial_orders = [
        order
        for order in sell_orders
        if str(order.get("status") or "").lower().replace("orderstatus.", "") == "partially_filled"
        or (
            float(order.get("filled_qty") or 0.0) > 0.0
            and float(order.get("filled_qty") or 0.0) < float(order.get("qty") or 0.0)
        )
    ]
    if partial_orders:
        events.append(
            TimelineEvent(
                TimelineEventType.PARTIAL_FILL_OBSERVED,
                min(
                    (normalize_timestamp(order.get("filled_at") or order.get("submitted_at")) for order in partial_orders),
                    default=None,
                ),
                "broker_orders",
                {"partial_order_count": len(partial_orders)},
            )
        )

    halt_reason = str(execution_payload.get("halt_reason") or "").lower()
    execution_reason = str(execution_payload.get("execution_reason") or "").lower()
    if "sell_phase_timeout" in halt_reason or "timeout_waiting_for_sell_completion" in halt_reason:
        events.append(
            TimelineEvent(
                TimelineEventType.SELL_PHASE_TIMEOUT,
                normalize_timestamp(execution_payload.get("completed_at")),
                "execution_payload",
                {"halt_reason": execution_payload.get("halt_reason")},
            )
        )
    if "sell_phase_timeout" in halt_reason or execution_payload.get("submitted_count"):
        buys_submitted = [
            order for order in broker_orders if str(order.get("side") or "").lower() == "buy"
        ]
        if not buys_submitted:
            events.append(
                TimelineEvent(
                    TimelineEventType.BUY_PHASE_BLOCKED,
                    normalize_timestamp(execution_payload.get("completed_at")),
                    "execution_payload",
                    {"reason": "no_buy_orders_observed_after_sell_submission"},
                )
            )
    if "posttrade" in execution_reason or "posttrade" in halt_reason:
        events.append(
            TimelineEvent(
                TimelineEventType.POSTTRADE_CAPTURE_STARTED,
                normalize_timestamp(execution_payload.get("completed_at")),
                "execution_payload",
                {"execution_reason": execution_payload.get("execution_reason")},
            )
        )
    if "posttrade_state_capture_failed" in halt_reason:
        events.append(
            TimelineEvent(
                TimelineEventType.RECON_CAPTURE_FAILED,
                normalize_timestamp(execution_payload.get("completed_at")),
                "execution_payload",
                {"halt_reason": execution_payload.get("halt_reason")},
            )
        )

    terminal_sell_fills = [
        fill for fill in fills if str(fill.get("side") or "").lower() == "sell"
    ]
    if terminal_sell_fills and posttrade_reconciliation.get("verdict") == "PASS":
        events.append(
            TimelineEvent(
                TimelineEventType.EVENTUAL_SETTLEMENT_OBSERVED,
                max(
                    (normalize_timestamp(fill.get("transaction_time")) for fill in terminal_sell_fills),
                    default=None,
                ),
                "fills_and_reconciliation",
                {
                    "sell_fill_count": len(terminal_sell_fills),
                    "reconciliation_status": posttrade_reconciliation.get("drift_status")
                    or posttrade_reconciliation.get("verdict"),
                },
            )
        )

    if recovery_summary.get("verdict") in {"SIMULATION_PASS", "PASS"}:
        events.append(
            TimelineEvent(
                TimelineEventType.RECOVERY_CANDIDATE_IDENTIFIED,
                normalize_timestamp(recovery_summary.get("recovery_timestamp") or recovery_summary.get("generated_at")),
                "recovery_summary",
                {"verdict": recovery_summary.get("verdict")},
            )
        )
    if recovery_summary.get("verdict") == "PASS":
        events.append(
            TimelineEvent(
                TimelineEventType.RECOVERY_EXECUTED,
                normalize_timestamp(recovery_summary.get("completed_at") or recovery_summary.get("recovery_timestamp")),
                "recovery_summary",
                {"orders_submitted_count": recovery_summary.get("orders_submitted_count")},
            )
        )
        if not recovery_summary.get("remaining_drift"):
            events.append(
                TimelineEvent(
                    TimelineEventType.RECOVERY_RECONCILED,
                    normalize_timestamp(recovery_summary.get("completed_at") or recovery_summary.get("recovery_timestamp")),
                    "recovery_summary",
                    {"remaining_drift_count": 0},
                )
            )

    ordered = sorted(events, key=_sort_key)
    return {
        "source_failed_run_id": source_run_id,
        "trade_date": trade_date,
        "event_count": len(ordered),
        "events": [event.to_artifact() for event in ordered],
    }

