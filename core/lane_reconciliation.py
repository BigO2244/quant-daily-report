"""Generic, fail-closed reconciliation for one executable Caerus lane.

The contract covers exact plan -> WAL intent -> broker order -> fill -> ending
positions and cash.  Inputs are explicit hash-bound evidence; this module does
not call a broker, read runtime configuration, or authorize execution.

Only exact-lineage fills are transformed into ``reconciled_fills``.  Those rows
are split by the plan's sleeve contributions and contain the economic fields
needed by a downstream accounting-journal builder.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.lane_oms import LANE_OMS_INTENT_SCHEMA, LaneOmsError, validate_lane_oms_intent


LANE_RECONCILIATION_SCHEMA = "caerus.lane_reconciliation.v1"
BROKER_ORDER_EVIDENCE_SCHEMA = "caerus.broker_order_evidence.v1"
BROKER_FILL_EVIDENCE_SCHEMA = "caerus.broker_fill_evidence.v1"
ENDING_LANE_STATE_SCHEMA = "caerus.ending_lane_state.v1"
RECONCILED_FILL_SCHEMA = "caerus.reconciled_fill.v1"

RECONCILIATION_STATUSES = frozenset({"PASS", "PARTIAL", "REJECTED", "UNRESOLVED"})
TERMINAL_ORDER_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})
ALL_ORDER_STATUSES = TERMINAL_ORDER_STATUSES | frozenset(
    {"SUBMITTED", "ACCEPTED", "PENDING", "PARTIALLY_FILLED"}
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_TOLERANCE = 1e-8

_SCOPE_FIELDS = frozenset(
    {
        "trade_date", "account_id_hash", "lane_id", "lane_kind",
        "deployment_version", "plan_id", "plan_hash",
    }
)
_ORDER_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version", "observation_id", "observed_at", "order_id",
        "client_order_id", "broker_order_id", "status", "submitted_quantity",
        "filled_quantity", "source_hash", "content_hash",
    }
) | _SCOPE_FIELDS
_FILL_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version", "fill_id", "event_time", "order_id", "client_order_id",
        "broker_order_id", "symbol", "side", "quantity", "price", "fee_amount",
        "source_hash", "content_hash",
    }
) | _SCOPE_FIELDS
_ENDING_STATE_FIELDS = frozenset(
    {
        "schema_version", "state_id", "as_of", "cash", "equity", "positions",
        "source_hash", "content_hash",
    }
) | _SCOPE_FIELDS
_ENDING_POSITION_FIELDS = frozenset(
    {"symbol", "quantity", "mark", "market_value", "source_hash"}
)
_RECONCILED_FILL_FIELDS = frozenset(
    {
        "schema_version", "reconciled_fill_id", "trade_date", "event_time",
        "account_id_hash", "lane_id", "lane_kind", "deployment_version",
        "session_id", "session_hash", "allocation_id", "allocation_hash",
        "target_hash", "plan_id", "plan_hash", "order_id", "client_order_id",
        "broker_order_id", "fill_id", "symbol", "side", "quantity", "price",
        "gross_amount", "fee_amount", "net_amount", "sleeve_id", "decision_id",
        "decision_hash", "order_fraction", "planned_contribution_quantity",
        "fill_allocation_fraction", "exact_order_hash", "broker_fill_source_hash",
        "content_hash",
    }
)
_RECONCILIATION_FIELDS = frozenset(
    {
        "schema_version", "reconciliation_id", "reconciled_at", "status",
        "reason_codes", "execution_authority", "broker_call_performed",
        "halt_required", "escalation_required", "accounting_ready",
        "trade_date", "account_id_hash", "lane_id", "lane_kind",
        "deployment_version", "session_id", "session_hash", "allocation_id",
        "allocation_hash", "target_hash", "plan_id", "plan_hash",
        "reconciliation_policy_hash", "intended_order_count", "wal_intent_count",
        "broker_order_count", "broker_fill_count", "terminal_order_count",
        "intended_orders", "wal_intents", "broker_orders", "broker_fills",
        "position_reconciliation", "cash_reconciliation", "nav_reconciliation",
        "reconciled_fills", "source_hashes", "content_hash",
    }
)
_INTENDED_SUMMARY_FIELDS = frozenset(
    {"order_id", "client_order_id", "symbol", "side", "quantity", "exact_order_hash"}
)
_WAL_SUMMARY_FIELDS = frozenset({"intent_id", "order_id", "intent_hash"})
_ORDER_SUMMARY_FIELDS = frozenset(
    {"order_id", "broker_order_id", "status", "submitted_quantity", "filled_quantity", "evidence_hash"}
)
_FILL_SUMMARY_FIELDS = frozenset(
    {"fill_id", "order_id", "broker_order_id", "quantity", "price", "fee_amount", "evidence_hash"}
)
_POSITION_PROOF_FIELDS = frozenset(
    {"status", "starting", "expected_from_fills", "actual", "deltas"}
)
_CASH_PROOF_FIELDS = frozenset(
    {
        "status", "starting_cash", "buy_notional", "sell_notional", "fees",
        "expected_from_fills", "actual", "delta",
    }
)
_NAV_PROOF_FIELDS = frozenset(
    {
        "status", "cash", "positions_market_value", "calculated_equity",
        "broker_equity", "delta", "as_of",
    }
)


class LaneReconciliationError(ValueError):
    """Raised for malformed evidence or an invalid reconciliation artifact."""


def _strict_fields(payload: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise LaneReconciliationError(
            f"{label} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _string(value: Any, *, label: str, safe: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneReconciliationError(f"{label} must be a non-blank string")
    if safe and (not _SAFE_ID.fullmatch(value) or ".." in value):
        raise LaneReconciliationError(f"{label} is invalid")
    return value


def _sha(value: Any, *, label: str) -> str:
    result = _string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneReconciliationError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _timestamp(value: Any, *, label: str) -> str:
    raw = _string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneReconciliationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LaneReconciliationError(f"{label} must include a timezone")
    return raw


def _finite(value: Any, *, label: str, nonnegative: bool = False, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise LaneReconciliationError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LaneReconciliationError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or (nonnegative and result < 0.0) or (positive and result <= 0.0):
        raise LaneReconciliationError(f"{label} is outside the allowed range")
    return result


def lane_reconciliation_content_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def evidence_content_hash(payload: Mapping[str, Any]) -> str:
    return lane_reconciliation_content_hash(payload)


def _validate_scope(payload: Mapping[str, Any], *, label: str) -> None:
    trade_date = _string(payload["trade_date"], label=f"{label}.trade_date")
    try:
        dt.date.fromisoformat(trade_date)
    except ValueError as exc:
        raise LaneReconciliationError(f"{label}.trade_date must be an ISO date") from exc
    _sha(payload["account_id_hash"], label=f"{label}.account_id_hash")
    for field in ("lane_id", "deployment_version", "plan_id"):
        _string(payload[field], label=f"{label}.{field}", safe=True)
    if payload["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneReconciliationError(f"{label}.lane_kind must be PAPER or LIVE")
    _sha(payload["plan_hash"], label=f"{label}.plan_hash")


def seal_broker_order_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = json.loads(canonical_json(dict(payload)))
    body["content_hash"] = evidence_content_hash(body)
    return validate_broker_order_evidence(body)


def validate_broker_order_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneReconciliationError("broker order evidence must be an object")
    _strict_fields(payload, _ORDER_EVIDENCE_FIELDS, label="broker order evidence")
    if payload["schema_version"] != BROKER_ORDER_EVIDENCE_SCHEMA:
        raise LaneReconciliationError("unsupported broker order evidence schema")
    _validate_scope(payload, label="broker order evidence")
    for field in ("observation_id", "order_id", "client_order_id", "broker_order_id"):
        _string(payload[field], label=field, safe=True)
    _timestamp(payload["observed_at"], label="observed_at")
    if payload["status"] not in ALL_ORDER_STATUSES:
        raise LaneReconciliationError("unsupported broker order status")
    submitted = _finite(payload["submitted_quantity"], label="submitted_quantity", positive=True)
    filled = _finite(payload["filled_quantity"], label="filled_quantity", nonnegative=True)
    if filled > submitted + _TOLERANCE:
        raise LaneReconciliationError("filled quantity exceeds submitted quantity")
    status = payload["status"]
    if status == "FILLED" and abs(filled - submitted) > _TOLERANCE:
        raise LaneReconciliationError("FILLED broker order must be completely filled")
    if status == "REJECTED" and filled > _TOLERANCE:
        raise LaneReconciliationError("REJECTED broker order cannot have a fill")
    if status == "PARTIALLY_FILLED" and not (
        filled > _TOLERANCE and filled < submitted - _TOLERANCE
    ):
        raise LaneReconciliationError(
            "PARTIALLY_FILLED broker order requires a strict partial quantity"
        )
    if status in {"SUBMITTED", "ACCEPTED", "PENDING"} and filled > _TOLERANCE:
        raise LaneReconciliationError(
            f"{status} broker order cannot declare filled quantity"
        )
    _sha(payload["source_hash"], label="source_hash")
    if _sha(payload["content_hash"], label="content_hash") != evidence_content_hash(payload):
        raise LaneReconciliationError("broker order evidence content_hash mismatch")
    return json.loads(canonical_json(payload))


def seal_broker_fill_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = json.loads(canonical_json(dict(payload)))
    body["content_hash"] = evidence_content_hash(body)
    return validate_broker_fill_evidence(body)


def validate_broker_fill_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneReconciliationError("broker fill evidence must be an object")
    _strict_fields(payload, _FILL_EVIDENCE_FIELDS, label="broker fill evidence")
    if payload["schema_version"] != BROKER_FILL_EVIDENCE_SCHEMA:
        raise LaneReconciliationError("unsupported broker fill evidence schema")
    _validate_scope(payload, label="broker fill evidence")
    for field in ("fill_id", "order_id", "client_order_id", "broker_order_id"):
        _string(payload[field], label=field, safe=True)
    _timestamp(payload["event_time"], label="event_time")
    symbol = _string(payload["symbol"], label="symbol")
    if not _SYMBOL.fullmatch(symbol) or payload["side"] not in {"BUY", "SELL"}:
        raise LaneReconciliationError("broker fill symbol or side is invalid")
    _finite(payload["quantity"], label="quantity", positive=True)
    _finite(payload["price"], label="price", positive=True)
    _finite(payload["fee_amount"], label="fee_amount", nonnegative=True)
    _sha(payload["source_hash"], label="source_hash")
    if _sha(payload["content_hash"], label="content_hash") != evidence_content_hash(payload):
        raise LaneReconciliationError("broker fill evidence content_hash mismatch")
    return json.loads(canonical_json(payload))


def seal_ending_lane_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = json.loads(canonical_json(dict(payload)))
    body["content_hash"] = evidence_content_hash(body)
    return validate_ending_lane_state(body)


def validate_ending_lane_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneReconciliationError("ending lane state must be an object")
    _strict_fields(payload, _ENDING_STATE_FIELDS, label="ending lane state")
    if payload["schema_version"] != ENDING_LANE_STATE_SCHEMA:
        raise LaneReconciliationError("unsupported ending lane state schema")
    _validate_scope(payload, label="ending lane state")
    _string(payload["state_id"], label="state_id", safe=True)
    _timestamp(payload["as_of"], label="as_of")
    cash = _finite(payload["cash"], label="cash")
    equity = _finite(payload["equity"], label="equity")
    positions = payload["positions"]
    if not isinstance(positions, list):
        raise LaneReconciliationError("ending positions must be an array")
    symbols: set[str] = set()
    position_value = 0.0
    for index, row in enumerate(positions):
        if not isinstance(row, Mapping):
            raise LaneReconciliationError("ending position rows must be objects")
        _strict_fields(row, _ENDING_POSITION_FIELDS, label=f"positions[{index}]")
        symbol = _string(row["symbol"], label="symbol")
        if not _SYMBOL.fullmatch(symbol) or symbol in symbols:
            raise LaneReconciliationError("ending position symbol is invalid or duplicated")
        symbols.add(symbol)
        quantity = _finite(row["quantity"], label="quantity", positive=True)
        mark = _finite(row["mark"], label="mark", positive=True)
        value = _finite(row["market_value"], label="market_value")
        if abs(value - quantity * mark) > 0.01:
            raise LaneReconciliationError("ending position market value mismatch")
        _sha(row["source_hash"], label="position source_hash")
        position_value += value
    if abs(equity - cash - position_value) > 0.01:
        raise LaneReconciliationError("ending state NAV identity mismatch")
    _sha(payload["source_hash"], label="source_hash")
    if _sha(payload["content_hash"], label="content_hash") != evidence_content_hash(payload):
        raise LaneReconciliationError("ending lane state content_hash mismatch")
    return json.loads(canonical_json(payload))


def _scope_matches(payload: Mapping[str, Any], plan: Mapping[str, Any]) -> bool:
    return all(
        payload[field] == plan[field]
        for field in ("trade_date", "account_id_hash", "lane_id", "lane_kind", "deployment_version", "plan_id")
    ) and payload["plan_hash"] == plan["content_hash"]


def _exact_order_hash(order: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(order).encode("utf-8")).hexdigest()


def _reconciled_fill_rows(
    *, plan: Mapping[str, Any], order: Mapping[str, Any], fill: Mapping[str, Any]
) -> list[dict[str, Any]]:
    contributions = order["sleeve_contributions"]
    total = sum(float(row["order_quantity"]) for row in contributions)
    rows: list[dict[str, Any]] = []
    assigned_qty = 0.0
    assigned_fee = 0.0
    exact_hash = _exact_order_hash(order)
    for index, contribution in enumerate(contributions):
        fraction = float(contribution["order_quantity"]) / total
        quantity = (
            float(fill["quantity"]) - assigned_qty
            if index == len(contributions) - 1
            else float(fill["quantity"]) * fraction
        )
        fee = (
            float(fill["fee_amount"]) - assigned_fee
            if index == len(contributions) - 1
            else float(fill["fee_amount"]) * fraction
        )
        assigned_qty += quantity
        assigned_fee += fee
        gross = quantity * float(fill["price"])
        net = -(gross + fee) if fill["side"] == "BUY" else gross - fee
        seed = hashlib.sha256(
            canonical_json(
                {"fill_hash": fill["content_hash"], "sleeve_id": contribution["sleeve_id"], "quantity": quantity}
            ).encode("utf-8")
        ).hexdigest()
        body = {
            "schema_version": RECONCILED_FILL_SCHEMA,
            "reconciled_fill_id": f"reconciled-fill:{fill['fill_id']}:{seed[:20]}",
            "trade_date": fill["trade_date"],
            "event_time": fill["event_time"],
            "account_id_hash": plan["account_id_hash"],
            "lane_id": plan["lane_id"],
            "lane_kind": plan["lane_kind"],
            "deployment_version": plan["deployment_version"],
            "session_id": plan["session_id"],
            "session_hash": plan["session_hash"],
            "allocation_id": plan["allocation_id"],
            "allocation_hash": plan["allocation_hash"],
            "target_hash": plan["target_hash"],
            "plan_id": plan["plan_id"],
            "plan_hash": plan["content_hash"],
            "order_id": order["order_id"],
            "client_order_id": order["client_order_id"],
            "broker_order_id": fill["broker_order_id"],
            "fill_id": fill["fill_id"],
            "symbol": fill["symbol"],
            "side": fill["side"],
            "quantity": quantity,
            "price": fill["price"],
            "gross_amount": gross,
            "fee_amount": fee,
            "net_amount": net,
            "sleeve_id": contribution["sleeve_id"],
            "decision_id": contribution["decision_id"],
            "decision_hash": contribution["decision_hash"],
            "order_fraction": contribution["order_fraction"],
            "planned_contribution_quantity": contribution["order_quantity"],
            "fill_allocation_fraction": fraction,
            "exact_order_hash": exact_hash,
            "broker_fill_source_hash": fill["content_hash"],
        }
        body["content_hash"] = lane_reconciliation_content_hash(body)
        rows.append(validate_reconciled_fill(body))
    return rows


def validate_reconciled_fill(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneReconciliationError("reconciled fill must be an object")
    _strict_fields(payload, _RECONCILED_FILL_FIELDS, label="reconciled fill")
    if payload["schema_version"] != RECONCILED_FILL_SCHEMA:
        raise LaneReconciliationError("unsupported reconciled fill schema")
    for field in (
        "reconciled_fill_id", "lane_id", "deployment_version", "session_id",
        "allocation_id", "plan_id", "order_id", "client_order_id", "broker_order_id",
        "fill_id", "sleeve_id", "decision_id",
    ):
        _string(payload[field], label=field, safe=True)
    for field in (
        "account_id_hash", "session_hash", "allocation_hash", "target_hash", "plan_hash",
        "decision_hash", "exact_order_hash", "broker_fill_source_hash",
    ):
        _sha(payload[field], label=field)
    _timestamp(payload["event_time"], label="event_time")
    if payload["lane_kind"] not in {"PAPER", "LIVE"} or payload["side"] not in {"BUY", "SELL"}:
        raise LaneReconciliationError("reconciled fill lane kind or side is invalid")
    symbol = _string(payload["symbol"], label="symbol")
    if not _SYMBOL.fullmatch(symbol):
        raise LaneReconciliationError("reconciled fill symbol is invalid")
    quantity = _finite(payload["quantity"], label="quantity", positive=True)
    price = _finite(payload["price"], label="price", positive=True)
    gross = _finite(payload["gross_amount"], label="gross_amount", positive=True)
    fee = _finite(payload["fee_amount"], label="fee_amount", nonnegative=True)
    if abs(gross - quantity * price) > _TOLERANCE:
        raise LaneReconciliationError("reconciled fill gross amount mismatch")
    expected_net = -(gross + fee) if payload["side"] == "BUY" else gross - fee
    if abs(_finite(payload["net_amount"], label="net_amount") - expected_net) > _TOLERANCE:
        raise LaneReconciliationError("reconciled fill net amount mismatch")
    for field in ("order_fraction", "fill_allocation_fraction"):
        value = _finite(payload[field], label=field, positive=True)
        if value > 1.0 + _TOLERANCE:
            raise LaneReconciliationError(f"{field} exceeds one")
    _finite(payload["planned_contribution_quantity"], label="planned_contribution_quantity", positive=True)
    if _sha(payload["content_hash"], label="content_hash") != lane_reconciliation_content_hash(payload):
        raise LaneReconciliationError("reconciled fill content_hash mismatch")
    return json.loads(canonical_json(payload))


def build_lane_reconciliation(
    *, exact_plan: Mapping[str, Any], wal_intents: Iterable[Mapping[str, Any]],
    broker_orders: Iterable[Mapping[str, Any]], broker_fills: Iterable[Mapping[str, Any]],
    ending_state: Mapping[str, Any], reconciled_at: str,
) -> dict[str, Any]:
    """Reconcile one plan through terminal broker and economic evidence."""

    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise LaneReconciliationError("exact plan is invalid: " + ",".join(failures))
    if exact_plan["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneReconciliationError("broker reconciliation supports PAPER and LIVE lanes")
    _timestamp(reconciled_at, label="reconciled_at")
    try:
        intents = [
            validate_lane_oms_intent(row, exact_plan=exact_plan)
            for row in wal_intents
        ]
    except LaneOmsError as exc:
        raise LaneReconciliationError(f"WAL intent is invalid: {exc}") from exc
    orders = [validate_broker_order_evidence(row) for row in broker_orders]
    fills = [validate_broker_fill_evidence(row) for row in broker_fills]
    state = validate_ending_lane_state(ending_state)
    if not _scope_matches(state, exact_plan):
        raise LaneReconciliationError("ending state scope differs from exact plan")

    planned = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    planned_by_id = {row["order_id"]: row for row in planned}
    reasons: set[str] = set()

    intent_by_order: dict[str, dict[str, Any]] = {}
    for intent in intents:
        order_id = intent["order_id"]
        if order_id in intent_by_order or order_id not in planned_by_id or not _scope_matches(intent, exact_plan):
            reasons.add("WAL_INTENT_COVERAGE_MISMATCH")
            continue
        plan_order = planned_by_id[order_id]
        if (
            intent["client_order_id"] != plan_order["client_order_id"]
            or intent["symbol"] != plan_order["symbol"]
            or intent["side"] != plan_order["side"]
            or abs(float(intent["quantity"]) - float(plan_order["quantity"])) > _TOLERANCE
        ):
            reasons.add("WAL_INTENT_PLAN_MISMATCH")
        intent_by_order[order_id] = intent
    if set(intent_by_order) != set(planned_by_id):
        reasons.add("WAL_INTENT_MISSING")

    order_by_id: dict[str, dict[str, Any]] = {}
    broker_ids: set[str] = set()
    for order in orders:
        order_id = order["order_id"]
        if (
            order_id in order_by_id
            or order["broker_order_id"] in broker_ids
            or order_id not in planned_by_id
            or not _scope_matches(order, exact_plan)
        ):
            reasons.add("BROKER_ORDER_COVERAGE_MISMATCH")
            continue
        broker_ids.add(order["broker_order_id"])
        plan_order = planned_by_id[order_id]
        if (
            order["client_order_id"] != plan_order["client_order_id"]
            or abs(float(order["submitted_quantity"]) - float(plan_order["quantity"])) > _TOLERANCE
        ):
            reasons.add("BROKER_ORDER_PLAN_MISMATCH")
        order_by_id[order_id] = order
    if set(order_by_id) != set(planned_by_id):
        reasons.add("BROKER_ORDER_MISSING")

    fills_by_order: dict[str, list[dict[str, Any]]] = {order_id: [] for order_id in planned_by_id}
    seen_fills: set[str] = set()
    lineage_valid_fills: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
    for fill in fills:
        if fill["fill_id"] in seen_fills:
            reasons.add("DUPLICATE_FILL_ID")
            continue
        seen_fills.add(fill["fill_id"])
        plan_order = planned_by_id.get(fill["order_id"])
        broker_order = order_by_id.get(fill["order_id"])
        if (
            plan_order is None
            or broker_order is None
            or not _scope_matches(fill, exact_plan)
            or fill["client_order_id"] != plan_order["client_order_id"]
            or fill["broker_order_id"] != broker_order["broker_order_id"]
            or fill["symbol"] != plan_order["symbol"]
            or fill["side"] != plan_order["side"]
        ):
            reasons.add("FILL_EXACT_LINEAGE_MISMATCH")
            continue
        fills_by_order[fill["order_id"]].append(fill)
        lineage_valid_fills.append((fill, plan_order))

    terminal_count = 0
    all_full = True
    all_rejected = bool(planned)
    for order_id, plan_order in planned_by_id.items():
        broker_order = order_by_id.get(order_id)
        if broker_order is None:
            all_full = False
            all_rejected = False
            continue
        fill_qty = sum(float(row["quantity"]) for row in fills_by_order[order_id])
        if fill_qty > float(plan_order["quantity"]) + _TOLERANCE:
            reasons.add("ORDER_OVERFILLED")
        if abs(fill_qty - float(broker_order["filled_quantity"])) > _TOLERANCE:
            reasons.add("BROKER_ORDER_FILL_TOTAL_MISMATCH")
        terminal = broker_order["status"] in TERMINAL_ORDER_STATUSES
        terminal_count += int(terminal)
        if not terminal:
            reasons.add("NONTERMINAL_BROKER_ORDER")
        full = (
            broker_order["status"] == "FILLED"
            and abs(fill_qty - float(plan_order["quantity"])) <= _TOLERANCE
        )
        all_full = all_full and full
        all_rejected = all_rejected and broker_order["status"] == "REJECTED" and fill_qty <= _TOLERANCE

    expected_positions = {
        row["symbol"]: float(row["quantity"]) for row in exact_plan["starting_positions"]
    }
    expected_cash = float(exact_plan["starting_cash"])
    total_fees = 0.0
    for fill, _ in lineage_valid_fills:
        quantity = float(fill["quantity"])
        gross = quantity * float(fill["price"])
        fee = float(fill["fee_amount"])
        total_fees += fee
        direction = 1.0 if fill["side"] == "BUY" else -1.0
        expected_positions[fill["symbol"]] = expected_positions.get(fill["symbol"], 0.0) + direction * quantity
        expected_cash += -gross - fee if fill["side"] == "BUY" else gross - fee
    expected_positions = {
        symbol: quantity for symbol, quantity in expected_positions.items() if abs(quantity) > _TOLERANCE
    }
    actual_positions = {row["symbol"]: float(row["quantity"]) for row in state["positions"]}
    symbols = sorted(set(expected_positions) | set(actual_positions))
    deltas = {
        symbol: actual_positions.get(symbol, 0.0) - expected_positions.get(symbol, 0.0)
        for symbol in symbols
        if abs(actual_positions.get(symbol, 0.0) - expected_positions.get(symbol, 0.0)) > _TOLERANCE
    }
    cash_delta = float(state["cash"]) - expected_cash
    if deltas:
        reasons.add("POSITIONS_FROM_FILLS_MISMATCH")
    if abs(cash_delta) > 0.01:
        reasons.add("CASH_FROM_FILLS_MISMATCH")
    marked_value = sum(float(row["market_value"]) for row in state["positions"])
    calculated_equity = float(state["cash"]) + marked_value
    nav_delta = float(state["equity"]) - calculated_equity
    if abs(nav_delta) > 0.01:
        reasons.add("ENDING_NAV_MISMATCH")
    if all_full:
        target_positions = {
            row["symbol"]: float(row["quantity"]) for row in exact_plan["expected_posttrade_positions"]
        }
        target_delta = any(
            abs(target_positions.get(symbol, 0.0) - actual_positions.get(symbol, 0.0))
            > _TOLERANCE
            for symbol in set(target_positions) | set(actual_positions)
        )
        # Exact quantities must attain the target.  Ending cash is reconciled
        # from actual fill prices and fees above; it must not be forced back to
        # the plan's pre-trade price estimate.
        if target_delta:
            reasons.add("FULL_FILL_TARGET_STATE_MISMATCH")

    unresolved = bool(reasons)
    if unresolved:
        status = "UNRESOLVED"
    elif all_full:
        status = "PASS"
    elif all_rejected and not fills:
        status = "REJECTED"
    else:
        status = "PARTIAL"
    if not reasons:
        reasons.add(
            {
                "PASS": "PLAN_ORDERS_FILLS_POSITIONS_CASH_RECONCILED",
                "PARTIAL": "TERMINAL_PARTIAL_EXECUTION_RECONCILED",
                "REJECTED": "TERMINAL_REJECTION_RECONCILED",
            }[status]
        )

    reconciled_fills: list[dict[str, Any]] = []
    if not unresolved:
        for fill, plan_order in sorted(
            lineage_valid_fills, key=lambda item: (item[0]["event_time"], item[0]["fill_id"])
        ):
            reconciled_fills.extend(
                _reconciled_fill_rows(plan=exact_plan, order=plan_order, fill=fill)
            )
    accounting_ready = status in {"PASS", "PARTIAL"} and bool(reconciled_fills)
    valid_fill_rows = [fill for fill, _ in lineage_valid_fills]
    source_hashes = {
        "plan": exact_plan["content_hash"],
        "wal_intents": [row["content_hash"] for row in sorted(intents, key=lambda row: row["intent_id"])],
        "broker_orders": [row["content_hash"] for row in sorted(orders, key=lambda row: row["observation_id"])],
        "broker_fills": [row["content_hash"] for row in sorted(fills, key=lambda row: row["fill_id"])],
        "ending_state": state["content_hash"],
    }
    seed = hashlib.sha256(canonical_json(source_hashes).encode("utf-8")).hexdigest()
    body = {
        "schema_version": LANE_RECONCILIATION_SCHEMA,
        "reconciliation_id": f"lane-reconciliation:{exact_plan['lane_id']}:{seed[:24]}",
        "reconciled_at": reconciled_at,
        "status": status,
        "reason_codes": sorted(reasons),
        "execution_authority": False,
        "broker_call_performed": False,
        "halt_required": status != "PASS",
        "escalation_required": status != "PASS",
        "accounting_ready": accounting_ready,
        "trade_date": exact_plan["trade_date"],
        "account_id_hash": exact_plan["account_id_hash"],
        "lane_id": exact_plan["lane_id"],
        "lane_kind": exact_plan["lane_kind"],
        "deployment_version": exact_plan["deployment_version"],
        "session_id": exact_plan["session_id"],
        "session_hash": exact_plan["session_hash"],
        "allocation_id": exact_plan["allocation_id"],
        "allocation_hash": exact_plan["allocation_hash"],
        "target_hash": exact_plan["target_hash"],
        "plan_id": exact_plan["plan_id"],
        "plan_hash": exact_plan["content_hash"],
        "reconciliation_policy_hash": exact_plan["reconciliation_policy_hash"],
        "intended_order_count": len(planned),
        "wal_intent_count": len(intents),
        "broker_order_count": len(orders),
        "broker_fill_count": len(fills),
        "terminal_order_count": terminal_count,
        "intended_orders": [
            {
                "order_id": row["order_id"], "client_order_id": row["client_order_id"],
                "symbol": row["symbol"], "side": row["side"], "quantity": row["quantity"],
                "exact_order_hash": _exact_order_hash(row),
            }
            for row in planned
        ],
        "wal_intents": [
            {"intent_id": row["intent_id"], "order_id": row["order_id"], "intent_hash": row["content_hash"]}
            for row in sorted(intents, key=lambda row: row["intent_id"])
        ],
        "broker_orders": [
            {
                "order_id": row["order_id"], "broker_order_id": row["broker_order_id"],
                "status": row["status"], "submitted_quantity": row["submitted_quantity"],
                "filled_quantity": row["filled_quantity"], "evidence_hash": row["content_hash"],
            }
            for row in sorted(orders, key=lambda row: row["order_id"])
        ],
        "broker_fills": [
            {
                "fill_id": row["fill_id"], "order_id": row["order_id"],
                "broker_order_id": row["broker_order_id"], "quantity": row["quantity"],
                "price": row["price"], "fee_amount": row["fee_amount"],
                "evidence_hash": row["content_hash"],
            }
            for row in sorted(fills, key=lambda row: row["fill_id"])
        ],
        "position_reconciliation": {
            "status": "PASS" if not deltas else "FAIL",
            "starting": {row["symbol"]: row["quantity"] for row in exact_plan["starting_positions"]},
            "expected_from_fills": dict(sorted(expected_positions.items())),
            "actual": dict(sorted(actual_positions.items())),
            "deltas": dict(sorted(deltas.items())),
        },
        "cash_reconciliation": {
            "status": "PASS" if abs(cash_delta) <= 0.01 else "FAIL",
            "starting_cash": exact_plan["starting_cash"],
            "buy_notional": sum(
                float(row["quantity"]) * float(row["price"])
                for row in valid_fill_rows
                if row["side"] == "BUY"
            ),
            "sell_notional": sum(
                float(row["quantity"]) * float(row["price"])
                for row in valid_fill_rows
                if row["side"] == "SELL"
            ),
            "fees": total_fees,
            "expected_from_fills": expected_cash,
            "actual": state["cash"],
            "delta": cash_delta,
        },
        "nav_reconciliation": {
            "status": "PASS" if abs(nav_delta) <= 0.01 else "FAIL",
            "cash": state["cash"], "positions_market_value": marked_value,
            "calculated_equity": calculated_equity, "broker_equity": state["equity"],
            "delta": nav_delta, "as_of": state["as_of"],
        },
        "reconciled_fills": reconciled_fills,
        "source_hashes": source_hashes,
    }
    body["content_hash"] = lane_reconciliation_content_hash(body)
    return validate_lane_reconciliation(body, exact_plan=exact_plan)


def _validate_position_map(value: Any, *, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise LaneReconciliationError(f"{label} must be an object")
    result: dict[str, float] = {}
    for raw_symbol, raw_quantity in value.items():
        symbol = _string(raw_symbol, label=f"{label}.symbol")
        if not _SYMBOL.fullmatch(symbol):
            raise LaneReconciliationError(f"{label} contains an invalid symbol")
        result[symbol] = _finite(raw_quantity, label=f"{label}.{symbol}")
    if list(value) != sorted(value):
        raise LaneReconciliationError(f"{label} keys must be sorted")
    return result


def _validate_summary_rows(payload: Mapping[str, Any]) -> dict[str, set[str]]:
    identities: dict[str, set[str]] = {}
    intended_ids: set[str] = set()
    intended_clients: set[str] = set()
    for index, row in enumerate(payload["intended_orders"]):
        label = f"intended_orders[{index}]"
        if not isinstance(row, Mapping):
            raise LaneReconciliationError(f"{label} must be an object")
        _strict_fields(row, _INTENDED_SUMMARY_FIELDS, label=label)
        order_id = _string(row["order_id"], label=f"{label}.order_id", safe=True)
        client_id = _string(
            row["client_order_id"], label=f"{label}.client_order_id", safe=True
        )
        symbol = _string(row["symbol"], label=f"{label}.symbol")
        if (
            order_id in intended_ids
            or client_id in intended_clients
            or not _SYMBOL.fullmatch(symbol)
            or row["side"] not in {"BUY", "SELL"}
        ):
            raise LaneReconciliationError("intended order identity is invalid or duplicated")
        intended_ids.add(order_id)
        intended_clients.add(client_id)
        _finite(row["quantity"], label=f"{label}.quantity", positive=True)
        _sha(row["exact_order_hash"], label=f"{label}.exact_order_hash")
    identities["intended"] = intended_ids

    wal_ids: set[str] = set()
    wal_orders: set[str] = set()
    for index, row in enumerate(payload["wal_intents"]):
        label = f"wal_intents[{index}]"
        if not isinstance(row, Mapping):
            raise LaneReconciliationError(f"{label} must be an object")
        _strict_fields(row, _WAL_SUMMARY_FIELDS, label=label)
        intent_id = _string(row["intent_id"], label=f"{label}.intent_id", safe=True)
        order_id = _string(row["order_id"], label=f"{label}.order_id", safe=True)
        if intent_id in wal_ids or order_id in wal_orders or order_id not in intended_ids:
            raise LaneReconciliationError("WAL summary identity is invalid or duplicated")
        wal_ids.add(intent_id)
        wal_orders.add(order_id)
        _sha(row["intent_hash"], label=f"{label}.intent_hash")
    identities["wal_orders"] = wal_orders

    broker_order_ids: set[str] = set()
    broker_orders: set[str] = set()
    for index, row in enumerate(payload["broker_orders"]):
        label = f"broker_orders[{index}]"
        if not isinstance(row, Mapping):
            raise LaneReconciliationError(f"{label} must be an object")
        _strict_fields(row, _ORDER_SUMMARY_FIELDS, label=label)
        order_id = _string(row["order_id"], label=f"{label}.order_id", safe=True)
        broker_id = _string(
            row["broker_order_id"], label=f"{label}.broker_order_id", safe=True
        )
        if (
            order_id in broker_orders
            or broker_id in broker_order_ids
            or order_id not in intended_ids
            or row["status"] not in ALL_ORDER_STATUSES
        ):
            raise LaneReconciliationError("broker order summary identity/status is invalid")
        broker_orders.add(order_id)
        broker_order_ids.add(broker_id)
        submitted = _finite(
            row["submitted_quantity"], label=f"{label}.submitted_quantity", positive=True
        )
        filled = _finite(
            row["filled_quantity"], label=f"{label}.filled_quantity", nonnegative=True
        )
        if filled > submitted + _TOLERANCE:
            raise LaneReconciliationError("broker order summary is overfilled")
        _sha(row["evidence_hash"], label=f"{label}.evidence_hash")
    identities["broker_orders"] = broker_orders
    identities["broker_ids"] = broker_order_ids

    fill_ids: set[str] = set()
    for index, row in enumerate(payload["broker_fills"]):
        label = f"broker_fills[{index}]"
        if not isinstance(row, Mapping):
            raise LaneReconciliationError(f"{label} must be an object")
        _strict_fields(row, _FILL_SUMMARY_FIELDS, label=label)
        fill_id = _string(row["fill_id"], label=f"{label}.fill_id", safe=True)
        order_id = _string(row["order_id"], label=f"{label}.order_id", safe=True)
        broker_id = _string(
            row["broker_order_id"], label=f"{label}.broker_order_id", safe=True
        )
        if (
            fill_id in fill_ids
            or order_id not in broker_orders
            or broker_id not in broker_order_ids
        ):
            raise LaneReconciliationError("broker fill summary identity is invalid")
        fill_ids.add(fill_id)
        _finite(row["quantity"], label=f"{label}.quantity", positive=True)
        _finite(row["price"], label=f"{label}.price", positive=True)
        _finite(row["fee_amount"], label=f"{label}.fee_amount", nonnegative=True)
        _sha(row["evidence_hash"], label=f"{label}.evidence_hash")
    identities["fills"] = fill_ids
    return identities


def _validate_economic_proofs(payload: Mapping[str, Any]) -> None:
    position = payload["position_reconciliation"]
    if not isinstance(position, Mapping):
        raise LaneReconciliationError("position_reconciliation must be an object")
    _strict_fields(position, _POSITION_PROOF_FIELDS, label="position_reconciliation")
    starting = _validate_position_map(position["starting"], label="positions.starting")
    expected = _validate_position_map(
        position["expected_from_fills"], label="positions.expected_from_fills"
    )
    actual = _validate_position_map(position["actual"], label="positions.actual")
    deltas = _validate_position_map(position["deltas"], label="positions.deltas")
    observed_deltas = {
        symbol: actual.get(symbol, 0.0) - expected.get(symbol, 0.0)
        for symbol in sorted(set(expected) | set(actual))
        if abs(actual.get(symbol, 0.0) - expected.get(symbol, 0.0)) > _TOLERANCE
    }
    if deltas != observed_deltas or position["status"] != ("PASS" if not deltas else "FAIL"):
        raise LaneReconciliationError("position reconciliation proof mismatch")
    del starting

    cash = payload["cash_reconciliation"]
    if not isinstance(cash, Mapping):
        raise LaneReconciliationError("cash_reconciliation must be an object")
    _strict_fields(cash, _CASH_PROOF_FIELDS, label="cash_reconciliation")
    for field in (
        "starting_cash", "buy_notional", "sell_notional", "fees",
        "expected_from_fills", "actual", "delta",
    ):
        _finite(cash[field], label=f"cash_reconciliation.{field}")
    expected_cash = (
        float(cash["starting_cash"])
        - float(cash["buy_notional"])
        + float(cash["sell_notional"])
        - float(cash["fees"])
    )
    delta = float(cash["actual"]) - expected_cash
    if (
        abs(float(cash["expected_from_fills"]) - expected_cash) > _TOLERANCE
        or abs(float(cash["delta"]) - delta) > _TOLERANCE
        or cash["status"] != ("PASS" if abs(delta) <= 0.01 else "FAIL")
    ):
        raise LaneReconciliationError("cash reconciliation proof mismatch")

    nav = payload["nav_reconciliation"]
    if not isinstance(nav, Mapping):
        raise LaneReconciliationError("nav_reconciliation must be an object")
    _strict_fields(nav, _NAV_PROOF_FIELDS, label="nav_reconciliation")
    for field in (
        "cash", "positions_market_value", "calculated_equity", "broker_equity", "delta"
    ):
        _finite(nav[field], label=f"nav_reconciliation.{field}")
    _timestamp(nav["as_of"], label="nav_reconciliation.as_of")
    calculated = float(nav["cash"]) + float(nav["positions_market_value"])
    nav_delta = float(nav["broker_equity"]) - calculated
    if (
        abs(float(nav["calculated_equity"]) - calculated) > _TOLERANCE
        or abs(float(nav["delta"]) - nav_delta) > _TOLERANCE
        or nav["status"] != ("PASS" if abs(nav_delta) <= 0.01 else "FAIL")
    ):
        raise LaneReconciliationError("NAV reconciliation proof mismatch")


def validate_lane_reconciliation(
    payload: Mapping[str, Any], *, exact_plan: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneReconciliationError("lane reconciliation must be an object")
    _strict_fields(payload, _RECONCILIATION_FIELDS, label="lane reconciliation")
    if payload["schema_version"] != LANE_RECONCILIATION_SCHEMA:
        raise LaneReconciliationError("unsupported lane reconciliation schema")
    _string(payload["reconciliation_id"], label="reconciliation_id", safe=True)
    _timestamp(payload["reconciled_at"], label="reconciled_at")
    status = payload["status"]
    if status not in RECONCILIATION_STATUSES:
        raise LaneReconciliationError("unsupported reconciliation status")
    if payload["execution_authority"] is not False or payload["broker_call_performed"] is not False:
        raise LaneReconciliationError("reconciliation cannot execute or call a broker")
    expected_stop = status != "PASS"
    if payload["halt_required"] is not expected_stop or payload["escalation_required"] is not expected_stop:
        raise LaneReconciliationError("halt/escalation semantics do not match status")
    if payload["accounting_ready"] is True and status not in {"PASS", "PARTIAL"}:
        raise LaneReconciliationError("only PASS/PARTIAL can be accounting ready")
    if not isinstance(payload["reason_codes"], list) or not payload["reason_codes"]:
        raise LaneReconciliationError("reconciliation reason_codes are required")
    _sha(payload["account_id_hash"], label="account_id_hash")
    for field in ("lane_id", "deployment_version", "session_id", "allocation_id", "plan_id"):
        _string(payload[field], label=field, safe=True)
    if payload["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneReconciliationError("reconciliation lane kind is invalid")
    for field in (
        "session_hash", "allocation_hash", "target_hash", "plan_hash",
        "reconciliation_policy_hash",
    ):
        _sha(payload[field], label=field)
    for field in (
        "intended_order_count", "wal_intent_count", "broker_order_count",
        "broker_fill_count", "terminal_order_count",
    ):
        if isinstance(payload[field], bool) or not isinstance(payload[field], int) or payload[field] < 0:
            raise LaneReconciliationError(f"{field} must be a nonnegative integer")
    count_bindings = {
        "intended_order_count": "intended_orders",
        "wal_intent_count": "wal_intents",
        "broker_order_count": "broker_orders",
        "broker_fill_count": "broker_fills",
    }
    for count_field, rows_field in count_bindings.items():
        if not isinstance(payload[rows_field], list) or payload[count_field] != len(payload[rows_field]):
            raise LaneReconciliationError(f"{count_field} does not match {rows_field}")
    identities = _validate_summary_rows(payload)
    if payload["terminal_order_count"] != sum(
        row["status"] in TERMINAL_ORDER_STATUSES for row in payload["broker_orders"]
    ):
        raise LaneReconciliationError("terminal_order_count mismatch")
    _validate_economic_proofs(payload)
    if not isinstance(payload["reconciled_fills"], list):
        raise LaneReconciliationError("reconciled_fills must be an array")
    reconciled = [validate_reconciled_fill(row) for row in payload["reconciled_fills"]]
    if len({row["reconciled_fill_id"] for row in reconciled}) != len(reconciled):
        raise LaneReconciliationError("reconciled fill identities must be unique")
    for row in reconciled:
        for field in (
            "trade_date", "account_id_hash", "lane_id", "lane_kind", "deployment_version",
            "session_id", "session_hash", "allocation_id", "allocation_hash", "target_hash",
            "plan_id", "plan_hash",
        ):
            if row[field] != payload[field]:
                raise LaneReconciliationError("reconciled fill lineage differs from reconciliation")
        if row["order_id"] not in identities["broker_orders"] or row["fill_id"] not in identities["fills"]:
            raise LaneReconciliationError("reconciled fill lacks broker summary lineage")
    expected_accounting_ready = status in {"PASS", "PARTIAL"} and bool(reconciled)
    if payload["accounting_ready"] is not expected_accounting_ready:
        raise LaneReconciliationError("accounting_ready does not match reconciled fill status")
    sources = payload["source_hashes"]
    if not isinstance(sources, Mapping) or set(sources) != {
        "plan", "wal_intents", "broker_orders", "broker_fills", "ending_state"
    }:
        raise LaneReconciliationError("reconciliation source hashes are incomplete")
    _sha(sources["plan"], label="source plan")
    _sha(sources["ending_state"], label="source ending_state")
    for field in ("wal_intents", "broker_orders", "broker_fills"):
        if not isinstance(sources[field], list):
            raise LaneReconciliationError(f"source {field} must be an array")
        for value in sources[field]:
            _sha(value, label=f"source {field}")
    expected_source_lists = {
        "wal_intents": [row["intent_hash"] for row in payload["wal_intents"]],
        "broker_orders": [row["evidence_hash"] for row in payload["broker_orders"]],
        "broker_fills": [row["evidence_hash"] for row in payload["broker_fills"]],
    }
    for field, expected_values in expected_source_lists.items():
        if sources[field] != expected_values:
            raise LaneReconciliationError(f"source {field} lineage mismatch")
    if sources["plan"] != payload["plan_hash"]:
        raise LaneReconciliationError("reconciliation plan source hash mismatch")
    if _sha(payload["content_hash"], label="content_hash") != lane_reconciliation_content_hash(payload):
        raise LaneReconciliationError("lane reconciliation content_hash mismatch")
    if exact_plan is not None:
        failures = validate_lane_exact_execution_plan(exact_plan)
        if failures:
            raise LaneReconciliationError("exact plan is invalid: " + ",".join(failures))
        plan_bindings = (
            "trade_date", "account_id_hash", "lane_id", "lane_kind",
            "deployment_version", "session_id", "session_hash", "allocation_id",
            "allocation_hash", "target_hash", "plan_id",
        )
        if any(payload[field] != exact_plan[field] for field in plan_bindings) or payload[
            "plan_hash"
        ] != exact_plan["content_hash"]:
            raise LaneReconciliationError("reconciliation scope differs from exact plan")
        if payload["reconciliation_policy_hash"] != exact_plan["reconciliation_policy_hash"]:
            raise LaneReconciliationError(
                "reconciliation policy hash differs from exact plan"
            )
        plan_orders = {
            row["order_id"]: row
            for row in [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
        }
        if set(plan_orders) != {row["order_id"] for row in payload["intended_orders"]}:
            raise LaneReconciliationError("intended order coverage differs from exact plan")
        for summary in payload["intended_orders"]:
            order = plan_orders[summary["order_id"]]
            expected = {
                "order_id": order["order_id"],
                "client_order_id": order["client_order_id"],
                "symbol": order["symbol"],
                "side": order["side"],
                "quantity": order["quantity"],
                "exact_order_hash": _exact_order_hash(order),
            }
            if summary != expected:
                raise LaneReconciliationError(
                    "intended order summary differs from exact plan"
                )

        broker_fill_by_id = {row["fill_id"]: row for row in payload["broker_fills"]}
        splits_by_fill: dict[str, list[Mapping[str, Any]]] = {}
        for row in reconciled:
            broker_fill = broker_fill_by_id.get(row["fill_id"])
            order = plan_orders.get(row["order_id"])
            if broker_fill is None or order is None:
                raise LaneReconciliationError(
                    "reconciled fill is absent from plan or broker evidence"
                )
            contribution = next(
                (
                    item
                    for item in order["sleeve_contributions"]
                    if item["sleeve_id"] == row["sleeve_id"]
                ),
                None,
            )
            if contribution is None:
                raise LaneReconciliationError(
                    "reconciled fill sleeve is absent from exact order"
                )
            if (
                row["decision_id"] != contribution["decision_id"]
                or row["decision_hash"] != contribution["decision_hash"]
                or abs(float(row["order_fraction"]) - float(contribution["order_fraction"]))
                > _TOLERANCE
                or abs(
                    float(row["planned_contribution_quantity"])
                    - float(contribution["order_quantity"])
                )
                > _TOLERANCE
                or row["exact_order_hash"] != _exact_order_hash(order)
                or row["broker_fill_source_hash"] != broker_fill["evidence_hash"]
                or abs(float(row["price"]) - float(broker_fill["price"])) > _TOLERANCE
                or row["symbol"] != order["symbol"]
                or row["side"] != order["side"]
            ):
                raise LaneReconciliationError(
                    "reconciled fill contribution differs from exact lineage"
                )
            expected_fraction = float(contribution["order_quantity"]) / float(
                order["quantity"]
            )
            if abs(float(row["fill_allocation_fraction"]) - expected_fraction) > _TOLERANCE:
                raise LaneReconciliationError(
                    "reconciled fill allocation fraction differs from exact order"
                )
            splits_by_fill.setdefault(row["fill_id"], []).append(row)
        if status in {"PASS", "PARTIAL"} and set(splits_by_fill) != set(
            broker_fill_by_id
        ):
            raise LaneReconciliationError(
                "accounting-ready reconciliation lacks complete broker fill splits"
            )
        for fill_id, split_rows in splits_by_fill.items():
            broker_fill = broker_fill_by_id[fill_id]
            order = plan_orders[broker_fill["order_id"]]
            expected_sleeves = {
                row["sleeve_id"] for row in order["sleeve_contributions"]
            }
            observed_sleeves = [row["sleeve_id"] for row in split_rows]
            if len(observed_sleeves) != len(set(observed_sleeves)) or set(
                observed_sleeves
            ) != expected_sleeves:
                raise LaneReconciliationError(
                    "reconciled fill sleeve coverage differs from exact order"
                )
            if (
                abs(
                    sum(float(row["quantity"]) for row in split_rows)
                    - float(broker_fill["quantity"])
                )
                > _TOLERANCE
                or abs(
                    sum(float(row["fee_amount"]) for row in split_rows)
                    - float(broker_fill["fee_amount"])
                )
                > _TOLERANCE
            ):
                raise LaneReconciliationError(
                    "reconciled fill sleeve splits do not sum to broker fill"
                )
    return json.loads(canonical_json(payload))


__all__ = [
    "BROKER_FILL_EVIDENCE_SCHEMA", "BROKER_ORDER_EVIDENCE_SCHEMA",
    "ENDING_LANE_STATE_SCHEMA", "LANE_RECONCILIATION_SCHEMA",
    "RECONCILED_FILL_SCHEMA", "LaneReconciliationError",
    "build_lane_reconciliation", "evidence_content_hash",
    "lane_reconciliation_content_hash", "seal_broker_fill_evidence",
    "seal_broker_order_evidence", "seal_ending_lane_state",
    "validate_broker_fill_evidence", "validate_broker_order_evidence",
    "validate_ending_lane_state", "validate_lane_reconciliation",
    "validate_reconciled_fill",
]
