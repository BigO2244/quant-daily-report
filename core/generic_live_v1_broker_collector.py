"""Read-only broker evidence collector and causal Live v1 closure runner."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from authority.lane_exact_plan import canonical_json
from core.generic_live_v1_ops import reject_sensitive_payload
from core.generic_live_v1_posttrade import (
    build_and_finalize_generic_live_v1_production_posttrade,
)
from core.generic_live_v1_submission import (
    _write_exclusive,
    seal_generic_live_v1_order_lifecycle,
    validate_generic_live_v1_submission_result,
)
from core.lane_reconciliation import (
    BROKER_FILL_EVIDENCE_SCHEMA,
    BROKER_ORDER_EVIDENCE_SCHEMA,
    ENDING_LANE_STATE_SCHEMA,
    seal_broker_fill_evidence,
    seal_broker_order_evidence,
    seal_ending_lane_state,
)


GENERIC_LIVE_V1_POSTTRADE_POINTER_SCHEMA = (
    "caerus.generic_live_v1_posttrade_pointer.v1"
)


class GenericLiveV1BrokerCollectorError(RuntimeError):
    """Raised when fresh broker evidence cannot close the exact session."""


class GenericLiveV1ReadBroker(Protocol):
    def get_order(self, order_id: str) -> Mapping[str, Any] | None: ...
    def list_generic_live_v1_fill_activities(
        self, date_iso: str,
    ) -> list[Mapping[str, Any]]: ...
    def get_account(self) -> Mapping[str, Any]: ...
    def get_positions(self) -> list[Mapping[str, Any]]: ...


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _finite(value: Any, *, label: str, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GenericLiveV1BrokerCollectorError(f"{label} is not numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0.0):
        raise GenericLiveV1BrokerCollectorError(f"{label} is invalid")
    return number


def _scope(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": plan["trade_date"],
        "account_id_hash": plan["account_id_hash"],
        "lane_id": plan["lane_id"],
        "lane_kind": plan["lane_kind"],
        "deployment_version": plan["deployment_version"],
        "plan_id": plan["plan_id"],
        "plan_hash": plan["content_hash"],
    }


def _status(value: Any) -> str:
    normalized = str(value or "").strip().lower().split(".")[-1]
    return {
        "new": "ACCEPTED",
        "accepted": "ACCEPTED",
        "pending_new": "PENDING",
        "pending_cancel": "PENDING",
        "partially_filled": "PARTIALLY_FILLED",
        "filled": "FILLED",
        "canceled": "CANCELED",
        "cancelled": "CANCELED",
        "rejected": "REJECTED",
        "expired": "EXPIRED",
    }.get(normalized, "PENDING")


def _safe_order(raw: Mapping[str, Any]) -> dict[str, Any]:
    safe = {
        "broker_order_id": str(raw.get("id") or raw.get("broker_order_id") or ""),
        "client_order_id": str(raw.get("client_order_id") or ""),
        "symbol": str(raw.get("symbol") or "").upper(),
        "side": str(raw.get("side") or "").upper().split(".")[-1],
        "status": _status(raw.get("status")),
        "submitted_quantity": str(raw.get("qty") or raw.get("quantity") or ""),
        "filled_quantity": str(raw.get("filled_qty") or raw.get("filled_quantity") or "0"),
    }
    reject_sensitive_payload(safe)
    return safe


def _safe_fill(raw: Mapping[str, Any]) -> dict[str, Any]:
    fee = raw.get("fee_amount", raw.get("fee"))
    if fee is None:
        raise GenericLiveV1BrokerCollectorError(
            "broker fill lacks explicit fee evidence"
        )
    safe = {
        "fill_id": str(raw.get("id") or raw.get("fill_id") or ""),
        "activity_type": str(raw.get("activity_type") or "").upper(),
        "event_time": str(raw.get("transaction_time") or raw.get("event_time") or ""),
        "broker_order_id": str(raw.get("order_id") or raw.get("broker_order_id") or ""),
        "symbol": str(raw.get("symbol") or "").upper(),
        "side": str(raw.get("side") or "").upper().split(".")[-1],
        "quantity": str(raw.get("qty") or raw.get("quantity") or ""),
        "price": str(raw.get("price") or ""),
        "fee_amount": str(fee),
    }
    reject_sensitive_payload(safe)
    return safe


def _collect_broker_evidence(
    *, broker: GenericLiveV1ReadBroker, submission: Mapping[str, Any],
    plan: Mapping[str, Any], observed_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    orders = [*plan["sell_orders"], *plan["buy_orders"]]
    broker_orders: list[dict[str, Any]] = []
    broker_fills: list[dict[str, Any]] = []
    if orders:
        order = orders[0]
        receipt = submission.get("broker_order")
        if not isinstance(receipt, Mapping):
            raise GenericLiveV1BrokerCollectorError(
                "submitted exact plan lacks a broker receipt"
            )
        raw_order = broker.get_order(str(receipt.get("broker_order_id") or ""))
        if not isinstance(raw_order, Mapping):
            raise GenericLiveV1BrokerCollectorError(
                "fresh broker order is unavailable"
            )
        safe_order = _safe_order(raw_order)
        if (
            safe_order["broker_order_id"] != receipt.get("broker_order_id")
            or safe_order["client_order_id"] != order["client_order_id"]
            or safe_order["symbol"] != order["symbol"]
            or safe_order["side"] != order["side"]
            or abs(_finite(safe_order["submitted_quantity"], label="submitted quantity") - float(order["quantity"])) > 1e-9
        ):
            raise GenericLiveV1BrokerCollectorError(
                "fresh broker order differs from exact plan/receipt"
            )
        order_source_hash = _hash(safe_order)
        broker_orders.append(seal_broker_order_evidence({
            "schema_version": BROKER_ORDER_EVIDENCE_SCHEMA,
            "observation_id": f"generic-live-v1-order:{safe_order['broker_order_id']}",
            "observed_at": observed_at, **_scope(plan),
            "order_id": order["order_id"],
            "client_order_id": order["client_order_id"],
            "broker_order_id": safe_order["broker_order_id"],
            "status": safe_order["status"],
            "submitted_quantity": float(order["quantity"]),
            "filled_quantity": _finite(
                safe_order["filled_quantity"], label="filled quantity"
            ),
            "source_hash": order_source_hash,
        }))
        raw_fills = broker.list_generic_live_v1_fill_activities(
            str(plan["trade_date"])
        )
        if not isinstance(raw_fills, list):
            raise GenericLiveV1BrokerCollectorError(
                "broker fill response is not an array"
            )
        seen_fill_ids: set[str] = set()
        for raw_fill in raw_fills:
            if not isinstance(raw_fill, Mapping):
                raise GenericLiveV1BrokerCollectorError(
                    "broker fill row is not an object"
                )
            safe_fill = _safe_fill(raw_fill)
            if safe_fill["broker_order_id"] != safe_order["broker_order_id"]:
                continue
            if (
                safe_fill["activity_type"] != "FILL"
                or safe_fill["symbol"] != order["symbol"]
                or safe_fill["side"] != order["side"]
                or not safe_fill["fill_id"]
                or safe_fill["fill_id"] in seen_fill_ids
            ):
                raise GenericLiveV1BrokerCollectorError(
                    "broker fill identity differs from exact order"
                )
            seen_fill_ids.add(safe_fill["fill_id"])
            fill_source_hash = _hash(safe_fill)
            broker_fills.append(seal_broker_fill_evidence({
                "schema_version": BROKER_FILL_EVIDENCE_SCHEMA,
                "fill_id": safe_fill["fill_id"], "event_time": safe_fill["event_time"],
                **_scope(plan), "order_id": order["order_id"],
                "client_order_id": order["client_order_id"],
                "broker_order_id": safe_order["broker_order_id"],
                "symbol": order["symbol"], "side": order["side"],
                "quantity": _finite(safe_fill["quantity"], label="fill quantity", positive=True),
                "price": _finite(safe_fill["price"], label="fill price", positive=True),
                "fee_amount": _finite(safe_fill["fee_amount"], label="fill fee"),
                "source_hash": fill_source_hash,
            }))
        if abs(
            sum(float(row["quantity"]) for row in broker_fills)
            - float(broker_orders[0]["filled_quantity"])
        ) > 1e-9:
            raise GenericLiveV1BrokerCollectorError(
                "fresh fill activities do not equal broker order filled quantity"
            )

    account = broker.get_account()
    positions = broker.get_positions()
    if not isinstance(account, Mapping) or not isinstance(positions, list):
        raise GenericLiveV1BrokerCollectorError(
            "ending broker account/positions response is invalid"
        )
    account_hash = str(account.get("id_hash") or "")
    if account_hash != plan["account_id_hash"]:
        raise GenericLiveV1BrokerCollectorError(
            "ending broker account pin differs from exact plan"
        )
    cash = _finite(account.get("cash"), label="ending cash")
    equity = _finite(account.get("equity"), label="ending equity")
    safe_account = {
        "account_id_hash": account_hash, "cash": cash, "equity": equity,
        "status": str(account.get("status") or ""),
        "trading_blocked": bool(account.get("trading_blocked", False)),
        "account_blocked": bool(account.get("account_blocked", False)),
    }
    reject_sensitive_payload(safe_account)
    state_positions: list[dict[str, Any]] = []
    for raw_position in positions:
        if not isinstance(raw_position, Mapping):
            raise GenericLiveV1BrokerCollectorError(
                "ending position row is invalid"
            )
        safe_position = {
            "symbol": str(raw_position.get("symbol") or "").upper(),
            "quantity": str(raw_position.get("qty") or raw_position.get("quantity") or ""),
            "mark": str(raw_position.get("current_price") or raw_position.get("mark") or ""),
            "market_value": str(raw_position.get("market_value") or ""),
        }
        reject_sensitive_payload(safe_position)
        quantity = _finite(safe_position["quantity"], label="ending position quantity", positive=True)
        mark = _finite(safe_position["mark"], label="ending position mark", positive=True)
        value = _finite(safe_position["market_value"], label="ending position value")
        state_positions.append({
            "symbol": safe_position["symbol"], "quantity": quantity,
            "mark": mark, "market_value": value,
            "source_hash": _hash(safe_position),
        })
    ending_source_hash = _hash({
        "account": safe_account,
        "positions": sorted(state_positions, key=lambda row: row["symbol"]),
    })
    ending = seal_ending_lane_state({
        "schema_version": ENDING_LANE_STATE_SCHEMA,
        "state_id": f"generic-live-v1-ending:{plan['trade_date']}:{ending_source_hash[:20]}",
        "as_of": observed_at, **_scope(plan), "cash": cash, "equity": equity,
        "positions": sorted(state_positions, key=lambda row: row["symbol"]),
        "source_hash": ending_source_hash,
    })
    return broker_orders, broker_fills, ending


def collect_generic_live_v1_broker_evidence(
    *, broker: GenericLiveV1ReadBroker, submission_result: Mapping[str, Any],
    exact_plan: Mapping[str, Any], observed_at: str,
    evidence_directory: Path | str,
) -> dict[str, Any]:
    """Collect and persist only typed, redacted, read-only broker evidence."""

    submission = validate_generic_live_v1_submission_result(submission_result)
    broker_orders, broker_fills, ending = _collect_broker_evidence(
        broker=broker, submission=submission, plan=exact_plan,
        observed_at=observed_at,
    )
    lifecycle = seal_generic_live_v1_order_lifecycle(
        submission_result=submission, observed_at=observed_at,
        broker_order_evidence_hash=(
            broker_orders[0]["content_hash"] if broker_orders else None
        ),
        broker_fill_evidence_hashes=[row["content_hash"] for row in broker_fills],
    )
    evidence = [*broker_orders, *broker_fills, ending, lifecycle]
    root = Path(evidence_directory)
    for artifact in evidence:
        reject_sensitive_payload(artifact)
        identity = artifact.get("content_hash")
        if not isinstance(identity, str) or len(identity) != 64:
            raise GenericLiveV1BrokerCollectorError(
                "broker evidence lacks an immutable hash"
            )
        _write_exclusive(root / f"{identity}.json", artifact)
    return {
        "submission_result": submission, "broker_orders": broker_orders,
        "broker_fills": broker_fills, "ending_state": ending,
        "order_lifecycle": lifecycle,
    }


def collect_and_finalize_generic_live_v1_posttrade(
    *, broker: GenericLiveV1ReadBroker, submission_result: Mapping[str, Any],
    exact_plan: Mapping[str, Any], observed_at: str,
    evidence_directory: Path | str, published_pointer_path: Path | str,
    rollback_handler: Callable[[str], Mapping[str, Any]], **posttrade_inputs: Any,
) -> dict[str, Any]:
    """Collect fresh read-only evidence, close causality, then publish one pointer."""

    evidence = collect_generic_live_v1_broker_evidence(
        broker=broker, submission_result=submission_result,
        exact_plan=exact_plan, observed_at=observed_at,
        evidence_directory=evidence_directory,
    )
    submission = evidence["submission_result"]
    broker_orders = evidence["broker_orders"]
    broker_fills = evidence["broker_fills"]
    ending = evidence["ending_state"]
    lifecycle = evidence["order_lifecycle"]
    closure = build_and_finalize_generic_live_v1_production_posttrade(
        submission_result=submission, exact_plan=exact_plan,
        order_lifecycle=lifecycle, broker_orders=broker_orders,
        broker_fills=broker_fills, ending_state=ending,
        rollback_handler=rollback_handler, **posttrade_inputs,
    )
    pointer = {
        "schema_version": GENERIC_LIVE_V1_POSTTRADE_POINTER_SCHEMA,
        "published_at": observed_at, "trade_date": exact_plan["trade_date"],
        "account_id_hash": exact_plan["account_id_hash"],
        "plan_hash": exact_plan["content_hash"],
        "submission_result_hash": submission["content_hash"],
        "order_lifecycle_hash": lifecycle["content_hash"],
        "broker_order_hashes": [row["content_hash"] for row in broker_orders],
        "broker_fill_hashes": [row["content_hash"] for row in broker_fills],
        "ending_state_hash": ending["content_hash"],
        "closure_hash": closure["content_hash"],
        "status": closure["status"], "generic_kill_switch_state": "ARMED",
        "broker_write_performed": False, "execution_authority": False,
        "activation_authority": False,
    }
    pointer["content_hash"] = _hash(pointer)
    reject_sensitive_payload(pointer)
    _write_exclusive(Path(published_pointer_path), pointer)
    return {"closure": closure, "pointer": pointer, "order_lifecycle": lifecycle}


__all__ = [
    "GENERIC_LIVE_V1_POSTTRADE_POINTER_SCHEMA",
    "GenericLiveV1BrokerCollectorError",
    "collect_and_finalize_generic_live_v1_posttrade",
    "collect_generic_live_v1_broker_evidence",
]
