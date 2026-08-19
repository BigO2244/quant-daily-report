"""Advisory exact execution-plan v4 for a governed Caerus lane.

The contract consumes three explicit, immutable inputs: an independent Risk
package, a broker account snapshot, and the lane policy that governed the
decision.  It never reads the active registry or runtime configuration and it
never authorizes submission.  Both PAPER and LIVE-shaped lanes use the same
schema; downstream authorization and broker guards remain separate concerns.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from core.lane_allocator import content_hash as _json_hash
from core.lane_risk_authority import (
    LANE_RISK_PACKAGE_SCHEMA,
    validate_lane_risk_package,
)


LANE_EXACT_PLAN_SCHEMA = "caerus.execution_plan.v4"
BROKER_SNAPSHOT_SCHEMA = "caerus.broker_account_snapshot.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SAFE_LANE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SAFE_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_ORDER_TYPES = frozenset({"market", "limit"})
_TIME_IN_FORCE = frozenset({"day", "gtc", "opg", "cls", "ioc", "fok"})
_LANE_KINDS = frozenset({"SHADOW", "PAPER", "LIVE"})
_BROKER_OWNED_FIELDS = frozenset(
    {
        "broker_order_id",
        "status",
        "filled_quantity",
        "filled_qty",
        "filled_avg_price",
        "submitted_at",
        "filled_at",
        "canceled_at",
        "rejected_at",
    }
)
_TOLERANCE = 1e-8
_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "plan_id",
        "trade_date",
        "planned_at",
        "expires_at",
        "status",
        "execution_authority",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "account_id_hash",
        "broker_environment",
        "session_id",
        "session_hash",
        "allocation_id",
        "allocation_hash",
        "target_package_id",
        "target_package_hash",
        "target_hash",
        "approved_target_hash",
        "risk_package_id",
        "risk_package_hash",
        "risk_decision",
        "broker_snapshot_id",
        "broker_snapshot_hash",
        "broker_snapshot_captured_at",
        "lane_policy_hash",
        "allocator_policy_hash",
        "risk_policy_hash",
        "capital_policy_hash",
        "execution_policy_hash",
        "reconciliation_policy_hash",
        "capital_basis",
        "deployable_capital",
        "starting_equity",
        "starting_cash",
        "starting_positions",
        "starting_state_hash",
        "price_marks",
        "approved_cash_weight",
        "approved_target_rows",
        "sell_orders",
        "buy_orders",
        "expected_posttrade_positions",
        "expected_posttrade_cash",
        "constraints",
        "source_hashes",
        "content_hash",
    }
)


class LaneExactPlanError(ValueError):
    """Raised when an advisory exact plan cannot be proven from its inputs."""


def _reject_json_constant(value: str) -> None:
    raise LaneExactPlanError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LaneExactPlanError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LaneExactPlanError(f"exact plan is not canonical JSON: {exc}") from exc


def artifact_content_hash(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise LaneExactPlanError("hashed artifact must be an object")
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def lane_exact_plan_content_hash(payload: Mapping[str, Any]) -> str:
    return artifact_content_hash(payload)


def _required_string(value: Any, *, label: str, safe_id: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneExactPlanError(
            f"{label} must be a non-blank string without surrounding whitespace"
        )
    if safe_id and (not _SAFE_ID.fullmatch(value) or ".." in value):
        raise LaneExactPlanError(f"{label} is invalid")
    return value


def _sha256(value: Any, *, label: str) -> str:
    result = _required_string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneExactPlanError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _finite(value: Any, *, label: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise LaneExactPlanError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LaneExactPlanError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < minimum:
        raise LaneExactPlanError(f"{label} is outside the allowed range")
    return result


def _integer(value: Any, *, label: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise LaneExactPlanError(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise LaneExactPlanError(f"{label} must be an integer") from exc
    if result != value or result < minimum or (maximum is not None and result > maximum):
        raise LaneExactPlanError(f"{label} is outside the allowed range")
    return result


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    raw = _required_string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneExactPlanError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LaneExactPlanError(f"{label} must include a timezone")
    return raw, parsed


def _trade_date(value: Any) -> str:
    raw = _required_string(value, label="trade_date")
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise LaneExactPlanError("trade_date must be an ISO date") from exc
    return raw


def _validate_risk(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != LANE_RISK_PACKAGE_SCHEMA:
        raise LaneExactPlanError("unsupported or missing lane Risk package")
    failures = validate_lane_risk_package(payload)
    if failures:
        raise LaneExactPlanError("lane Risk package is invalid: " + ",".join(failures))
    if payload.get("decision") not in {"APPROVE", "CONSTRAIN"}:
        raise LaneExactPlanError("Risk REJECT does not permit an exact plan")
    if payload.get("execution_authority") is not False:
        raise LaneExactPlanError("Risk input must not carry execution authority")
    return copy.deepcopy(dict(payload))


def _normalize_policy(
    lane_policy: Mapping[str, Any], *, risk: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(lane_policy, Mapping):
        raise LaneExactPlanError("governed lane policy must be an object")
    policy = copy.deepcopy(dict(lane_policy))
    declared = str(policy.pop("content_hash", ""))
    observed = _json_hash(policy)
    if declared and declared != observed:
        raise LaneExactPlanError("governed lane policy content hash mismatch")
    if observed != risk["lane_policy_hash"]:
        raise LaneExactPlanError("Risk and governed lane policy hashes differ")
    for field in ("lane_id", "lane_kind", "deployment_version", "account_id_hash"):
        if policy.get(field) != risk[field]:
            raise LaneExactPlanError(f"Risk and governed lane policy differ: {field}")
    broker_environment = _required_string(
        policy.get("broker_environment"), label="broker_environment", safe_id=True
    )
    allocator = policy.get("allocator_policy")
    if not isinstance(allocator, Mapping):
        raise LaneExactPlanError("allocator_policy is required")
    normalized_allocator = {
        "allocator_id": allocator.get("allocator_id"),
        "allocator_version": allocator.get("allocator_version"),
        "method": allocator.get("method"),
        "unavailable_policy": "fail_closed",
        "target_cash_weight": allocator.get("target_cash_weight"),
    }
    bindings = {
        "allocator_policy_hash": _json_hash(normalized_allocator),
        "risk_policy_hash": _json_hash(policy.get("risk_policy") or {}),
        "capital_policy_hash": _json_hash(policy.get("capital_policy") or {}),
        "execution_policy_hash": _json_hash(policy.get("execution_policy") or {}),
        "reconciliation_policy_hash": _json_hash(
            policy.get("reconciliation_policy") or {}
        ),
    }
    for field, observed_hash in bindings.items():
        if observed_hash != risk[field]:
            raise LaneExactPlanError(f"Risk and governed policy differ: {field}")

    execution = policy.get("execution_policy")
    capital = policy.get("capital_policy")
    if not isinstance(execution, Mapping) or not isinstance(capital, Mapping):
        raise LaneExactPlanError("capital_policy and execution_policy are required")
    order_type = str(execution.get("order_type") or "").strip().lower()
    time_in_force = str(execution.get("time_in_force") or "").strip().lower()
    if order_type not in _ORDER_TYPES or time_in_force not in _TIME_IN_FORCE:
        raise LaneExactPlanError("execution policy order type or time in force is invalid")
    allow_extended_hours = execution.get("allow_extended_hours")
    allow_fractional = execution.get("allow_fractional_shares")
    if type(allow_extended_hours) is not bool or type(allow_fractional) is not bool:
        raise LaneExactPlanError("execution policy boolean fields are invalid")
    if allow_extended_hours and (order_type != "limit" or time_in_force != "day"):
        raise LaneExactPlanError("extended-hours policy requires DAY limit orders")
    quantity_precision = _integer(
        execution.get("quantity_precision"),
        label="execution_policy.quantity_precision",
        maximum=6,
    )
    if not allow_fractional and quantity_precision != 0:
        raise LaneExactPlanError("whole-share policy requires quantity_precision=0")
    execution_values = {
        "policy_id": _required_string(
            execution.get("policy_id"), label="execution_policy.policy_id", safe_id=True
        ),
        "order_type": order_type,
        "time_in_force": time_in_force,
        "allow_extended_hours": allow_extended_hours,
        "allow_fractional_shares": allow_fractional,
        "quantity_precision": quantity_precision,
        "price_precision": _integer(
            execution.get("price_precision", 4),
            label="execution_policy.price_precision",
            minimum=2,
            maximum=8,
        ),
        "minimum_order_notional_usd": _finite(
            execution.get("minimum_order_notional_usd"),
            label="execution_policy.minimum_order_notional_usd",
        ),
        "maximum_order_notional_usd": _finite(
            execution.get("maximum_order_notional_usd"),
            label="execution_policy.maximum_order_notional_usd",
        ),
        "maximum_total_buy_notional_usd": _finite(
            execution.get("maximum_total_buy_notional_usd"),
            label="execution_policy.maximum_total_buy_notional_usd",
        ),
        "maximum_orders": _integer(
            execution.get("maximum_orders"),
            label="execution_policy.maximum_orders",
        ),
        "max_adverse_slippage_bps": _finite(
            execution.get("max_adverse_slippage_bps"),
            label="execution_policy.max_adverse_slippage_bps",
        ),
        "max_risk_age_seconds": _integer(
            execution.get("max_risk_age_seconds"),
            label="execution_policy.max_risk_age_seconds",
            minimum=1,
        ),
        "max_broker_snapshot_age_seconds": _integer(
            execution.get("max_broker_snapshot_age_seconds"),
            label="execution_policy.max_broker_snapshot_age_seconds",
            minimum=1,
        ),
        "max_price_age_seconds": _integer(
            execution.get("max_price_age_seconds"),
            label="execution_policy.max_price_age_seconds",
            minimum=1,
        ),
        "plan_ttl_seconds": _integer(
            execution.get("plan_ttl_seconds"),
            label="execution_policy.plan_ttl_seconds",
            minimum=1,
        ),
        "snapshot_reconciliation_tolerance_usd": _finite(
            execution.get("snapshot_reconciliation_tolerance_usd", 0.01),
            label="execution_policy.snapshot_reconciliation_tolerance_usd",
        ),
    }
    if execution_values["maximum_order_notional_usd"] <= 0.0:
        raise LaneExactPlanError("maximum_order_notional_usd must be positive")
    if execution_values["maximum_total_buy_notional_usd"] <= 0.0:
        raise LaneExactPlanError("maximum_total_buy_notional_usd must be positive")
    if execution_values["max_adverse_slippage_bps"] > 100.0:
        raise LaneExactPlanError("max_adverse_slippage_bps may not exceed 100")

    capital_basis = _required_string(
        capital.get("capital_basis"), label="capital_policy.capital_basis", safe_id=True
    )
    if capital_basis != risk["capital_basis"]:
        raise LaneExactPlanError("Risk and capital policy basis differ")
    capital_ceiling = capital.get("capital_ceiling_usd")
    if capital_basis != "FULL_ACCOUNT_EQUITY" and capital_ceiling is None:
        raise LaneExactPlanError("capped capital basis requires capital_ceiling_usd")
    return {
        "lane_policy_hash": observed,
        "broker_environment": broker_environment,
        "execution": execution_values,
        "capital_basis": capital_basis,
        "capital_ceiling_usd": (
            None
            if capital_ceiling is None
            else _finite(capital_ceiling, label="capital_policy.capital_ceiling_usd")
        ),
    }


def _normalize_snapshot(
    snapshot: Mapping[str, Any],
    *,
    risk: Mapping[str, Any],
    broker_environment: str,
) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise LaneExactPlanError("broker snapshot must be an object")
    source = copy.deepcopy(dict(snapshot))
    if source.get("schema_version") != BROKER_SNAPSHOT_SCHEMA:
        raise LaneExactPlanError("unsupported broker snapshot schema")
    observed_hash = artifact_content_hash(source)
    declared_hash = source.get("content_hash")
    if declared_hash != observed_hash:
        raise LaneExactPlanError("broker snapshot content hash mismatch")
    if observed_hash != risk["account_state_hash"]:
        raise LaneExactPlanError("Risk account-state hash does not bind broker snapshot")
    for field in ("trade_date", "account_id_hash"):
        if source.get(field) != risk[field]:
            raise LaneExactPlanError(f"Risk and broker snapshot differ: {field}")
    if source.get("broker_environment") != broker_environment:
        raise LaneExactPlanError("broker snapshot environment differs from lane policy")
    snapshot_id = _required_string(
        source.get("snapshot_id"), label="broker_snapshot.snapshot_id", safe_id=True
    )
    captured_at, _ = _timestamp(source.get("captured_at"), label="broker_snapshot.captured_at")
    if source.get("currency") != "USD":
        raise LaneExactPlanError("broker snapshot currency must be USD")
    equity = _finite(source.get("equity"), label="broker_snapshot.equity")
    cash = _finite(source.get("cash"), label="broker_snapshot.cash")
    if equity <= 0.0:
        raise LaneExactPlanError("broker snapshot equity must be positive")

    raw_marks = source.get("price_marks")
    if not isinstance(raw_marks, list) or not raw_marks:
        raise LaneExactPlanError("broker snapshot price_marks must be a non-empty array")
    marks: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_marks):
        if not isinstance(raw, Mapping):
            raise LaneExactPlanError("price mark rows must be objects")
        symbol = _required_string(raw.get("symbol"), label=f"price_marks[{index}].symbol")
        if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in marks:
            raise LaneExactPlanError("price marks contain invalid or duplicate symbols")
        price = _finite(raw.get("price"), label=f"price_marks.{symbol}.price")
        if price <= 0.0:
            raise LaneExactPlanError(f"price_marks.{symbol}.price must be positive")
        as_of, _ = _timestamp(raw.get("as_of"), label=f"price_marks.{symbol}.as_of")
        marks[symbol] = {"symbol": symbol, "price": price, "as_of": as_of}

    raw_positions = source.get("positions")
    if not isinstance(raw_positions, list):
        raise LaneExactPlanError("broker snapshot positions must be an array")
    positions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_positions):
        if not isinstance(raw, Mapping):
            raise LaneExactPlanError("position rows must be objects")
        symbol = _required_string(raw.get("symbol"), label=f"positions[{index}].symbol")
        if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in seen or symbol not in marks:
            raise LaneExactPlanError("positions contain invalid, duplicate, or unpriced symbols")
        seen.add(symbol)
        quantity = _finite(raw.get("quantity"), label=f"positions.{symbol}.quantity")
        if quantity <= 0.0:
            raise LaneExactPlanError("zero positions must be omitted")
        contributions = _position_contributions(
            raw.get("sleeve_contributions"), symbol=symbol, position_quantity=quantity
        )
        positions.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "sleeve_contributions": contributions,
            }
        )
    positions.sort(key=lambda row: row["symbol"])
    return {
        "snapshot_id": snapshot_id,
        "content_hash": observed_hash,
        "captured_at": captured_at,
        "equity": equity,
        "cash": cash,
        "positions": positions,
        "price_marks": [marks[symbol] for symbol in sorted(marks)],
        "marks": marks,
    }


def _position_contributions(
    value: Any, *, symbol: str, position_quantity: float
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise LaneExactPlanError(f"position {symbol} lacks causal sleeve contributions")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0.0
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise LaneExactPlanError("position sleeve contributions must be objects")
        row = copy.deepcopy(dict(raw))
        sleeve_id = _required_string(
            row.get("sleeve_id"),
            label=f"positions.{symbol}.sleeve_contributions[{index}].sleeve_id",
            safe_id=True,
        )
        if sleeve_id in seen:
            raise LaneExactPlanError(f"position {symbol} has duplicate sleeve attribution")
        seen.add(sleeve_id)
        quantity = _finite(
            row.get("quantity"), label=f"positions.{symbol}.{sleeve_id}.quantity"
        )
        if quantity <= 0.0:
            raise LaneExactPlanError("position contribution quantity must be positive")
        _required_string(row.get("decision_id"), label="position decision_id", safe_id=True)
        _sha256(row.get("decision_hash"), label="position decision_hash")
        row["sleeve_id"] = sleeve_id
        row["quantity"] = quantity
        total += quantity
        result.append(row)
    if abs(total - position_quantity) > _TOLERANCE:
        raise LaneExactPlanError(
            f"position {symbol} sleeve quantities do not sum to position quantity"
        )
    return sorted(result, key=lambda row: row["sleeve_id"])


def _prove_freshness(
    *,
    risk: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    execution: Mapping[str, Any],
    planned_at: str,
) -> tuple[str, dt.datetime]:
    planned_raw, planned = _timestamp(planned_at, label="planned_at")
    _, evaluated = _timestamp(risk["evaluated_at"], label="risk.evaluated_at")
    _, captured = _timestamp(snapshot["captured_at"], label="broker_snapshot.captured_at")
    for label, source, maximum in (
        ("Risk package", evaluated, execution["max_risk_age_seconds"]),
        ("broker snapshot", captured, execution["max_broker_snapshot_age_seconds"]),
    ):
        age = (planned - source).total_seconds()
        if age < 0.0 or age > maximum:
            raise LaneExactPlanError(f"{label} is stale or from the future")
    required_symbols = {
        row["symbol"] for row in risk["approved_target_rows"]
    } | {row["symbol"] for row in snapshot["positions"]}
    marks = snapshot["marks"]
    if not required_symbols.issubset(marks):
        missing = sorted(required_symbols - set(marks))
        raise LaneExactPlanError("broker snapshot lacks governed prices: " + ",".join(missing))
    for symbol in required_symbols:
        _, mark_time = _timestamp(marks[symbol]["as_of"], label=f"price_marks.{symbol}.as_of")
        age = (planned - mark_time).total_seconds()
        if age < 0.0 or age > execution["max_price_age_seconds"]:
            raise LaneExactPlanError(f"price mark is stale or from the future: {symbol}")
    return planned_raw, planned


def _deployable_capital(
    *, risk: Mapping[str, Any], snapshot: Mapping[str, Any], policy: Mapping[str, Any]
) -> float:
    candidates = [float(snapshot["equity"])]
    if policy["capital_ceiling_usd"] is not None:
        candidates.append(float(policy["capital_ceiling_usd"]))
    constraints = risk.get("constraints") or {}
    for key in ("maximum_deployable_capital_usd", "capital_ceiling_usd"):
        if key in constraints:
            candidates.append(_finite(constraints[key], label=f"risk.constraints.{key}"))
    deployable = min(candidates)
    if deployable <= 0.0:
        raise LaneExactPlanError("effective deployable capital must be positive")
    return deployable


def _floor_quantity(value: float, precision: int) -> float:
    scale = 10**precision
    return math.floor((value + 1e-12) * scale) / scale


def _scaled_contributions(
    source: Sequence[Mapping[str, Any]], *, quantity: float, basis_field: str
) -> list[dict[str, Any]]:
    bases = [_finite(row.get(basis_field), label=f"sleeve_contribution.{basis_field}") for row in source]
    total = sum(bases)
    if total <= 0.0:
        raise LaneExactPlanError("order sleeve contribution basis must be positive")
    result: list[dict[str, Any]] = []
    assigned = 0.0
    for index, (raw, basis) in enumerate(zip(source, bases, strict=True)):
        row = copy.deepcopy(dict(raw))
        _required_string(row.get("sleeve_id"), label="order contribution sleeve_id", safe_id=True)
        _required_string(row.get("decision_id"), label="order contribution decision_id", safe_id=True)
        _sha256(row.get("decision_hash"), label="order contribution decision_hash")
        fraction = basis / total
        order_quantity = quantity - assigned if index == len(source) - 1 else quantity * fraction
        assigned += order_quantity
        row["order_fraction"] = fraction
        row["order_quantity"] = order_quantity
        result.append(row)
    return sorted(result, key=lambda row: row["sleeve_id"])


def _enforcement_price(
    *, reference_price: float, side: str, execution: Mapping[str, Any]
) -> float:
    if execution["order_type"] == "market":
        return reference_price
    collar = execution["max_adverse_slippage_bps"] / 10000.0
    raw = reference_price * (1.0 + collar if side == "BUY" else 1.0 - collar)
    return round(raw, execution["price_precision"])


def _order_seed(row: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(row))
    result.pop("order_id", None)
    result.pop("client_order_id", None)
    return result


def _order_plan_seed(payload: Mapping[str, Any]) -> str:
    return _json_hash(
        {
            "risk_package_hash": payload["risk_package_hash"],
            "broker_snapshot_hash": payload["broker_snapshot_hash"],
            "lane_policy_hash": payload["lane_policy_hash"],
            "approved_target_hash": payload["approved_target_hash"],
            "sell_orders": [_order_seed(row) for row in payload["sell_orders"]],
            "buy_orders": [_order_seed(row) for row in payload["buy_orders"]],
        }
    )


def _approved_target_hash(payload: Mapping[str, Any]) -> str:
    return _json_hash(
        {
            "trade_date": payload["trade_date"],
            "session_id": payload["session_id"],
            "session_hash": payload["session_hash"],
            "lane_id": payload["lane_id"],
            "lane_kind": payload["lane_kind"],
            "deployment_version": payload["deployment_version"],
            "account_id_hash": payload["account_id_hash"],
            "allocation_id": payload["allocation_id"],
            "allocation_hash": payload["allocation_hash"],
            "target_cash_weight": payload["approved_cash_weight"],
            "capital_basis": payload["capital_basis"],
            "target_rows": payload["approved_target_rows"],
        }
    )


def _build_orders(
    *, risk: Mapping[str, Any], snapshot: Mapping[str, Any], policy: Mapping[str, Any], deployable: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], float]:
    execution = policy["execution"]
    precision = execution["quantity_precision"]
    marks = snapshot["marks"]
    current = {row["symbol"]: row for row in snapshot["positions"]}
    targets = {row["symbol"]: row for row in risk["approved_target_rows"]}
    desired = {
        symbol: _floor_quantity(
            deployable * float(row["target_weight"]) / float(marks[symbol]["price"]),
            precision,
        )
        for symbol, row in targets.items()
    }
    sells: list[dict[str, Any]] = []
    buys: list[dict[str, Any]] = []
    for symbol in sorted(set(current) | set(desired)):
        current_quantity = float(current.get(symbol, {}).get("quantity", 0.0))
        desired_quantity = float(desired.get(symbol, 0.0))
        delta = desired_quantity - current_quantity
        if abs(delta) <= 10 ** (-(precision + 3)):
            continue
        side = "BUY" if delta > 0.0 else "SELL"
        quantity = abs(delta)
        reference = float(marks[symbol]["price"])
        enforcement = _enforcement_price(
            reference_price=reference, side=side, execution=execution
        )
        notional = quantity * enforcement
        if notional + _TOLERANCE < execution["minimum_order_notional_usd"]:
            raise LaneExactPlanError(f"{side}.{symbol} is below minimum order notional")
        if notional > execution["maximum_order_notional_usd"] + _TOLERANCE:
            raise LaneExactPlanError(f"{side}.{symbol} exceeds maximum order notional")
        if side == "BUY":
            contributions = _scaled_contributions(
                targets[symbol]["sleeve_contributions"],
                quantity=quantity,
                basis_field="target_weight",
            )
        else:
            contributions = _scaled_contributions(
                current[symbol]["sleeve_contributions"],
                quantity=quantity,
                basis_field="quantity",
            )
        order = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": execution["order_type"],
            "time_in_force": execution["time_in_force"],
            "extended_hours": execution["allow_extended_hours"],
            "reference_price": reference,
            "enforcement_price": enforcement,
            "notional": notional,
            "sleeve_contributions": contributions,
        }
        (buys if side == "BUY" else sells).append(order)

    if len(sells) + len(buys) > execution["maximum_orders"]:
        raise LaneExactPlanError("exact order count exceeds governed maximum")
    total_buy = sum(row["notional"] for row in buys)
    if total_buy > execution["maximum_total_buy_notional_usd"] + _TOLERANCE:
        raise LaneExactPlanError("exact buy notional exceeds governed maximum")

    plan_seed = _order_plan_seed(
        {
            "risk_package_hash": risk["content_hash"],
            "broker_snapshot_hash": snapshot["content_hash"],
            "lane_policy_hash": policy["lane_policy_hash"],
            "approved_target_hash": risk["approved_target_hash"],
            "sell_orders": sells,
            "buy_orders": buys,
        }
    )
    for side_rows, side in ((sells, "sell"), (buys, "buy")):
        for index, order in enumerate(side_rows, start=1):
            identity = _json_hash(
                {"plan_seed": plan_seed, "side": side.upper(), "index": index, "order": order}
            )
            order["order_id"] = f"order:{side}:{index}:{identity[:16]}"
            order["client_order_id"] = f"cx4-{identity[:39]}"

    expected_by_symbol = {
        symbol: quantity for symbol, quantity in desired.items() if quantity > 0.0
    }
    expected_positions: list[dict[str, Any]] = []
    for symbol in sorted(expected_by_symbol):
        target = targets[symbol]
        expected_positions.append(
            {
                "symbol": symbol,
                "quantity": expected_by_symbol[symbol],
                "sleeve_contributions": _scaled_contributions(
                    target["sleeve_contributions"],
                    quantity=expected_by_symbol[symbol],
                    basis_field="target_weight",
                ),
            }
        )
    expected_cash = float(snapshot["cash"]) + sum(
        row["notional"] for row in sells
    ) - total_buy
    required_cash = max(
        0.0,
        float(snapshot["equity"]) - deployable
        + deployable * float(risk["approved_cash_weight"]),
    )
    if expected_cash + 0.01 < required_cash:
        raise LaneExactPlanError("exact plan violates Risk-approved cash requirement")
    if expected_cash < -_TOLERANCE:
        raise LaneExactPlanError("exact plan would create negative cash")
    return sells, buys, expected_positions, expected_cash


def _starting_state_hash(positions: Sequence[Mapping[str, Any]], cash: float) -> str:
    return _json_hash(
        {
            "positions": [
                {"symbol": row["symbol"], "quantity": row["quantity"]}
                for row in positions
            ],
            "cash": cash,
        }
    )


def _plan_identity_seed(payload: Mapping[str, Any]) -> str:
    return _json_hash(
        {
            "trade_date": payload["trade_date"],
            "lane_id": payload["lane_id"],
            "deployment_version": payload["deployment_version"],
            "account_id_hash": payload["account_id_hash"],
            "session_id": payload["session_id"],
            "allocation_id": payload["allocation_id"],
            "target_hash": payload["target_hash"],
            "approved_target_hash": payload["approved_target_hash"],
            "risk_package_hash": payload["risk_package_hash"],
            "broker_snapshot_hash": payload["broker_snapshot_hash"],
            "lane_policy_hash": payload["lane_policy_hash"],
            "sell_orders": [_order_seed(row) for row in payload["sell_orders"]],
            "buy_orders": [_order_seed(row) for row in payload["buy_orders"]],
        }
    )


def build_lane_exact_execution_plan(
    *,
    lane_risk_package: Mapping[str, Any],
    broker_snapshot: Mapping[str, Any],
    governed_lane_policy: Mapping[str, Any],
    planned_at: str | None = None,
) -> dict[str, Any]:
    """Build one immutable advisory exact-order plan from explicit sources."""

    risk = _validate_risk(lane_risk_package)
    policy = _normalize_policy(governed_lane_policy, risk=risk)
    snapshot = _normalize_snapshot(
        broker_snapshot,
        risk=risk,
        broker_environment=policy["broker_environment"],
    )
    effective_planned_at = planned_at or dt.datetime.now(dt.timezone.utc).isoformat()
    planned_raw, planned = _prove_freshness(
        risk=risk,
        snapshot=snapshot,
        execution=policy["execution"],
        planned_at=effective_planned_at,
    )
    trade_date = _trade_date(risk["trade_date"])
    marked_equity = float(snapshot["cash"]) + sum(
        float(row["quantity"]) * float(snapshot["marks"][row["symbol"]]["price"])
        for row in snapshot["positions"]
    )
    tolerance = policy["execution"]["snapshot_reconciliation_tolerance_usd"]
    if abs(marked_equity - float(snapshot["equity"])) > tolerance:
        raise LaneExactPlanError("broker snapshot positions, cash, and equity do not reconcile")
    deployable = _deployable_capital(risk=risk, snapshot=snapshot, policy=policy)
    sells, buys, expected_positions, expected_cash = _build_orders(
        risk=risk, snapshot=snapshot, policy=policy, deployable=deployable
    )
    expires_at = (
        planned + dt.timedelta(seconds=policy["execution"]["plan_ttl_seconds"])
    ).isoformat()
    body: dict[str, Any] = {
        "schema_version": LANE_EXACT_PLAN_SCHEMA,
        "plan_id": "pending",
        "trade_date": trade_date,
        "planned_at": planned_raw,
        "expires_at": expires_at,
        "status": "ADVISORY",
        "execution_authority": False,
        "lane_id": risk["lane_id"],
        "lane_kind": risk["lane_kind"],
        "deployment_version": risk["deployment_version"],
        "account_id_hash": risk["account_id_hash"],
        "broker_environment": policy["broker_environment"],
        "session_id": risk["session_id"],
        "session_hash": risk["session_hash"],
        "allocation_id": risk["allocation_id"],
        "allocation_hash": risk["allocation_hash"],
        "target_package_id": risk["target_package_id"],
        "target_package_hash": risk["target_package_hash"],
        "target_hash": risk["target_hash"],
        "approved_target_hash": risk["approved_target_hash"],
        "risk_package_id": risk["risk_package_id"],
        "risk_package_hash": risk["content_hash"],
        "risk_decision": risk["decision"],
        "broker_snapshot_id": snapshot["snapshot_id"],
        "broker_snapshot_hash": snapshot["content_hash"],
        "broker_snapshot_captured_at": snapshot["captured_at"],
        "lane_policy_hash": risk["lane_policy_hash"],
        "allocator_policy_hash": risk["allocator_policy_hash"],
        "risk_policy_hash": risk["risk_policy_hash"],
        "capital_policy_hash": risk["capital_policy_hash"],
        "execution_policy_hash": risk["execution_policy_hash"],
        "reconciliation_policy_hash": risk["reconciliation_policy_hash"],
        "capital_basis": risk["capital_basis"],
        "deployable_capital": deployable,
        "starting_equity": snapshot["equity"],
        "starting_cash": snapshot["cash"],
        "starting_positions": snapshot["positions"],
        "starting_state_hash": _starting_state_hash(
            snapshot["positions"], snapshot["cash"]
        ),
        "price_marks": snapshot["price_marks"],
        "approved_cash_weight": risk["approved_cash_weight"],
        "approved_target_rows": risk["approved_target_rows"],
        "sell_orders": sells,
        "buy_orders": buys,
        "expected_posttrade_positions": expected_positions,
        "expected_posttrade_cash": expected_cash,
        "constraints": copy.deepcopy(policy["execution"]),
        "source_hashes": {
            "session": risk["session_hash"],
            "allocation": risk["allocation_hash"],
            "target_package": risk["target_package_hash"],
            "target": risk["target_hash"],
            "approved_target": risk["approved_target_hash"],
            "risk_package": risk["content_hash"],
            "broker_snapshot": snapshot["content_hash"],
            "lane_policy": risk["lane_policy_hash"],
            "allocator_policy": risk["allocator_policy_hash"],
            "risk_policy": risk["risk_policy_hash"],
            "capital_policy": risk["capital_policy_hash"],
            "execution_policy": risk["execution_policy_hash"],
            "reconciliation_policy": risk["reconciliation_policy_hash"],
        },
    }
    seed = _plan_identity_seed(body)
    body["plan_id"] = f"lane-plan:{body['lane_id']}:{trade_date}:{seed[:24]}"
    body["content_hash"] = lane_exact_plan_content_hash(body)
    return _validate_or_raise(
        body,
        lane_risk_package=lane_risk_package,
        broker_snapshot=broker_snapshot,
        governed_lane_policy=governed_lane_policy,
    )


def _validate_orders(payload: Mapping[str, Any]) -> None:
    order_ids: set[str] = set()
    client_ids: set[str] = set()
    plan_seed = _order_plan_seed(payload)
    for field, required_side in (("sell_orders", "SELL"), ("buy_orders", "BUY")):
        rows = payload[field]
        if not isinstance(rows, list):
            raise LaneExactPlanError(f"{field} must be an array")
        symbols: set[str] = set()
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping) or _BROKER_OWNED_FIELDS.intersection(row):
                raise LaneExactPlanError("exact order is invalid or contains broker-owned state")
            symbol = str(row.get("symbol") or "")
            if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in symbols:
                raise LaneExactPlanError("exact orders contain invalid or duplicate symbols")
            symbols.add(symbol)
            if row.get("side") != required_side:
                raise LaneExactPlanError(f"{field} contains the wrong order side")
            quantity = _finite(row.get("quantity"), label=f"{field}.{symbol}.quantity")
            price = _finite(
                row.get("enforcement_price"), label=f"{field}.{symbol}.enforcement_price"
            )
            if quantity <= 0.0 or price <= 0.0:
                raise LaneExactPlanError("exact order quantity and price must be positive")
            if abs(float(row.get("notional", -1.0)) - quantity * price) > 0.01:
                raise LaneExactPlanError("exact order notional is not quantity times price")
            order_id = _required_string(row.get("order_id"), label="order_id", safe_id=True)
            client_id = _required_string(
                row.get("client_order_id"), label="client_order_id", safe_id=True
            )
            if order_id in order_ids or client_id in client_ids:
                raise LaneExactPlanError("exact order identities must be unique")
            order_ids.add(order_id)
            client_ids.add(client_id)
            identity = _json_hash(
                {
                    "plan_seed": plan_seed,
                    "side": required_side,
                    "index": index,
                    "order": _order_seed(row),
                }
            )
            side_name = required_side.lower()
            if order_id != f"order:{side_name}:{index}:{identity[:16]}":
                raise LaneExactPlanError("exact order_id mismatch")
            if client_id != f"cx4-{identity[:39]}":
                raise LaneExactPlanError("exact client_order_id mismatch")
            contributions = row.get("sleeve_contributions")
            if not isinstance(contributions, list) or not contributions:
                raise LaneExactPlanError("every exact order requires sleeve contributions")
            contribution_quantity = 0.0
            for contribution in contributions:
                if not isinstance(contribution, Mapping):
                    raise LaneExactPlanError("order contributions must be objects")
                _required_string(
                    contribution.get("sleeve_id"),
                    label="order contribution sleeve_id",
                    safe_id=True,
                )
                _required_string(
                    contribution.get("decision_id"),
                    label="order contribution decision_id",
                    safe_id=True,
                )
                _sha256(contribution.get("decision_hash"), label="order contribution decision_hash")
                contribution_quantity += _finite(
                    contribution.get("order_quantity"),
                    label="order contribution order_quantity",
                )
            if abs(contribution_quantity - quantity) > _TOLERANCE:
                raise LaneExactPlanError("order contribution quantities do not sum to order")


def _validate_or_raise(
    payload: Mapping[str, Any],
    *,
    lane_risk_package: Mapping[str, Any] | None = None,
    broker_snapshot: Mapping[str, Any] | None = None,
    governed_lane_policy: Mapping[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneExactPlanError("lane exact plan must be an object")
    if set(payload) != _PLAN_FIELDS:
        missing = sorted(_PLAN_FIELDS - set(payload))
        unknown = sorted(set(payload) - _PLAN_FIELDS)
        raise LaneExactPlanError(
            "lane exact plan fields differ; missing="
            + ",".join(missing)
            + "; unknown="
            + ",".join(unknown)
        )
    if payload["schema_version"] != LANE_EXACT_PLAN_SCHEMA:
        raise LaneExactPlanError("unsupported lane exact plan schema")
    if payload["status"] != "ADVISORY" or payload["execution_authority"] is not False:
        raise LaneExactPlanError("v4 plan must remain advisory and non-authoritative")
    _trade_date(payload["trade_date"])
    _, planned = _timestamp(payload["planned_at"], label="planned_at")
    _, expires = _timestamp(payload["expires_at"], label="expires_at")
    if expires <= planned:
        raise LaneExactPlanError("exact plan expiry must follow planning time")
    if as_of is not None:
        _, observed = _timestamp(as_of, label="as_of")
        if observed < planned or observed > expires:
            raise LaneExactPlanError("exact plan is stale or not yet valid")
    if payload["risk_decision"] not in {"APPROVE", "CONSTRAIN"}:
        raise LaneExactPlanError("exact plan does not carry an approved Risk decision")
    lane_id = _required_string(payload["lane_id"], label="lane_id")
    if not _SAFE_LANE.fullmatch(lane_id) or payload["lane_kind"] not in _LANE_KINDS:
        raise LaneExactPlanError("lane identity is invalid")
    for field in (
        "session_id",
        "allocation_id",
        "target_package_id",
        "risk_package_id",
        "broker_snapshot_id",
        "deployment_version",
    ):
        _required_string(payload[field], label=field, safe_id=True)
    hash_fields = (
        "account_id_hash",
        "session_hash",
        "allocation_hash",
        "target_package_hash",
        "target_hash",
        "approved_target_hash",
        "risk_package_hash",
        "broker_snapshot_hash",
        "lane_policy_hash",
        "allocator_policy_hash",
        "risk_policy_hash",
        "capital_policy_hash",
        "execution_policy_hash",
        "reconciliation_policy_hash",
        "starting_state_hash",
    )
    for field in hash_fields:
        _sha256(payload[field], label=field)
    if payload["content_hash"] != lane_exact_plan_content_hash(payload):
        raise LaneExactPlanError("lane exact plan content_hash mismatch")
    approved_cash = _finite(
        payload["approved_cash_weight"], label="approved_cash_weight"
    )
    if approved_cash > 1.0:
        raise LaneExactPlanError("approved_cash_weight may not exceed one")
    approved_rows = payload["approved_target_rows"]
    if not isinstance(approved_rows, list) or not approved_rows:
        raise LaneExactPlanError("approved_target_rows must be a non-empty array")
    approved_by_symbol: dict[str, Mapping[str, Any]] = {}
    approved_gross = 0.0
    for row in approved_rows:
        if not isinstance(row, Mapping):
            raise LaneExactPlanError("approved target rows must be objects")
        symbol = str(row.get("symbol") or "")
        if (
            not _SAFE_SYMBOL.fullmatch(symbol)
            or row.get("ticker") != symbol
            or symbol in approved_by_symbol
        ):
            raise LaneExactPlanError("approved target symbols are invalid or duplicated")
        weight = _finite(row.get("target_weight"), label=f"approved_target.{symbol}")
        if weight <= 0.0:
            raise LaneExactPlanError("approved target weights must be positive")
        contributions = row.get("sleeve_contributions")
        if not isinstance(contributions, list) or not contributions:
            raise LaneExactPlanError("approved targets require sleeve contributions")
        contribution_weight = sum(
            _finite(item.get("target_weight"), label="target contribution weight")
            for item in contributions
            if isinstance(item, Mapping)
        )
        if len(contributions) != sum(isinstance(item, Mapping) for item in contributions):
            raise LaneExactPlanError("target contributions must be objects")
        if abs(contribution_weight - weight) > _TOLERANCE:
            raise LaneExactPlanError("target contributions do not sum to target weight")
        _scaled_contributions(contributions, quantity=1.0, basis_field="target_weight")
        approved_by_symbol[symbol] = row
        approved_gross += weight
    if abs(approved_cash + approved_gross - 1.0) > _TOLERANCE:
        raise LaneExactPlanError("approved target cash plus exposure must equal one")
    if payload["approved_target_hash"] != _approved_target_hash(payload):
        raise LaneExactPlanError("approved target hash mismatch")
    expected_sources = {
        "session": payload["session_hash"],
        "allocation": payload["allocation_hash"],
        "target_package": payload["target_package_hash"],
        "target": payload["target_hash"],
        "approved_target": payload["approved_target_hash"],
        "risk_package": payload["risk_package_hash"],
        "broker_snapshot": payload["broker_snapshot_hash"],
        "lane_policy": payload["lane_policy_hash"],
        "allocator_policy": payload["allocator_policy_hash"],
        "risk_policy": payload["risk_policy_hash"],
        "capital_policy": payload["capital_policy_hash"],
        "execution_policy": payload["execution_policy_hash"],
        "reconciliation_policy": payload["reconciliation_policy_hash"],
    }
    if payload["source_hashes"] != expected_sources:
        raise LaneExactPlanError("exact plan source hash bindings are inconsistent")
    for field in ("starting_positions", "expected_posttrade_positions"):
        rows = payload[field]
        if not isinstance(rows, list):
            raise LaneExactPlanError(f"{field} must be an array")
        symbols: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise LaneExactPlanError(f"{field} rows must be objects")
            symbol = str(row.get("symbol") or "")
            if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in symbols:
                raise LaneExactPlanError(f"{field} symbols are invalid or duplicated")
            symbols.add(symbol)
            quantity = _finite(row.get("quantity"), label=f"{field}.{symbol}.quantity")
            if quantity <= 0.0:
                raise LaneExactPlanError(f"{field} quantities must be positive")
            contributions = row.get("sleeve_contributions")
            if not isinstance(contributions, list) or not contributions:
                raise LaneExactPlanError(f"{field} requires sleeve contributions")
            if field == "starting_positions" and contributions != _position_contributions(
                contributions, symbol=symbol, position_quantity=quantity
            ):
                raise LaneExactPlanError(
                    "starting position attribution is not canonical"
                )
    marks = payload["price_marks"]
    if not isinstance(marks, list) or not marks:
        raise LaneExactPlanError("price_marks must be a non-empty array")
    mark_symbols = [str(row.get("symbol") or "") for row in marks if isinstance(row, Mapping)]
    if len(mark_symbols) != len(marks) or len(mark_symbols) != len(set(mark_symbols)):
        raise LaneExactPlanError("price marks must be unique objects")
    if payload["starting_state_hash"] != _starting_state_hash(
        payload["starting_positions"], payload["starting_cash"]
    ):
        raise LaneExactPlanError("exact plan starting-state hash mismatch")
    _validate_orders(payload)
    if payload["plan_id"] != (
        f"lane-plan:{lane_id}:{payload['trade_date']}:{_plan_identity_seed(payload)[:24]}"
    ):
        raise LaneExactPlanError("lane exact plan_id mismatch")

    positions = {
        row["symbol"]: float(row["quantity"]) for row in payload["starting_positions"]
    }
    cash = float(payload["starting_cash"])
    for order in payload["sell_orders"]:
        symbol = order["symbol"]
        if positions.get(symbol, 0.0) + _TOLERANCE < float(order["quantity"]):
            raise LaneExactPlanError("exact sell exceeds starting position")
        positions[symbol] = positions.get(symbol, 0.0) - float(order["quantity"])
        cash += float(order["notional"])
    for order in payload["buy_orders"]:
        symbol = order["symbol"]
        positions[symbol] = positions.get(symbol, 0.0) + float(order["quantity"])
        cash -= float(order["notional"])
    expected = {
        symbol: quantity for symbol, quantity in positions.items() if quantity > _TOLERANCE
    }
    supplied = {
        row["symbol"]: float(row["quantity"])
        for row in payload["expected_posttrade_positions"]
    }
    if expected != supplied or abs(cash - float(payload["expected_posttrade_cash"])) > 1e-6:
        raise LaneExactPlanError("expected post-trade state is not derived from exact orders")
    for row in payload["expected_posttrade_positions"]:
        symbol = row["symbol"]
        target = approved_by_symbol.get(symbol)
        if target is None:
            raise LaneExactPlanError("post-trade position is absent from approved target")
        expected_contributions = _scaled_contributions(
            target["sleeve_contributions"],
            quantity=float(row["quantity"]),
            basis_field="target_weight",
        )
        if row.get("sleeve_contributions") != expected_contributions:
            raise LaneExactPlanError(
                "post-trade sleeve ownership is not derived from approved target"
            )

    provided = (
        lane_risk_package is not None,
        broker_snapshot is not None,
        governed_lane_policy is not None,
    )
    if any(provided) and not all(provided):
        raise LaneExactPlanError("full source validation requires Risk, snapshot, and policy")
    if all(provided):
        risk = _validate_risk(lane_risk_package)  # type: ignore[arg-type]
        policy = _normalize_policy(governed_lane_policy, risk=risk)  # type: ignore[arg-type]
        snapshot = _normalize_snapshot(  # type: ignore[arg-type]
            broker_snapshot,
            risk=risk,
            broker_environment=policy["broker_environment"],
        )
        bindings = {
            "risk_package_hash": risk["content_hash"],
            "broker_snapshot_hash": snapshot["content_hash"],
            "lane_policy_hash": policy["lane_policy_hash"],
            "approved_target_hash": risk["approved_target_hash"],
            "account_id_hash": risk["account_id_hash"],
            "lane_id": risk["lane_id"],
            "lane_kind": risk["lane_kind"],
            "deployment_version": risk["deployment_version"],
            "session_id": risk["session_id"],
            "session_hash": risk["session_hash"],
            "allocation_id": risk["allocation_id"],
            "allocation_hash": risk["allocation_hash"],
            "target_package_id": risk["target_package_id"],
            "target_package_hash": risk["target_package_hash"],
            "target_hash": risk["target_hash"],
            "allocator_policy_hash": risk["allocator_policy_hash"],
            "risk_policy_hash": risk["risk_policy_hash"],
            "capital_policy_hash": risk["capital_policy_hash"],
            "execution_policy_hash": risk["execution_policy_hash"],
            "reconciliation_policy_hash": risk["reconciliation_policy_hash"],
            "capital_basis": risk["capital_basis"],
            "risk_decision": risk["decision"],
            "broker_snapshot_id": snapshot["snapshot_id"],
            "broker_snapshot_captured_at": snapshot["captured_at"],
            "broker_environment": policy["broker_environment"],
        }
        for field, expected_value in bindings.items():
            if payload[field] != expected_value:
                raise LaneExactPlanError(f"exact plan source binding mismatch: {field}")
        if payload["starting_positions"] != snapshot["positions"]:
            raise LaneExactPlanError("exact plan starting positions differ from broker snapshot")
        if payload["starting_cash"] != snapshot["cash"]:
            raise LaneExactPlanError("exact plan starting cash differs from broker snapshot")
        if payload["starting_equity"] != snapshot["equity"]:
            raise LaneExactPlanError("exact plan starting equity differs from broker snapshot")
        if payload["price_marks"] != snapshot["price_marks"]:
            raise LaneExactPlanError("exact plan prices differ from broker snapshot")
        if payload["approved_target_rows"] != risk["approved_target_rows"]:
            raise LaneExactPlanError("exact plan approved target differs from Risk")
        if payload["approved_cash_weight"] != risk["approved_cash_weight"]:
            raise LaneExactPlanError("exact plan approved cash differs from Risk")
        if payload["constraints"] != policy["execution"]:
            raise LaneExactPlanError("exact plan constraints differ from execution policy")
    return copy.deepcopy(dict(payload))


def validate_lane_exact_execution_plan(
    payload: Mapping[str, Any],
    *,
    lane_risk_package: Mapping[str, Any] | None = None,
    broker_snapshot: Mapping[str, Any] | None = None,
    governed_lane_policy: Mapping[str, Any] | None = None,
    as_of: str | None = None,
) -> list[str]:
    try:
        _validate_or_raise(
            payload,
            lane_risk_package=lane_risk_package,
            broker_snapshot=broker_snapshot,
            governed_lane_policy=governed_lane_policy,
            as_of=as_of,
        )
    except LaneExactPlanError as exc:
        return [f"lane_exact_plan:{exc}"]
    return []


def serialize_lane_exact_execution_plan(payload: Mapping[str, Any]) -> str:
    return canonical_json(_validate_or_raise(payload)) + "\n"


def read_lane_exact_execution_plan(path: Path | str) -> dict[str, Any]:
    artifact_path = Path(path)
    try:
        payload = json.loads(
            artifact_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except LaneExactPlanError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneExactPlanError(f"cannot read exact plan {artifact_path}: {exc}") from exc
    return _validate_or_raise(payload)


def write_lane_exact_execution_plan(
    path: Path | str, payload: Mapping[str, Any]
) -> Path:
    artifact_path = Path(path)
    serialized = serialize_lane_exact_execution_plan(payload)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with artifact_path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise LaneExactPlanError(f"exact plan already exists: {artifact_path}") from exc
    directory_fd = os.open(str(artifact_path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return artifact_path


__all__ = [
    "BROKER_SNAPSHOT_SCHEMA",
    "LANE_EXACT_PLAN_SCHEMA",
    "LaneExactPlanError",
    "artifact_content_hash",
    "build_lane_exact_execution_plan",
    "canonical_json",
    "lane_exact_plan_content_hash",
    "read_lane_exact_execution_plan",
    "serialize_lane_exact_execution_plan",
    "validate_lane_exact_execution_plan",
    "write_lane_exact_execution_plan",
]
