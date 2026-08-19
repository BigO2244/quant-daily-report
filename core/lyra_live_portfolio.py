"""Owner-scoped Lyra Live portfolio planning and execution evidence.

The module is deliberately independent from the retired single-order Live
pilot.  It validates the exact five-name Lyra target, sizes against factual
broker equity, reserves five percent cash, and produces deterministic,
idempotent order identities.  It never submits an order itself.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping, Sequence


DECISION_SCHEMA = "caerus.lyra_live_owner_decision.v1"
PLAN_SCHEMA = "caerus.lyra_live_portfolio_plan.v1"
INITIALIZATION_SESSION = "2026-08-20"
INITIALIZATION_SIGNAL = "2026-08-17"
INITIALIZATION_TARGET_SHA256 = (
    "6c6378a534bf88cc6d8ef90e26688dd0694ed55bc4651bdbdcad177c3e118bf9"
)
LYRA_VARIANT = "h1_weekly_h6_top5"
TARGET_SYMBOLS = frozenset({"DELL", "INTC", "MU", "STX", "WDC"})


class LyraLivePortfolioError(ValueError):
    """Raised before Live mutation when the owner contract is not exact."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LyraLivePortfolioError("payload is not canonical JSON") from exc


def content_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _finite(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise LyraLivePortfolioError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LyraLivePortfolioError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        raise LyraLivePortfolioError(f"{label} is outside the allowed range")
    return number


def _timestamp(value: Any, *, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LyraLivePortfolioError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise LyraLivePortfolioError(f"{label} needs a timezone")
    return parsed


def validate_owner_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "decision_id", "owner", "decision", "decided_at",
        "initialization", "recurring_cadence", "constraints", "governance",
        "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraLivePortfolioError("owner decision fields differ")
    if payload.get("schema_version") != DECISION_SCHEMA:
        raise LyraLivePortfolioError("owner decision schema differs")
    if (
        payload.get("decision_id") != "lyra-live-owner-decision:20260819"
        or payload.get("owner") != "Brett Olson"
        or payload.get("decision") != "APPROVE"
    ):
        raise LyraLivePortfolioError("owner authority differs")
    decided = _timestamp(payload.get("decided_at"), label="decided_at")
    if decided.date().isoformat() != "2026-08-19":
        raise LyraLivePortfolioError("owner decision date differs")
    expected_initialization = {
        "execution_session": INITIALIZATION_SESSION,
        "execution_time_et": "09:35",
        "signal_as_of": INITIALIZATION_SIGNAL,
        "target_source_path": "outputs/shadow_candidates/2026-08-17/caerus_lyra.json",
        "target_source_sha256": INITIALIZATION_TARGET_SHA256,
        "maximum_orders": 5,
        "one_time_only": True,
    }
    if payload.get("initialization") != expected_initialization:
        raise LyraLivePortfolioError("initialization authority differs")
    expected_cadence = {
        "signal_timing": "MONDAY_XNYS_CLOSE",
        "execution_timing": "IMMEDIATE_NEXT_XNYS_SESSION_09:35_ET",
        "expected_execution_weekday": "TUESDAY",
        "source_variant": LYRA_VARIANT,
        "maximum_orders": 10,
    }
    if payload.get("recurring_cadence") != expected_cadence:
        raise LyraLivePortfolioError("recurring cadence differs")
    expected_constraints = {
        "account_basis": "FRESH_FACTUAL_BROKER_NET_LIQUIDATION_EQUITY",
        "nominal_capital_ceiling_usd": None,
        "maximum_gross_fraction": 0.95,
        "minimum_cash_reserve_fraction": 0.05,
        "minimum_order_notional_usd": 1.0,
        "maximum_names": 5,
        "fractional_shares_required": True,
        "long_only": True,
        "leverage_allowed": False,
        "shorting_allowed": False,
        "opportunistic_orders_allowed": False,
    }
    if payload.get("constraints") != expected_constraints:
        raise LyraLivePortfolioError("portfolio constraints differ")
    expected_governance = {
        "paper_behavior_changed": False,
        "shadow_behavior_changed": False,
        "legacy_live_executor_allowed": False,
        "manual_trade_forcing_allowed": False,
        "no_trade_when_no_target_delta": True,
        "intent_must_precede_submission": True,
        "idempotent_client_order_ids_required": True,
        "posttrade_reconciliation_required": True,
    }
    if payload.get("governance") != expected_governance:
        raise LyraLivePortfolioError("governance constraints differ")
    declared = str(payload.get("content_hash") or "")
    if declared != content_hash(payload):
        raise LyraLivePortfolioError("owner decision content hash differs")
    return copy.deepcopy(dict(payload))


def validate_target_source(
    raw_source: bytes, *, mode: str, execution_session: str,
) -> dict[str, Any]:
    if mode not in {"initialization", "recurring"}:
        raise LyraLivePortfolioError("execution mode differs")
    try:
        target = json.loads(raw_source)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LyraLivePortfolioError("Lyra target source is invalid JSON") from exc
    if not isinstance(target, Mapping):
        raise LyraLivePortfolioError("Lyra target source must be an object")
    source_hash = hashlib.sha256(raw_source).hexdigest()
    signal = str(target.get("trade_date") or "")
    if (
        target.get("strategy_slug") != "caerus_lyra"
        or target.get("source_variant") != LYRA_VARIANT
        or target.get("effective_trade_date") != signal
    ):
        raise LyraLivePortfolioError("Lyra target identity differs")
    try:
        signal_date = dt.date.fromisoformat(signal)
        execution_date = dt.date.fromisoformat(execution_session)
    except ValueError as exc:
        raise LyraLivePortfolioError("Lyra target chronology is invalid") from exc
    if mode == "initialization":
        if (
            execution_session != INITIALIZATION_SESSION
            or signal != INITIALIZATION_SIGNAL
            or source_hash != INITIALIZATION_TARGET_SHA256
        ):
            raise LyraLivePortfolioError("initialization target lineage differs")
    elif (
        signal_date.weekday() != 0
        or execution_date.weekday() != 1
        or execution_date != signal_date + dt.timedelta(days=1)
    ):
        raise LyraLivePortfolioError(
            "recurring target must be Monday-close for Tuesday execution"
        )
    weights = target.get("target_weights")
    if not isinstance(weights, Mapping) or len(weights) != 5:
        raise LyraLivePortfolioError("Lyra target must contain five names")
    normalized = {str(symbol).upper(): _finite(weight, label="target weight") for symbol, weight in weights.items()}
    if any(weight <= 0 for weight in normalized.values()) or abs(sum(normalized.values()) - 1.0) > 1e-9:
        raise LyraLivePortfolioError("Lyra target weights must be positive and sum to one")
    if mode == "initialization" and frozenset(normalized) != TARGET_SYMBOLS:
        raise LyraLivePortfolioError("initialization symbols differ from the Aug 17 target")
    return {
        "signal_as_of": signal,
        "source_hash": source_hash,
        "source_variant": LYRA_VARIANT,
        "weights": dict(sorted(normalized.items())),
    }


def _floor_cents(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _floor_quantity(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN))


def build_portfolio_plan(
    *, owner_decision: Mapping[str, Any], raw_target_source: bytes,
    mode: str, execution_session: str, planned_at: str,
    account_id_hash: str, equity_usd: float, cash_usd: float,
    buying_power_usd: float, positions: Sequence[Mapping[str, Any]],
    open_orders: Sequence[Mapping[str, Any]], assets: Mapping[str, Mapping[str, Any]],
    latest_prices: Mapping[str, float], deployed_sha: str,
) -> dict[str, Any]:
    owner = validate_owner_decision(owner_decision)
    target = validate_target_source(
        raw_target_source, mode=mode, execution_session=execution_session,
    )
    planned = _timestamp(planned_at, label="planned_at")
    if planned.date().isoformat() != execution_session:
        raise LyraLivePortfolioError("plan is not session-bound")
    if len(account_id_hash) != 64 or len(deployed_sha) != 40:
        raise LyraLivePortfolioError("account or deployment pin is invalid")
    equity = _finite(equity_usd, label="equity", positive=True)
    cash = _finite(cash_usd, label="cash")
    buying_power = _finite(buying_power_usd, label="buying power")
    if buying_power > equity + 0.01:
        raise LyraLivePortfolioError("broker buying power implies leverage")
    if open_orders:
        raise LyraLivePortfolioError("broker open orders must be clear")
    current: dict[str, float] = {}
    for row in positions:
        symbol = str(row.get("symbol") or "").upper()
        quantity = _finite(row.get("qty", row.get("quantity")), label=f"{symbol} quantity")
        if not symbol or symbol in current:
            raise LyraLivePortfolioError("broker positions are malformed")
        current[symbol] = quantity
    if mode == "initialization" and any(quantity > 0 for quantity in current.values()):
        raise LyraLivePortfolioError("initialization requires an empty Live account")
    symbols = sorted(set(current) | set(target["weights"]))
    prices: dict[str, float] = {}
    for symbol in symbols:
        prices[symbol] = _finite(latest_prices.get(symbol), label=f"{symbol} price", positive=True)
    for symbol in target["weights"]:
        asset = assets.get(symbol)
        if (
            not isinstance(asset, Mapping)
            or asset.get("tradable") is not True
            or asset.get("fractionable") is not True
            or str(asset.get("status") or "").lower().split(".")[-1] != "active"
        ):
            raise LyraLivePortfolioError(f"{symbol} is not active/tradable/fractionable")

    gross_cap = equity * 0.95
    reserve = equity * 0.05
    target_values = {
        symbol: gross_cap * weight for symbol, weight in target["weights"].items()
    }
    current_values = {symbol: current.get(symbol, 0.0) * prices[symbol] for symbol in symbols}
    sell_orders: list[dict[str, Any]] = []
    for symbol in symbols:
        excess = current_values[symbol] - target_values.get(symbol, 0.0)
        if excess < 1.0:
            continue
        quantity = min(current.get(symbol, 0.0), _floor_quantity(excess / prices[symbol]))
        if quantity <= 0:
            continue
        sell_orders.append({"symbol": symbol, "side": "SELL", "quantity": quantity, "notional": None})
    expected_sell_notional = sum(order["quantity"] * prices[order["symbol"]] for order in sell_orders)
    available_for_buys = max(0.0, cash + expected_sell_notional - reserve)
    buy_needs = {
        symbol: max(0.0, target_values[symbol] - current_values.get(symbol, 0.0))
        for symbol in target["weights"]
    }
    total_need = sum(buy_needs.values())
    scale = min(1.0, available_for_buys / total_need) if total_need > 0 else 0.0
    buy_orders: list[dict[str, Any]] = []
    for symbol in sorted(buy_needs):
        notional = _floor_cents(buy_needs[symbol] * scale)
        if notional >= 1.0:
            buy_orders.append({"symbol": symbol, "side": "BUY", "quantity": None, "notional": notional})
    orders = [*sell_orders, *buy_orders]
    maximum_orders = 5 if mode == "initialization" else 10
    if len(orders) > maximum_orders:
        raise LyraLivePortfolioError("owner maximum order count is exceeded")
    total_buy = sum(float(order["notional"] or 0.0) for order in buy_orders)
    if total_buy > available_for_buys + 1e-9:
        raise LyraLivePortfolioError("planned buys breach factual cash capacity")
    if mode == "initialization" and len(buy_orders) != 5:
        raise LyraLivePortfolioError("initialization must realize all five Lyra names")
    projected_gross = sum(target_values.values()) if orders else sum(current_values.values())
    if projected_gross > gross_cap + 0.01:
        raise LyraLivePortfolioError("projected gross exceeds 95% of factual NAV")
    order_seed = {
        "execution_session": execution_session, "mode": mode,
        "owner_decision_hash": owner["content_hash"],
        "target_source_hash": target["source_hash"], "account_id_hash": account_id_hash,
        "equity_usd": equity, "cash_usd": cash, "positions": dict(sorted(current.items())),
        "orders": orders,
    }
    seed_hash = hashlib.sha256(canonical_json(order_seed).encode()).hexdigest()
    for index, order in enumerate(orders):
        order["order_index"] = index
        order["client_order_id"] = f"cxl-{seed_hash[:32]}-{index:02d}"
    body = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": f"lyra-live:{execution_session}:{seed_hash[:24]}",
        "mode": mode, "execution_session": execution_session,
        "signal_as_of": target["signal_as_of"], "planned_at": planned_at,
        "owner_decision_hash": owner["content_hash"],
        "target_source_hash": target["source_hash"],
        "source_variant": target["source_variant"], "target_weights": target["weights"],
        "account_id_hash": account_id_hash, "deployed_sha": deployed_sha,
        "factual_equity_usd": equity, "factual_cash_usd": cash,
        "factual_buying_power_usd": buying_power,
        "maximum_gross_usd": gross_cap, "required_cash_reserve_usd": reserve,
        "expected_sell_notional_usd": expected_sell_notional,
        "maximum_buy_notional_usd": available_for_buys,
        "total_buy_notional_usd": total_buy,
        "starting_positions": dict(sorted(current.items())),
        "latest_prices": dict(sorted(prices.items())),
        "orders": orders, "maximum_orders": maximum_orders,
        "projected_gross_usd": projected_gross,
        "status": "READY" if orders else "NO_TARGET_DELTA",
        "execution_authority": True,
    }
    body["content_hash"] = content_hash(body)
    return body


def validate_plan(payload: Mapping[str, Any], *, owner_decision: Mapping[str, Any]) -> dict[str, Any]:
    owner = validate_owner_decision(owner_decision)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != PLAN_SCHEMA:
        raise LyraLivePortfolioError("portfolio plan schema differs")
    if payload.get("content_hash") != content_hash(payload):
        raise LyraLivePortfolioError("portfolio plan content hash differs")
    if payload.get("owner_decision_hash") != owner["content_hash"]:
        raise LyraLivePortfolioError("portfolio plan owner lineage differs")
    orders = payload.get("orders")
    if not isinstance(orders, list) or len(orders) > int(payload.get("maximum_orders", -1)):
        raise LyraLivePortfolioError("portfolio plan order count differs")
    if len({order.get("client_order_id") for order in orders}) != len(orders):
        raise LyraLivePortfolioError("portfolio plan client ids are not unique")
    return copy.deepcopy(dict(payload))


__all__ = [
    "INITIALIZATION_SESSION", "INITIALIZATION_SIGNAL", "INITIALIZATION_TARGET_SHA256",
    "LyraLivePortfolioError", "build_portfolio_plan", "canonical_json", "content_hash",
    "validate_owner_decision", "validate_plan", "validate_target_source",
]
