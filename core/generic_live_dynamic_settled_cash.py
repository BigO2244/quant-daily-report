"""Canonical settled-cash evidence from complete byte-bound broker history."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json
from core.generic_live_dynamic_account import (
    GenericLiveDynamicAccountError,
    validate_generic_live_dynamic_account_observation,
)
from core.settled_cash import compute_settled_cash
from paper.trading_calendar import ET_TZ, prev_trading_day


SCHEMA = "caerus.generic_live_dynamic_settled_cash_evidence.v1"
ORDER_SOURCE_SCHEMA = "caerus.alpaca_complete_order_history_source.v1"
FILL_SOURCE_SCHEMA = "caerus.alpaca_complete_fill_history_source.v1"
METHOD = "core.settled_cash.compute_settled_cash:T_PLUS_1_XNYS_V1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_FIELDS = frozenset(
    {
        "schema_version", "captured_at", "endpoint", "query",
        "pagination_complete", "next_page_token", "pages_retrieved", "rows",
        "content_hash",
    }
)
_FIELDS = frozenset(
    {
        "schema_version", "evaluated_at", "as_of_date", "account_id_hash",
        "account_observation_hash", "account_source_hash", "orders_source_hash",
        "orders_source_content_hash", "fills_source_hash",
        "fills_source_content_hash", "history_captured_at", "history_after",
        "history_until", "order_count", "fill_count", "history_complete",
        "broker_cash_usd", "unsettled_proceeds_usd", "settled_cash_usd",
        "buffered_settled_cash_usd", "fail_closed", "reason_code", "method",
        "raw_sources_required", "broker_write_performed", "execution_authority",
        "approval_authority", "content_hash",
    }
)


class GenericLiveDynamicSettledCashError(ValueError):
    """Raised when settled cash lacks fresh, complete, causal broker evidence."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw:
        raise GenericLiveDynamicSettledCashError(f"{label} source bytes are required")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise GenericLiveDynamicSettledCashError(
                    f"{label} source contains duplicate keys"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                GenericLiveDynamicSettledCashError(
                    f"{label} source contains non-finite values"
                )
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenericLiveDynamicSettledCashError(f"{label} source is invalid JSON") from exc
    if not isinstance(value, dict):
        raise GenericLiveDynamicSettledCashError(f"{label} source must be an object")
    return value


def _time(value: Any, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveDynamicSettledCashError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise GenericLiveDynamicSettledCashError(f"{label} needs a timezone")
    return parsed


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise GenericLiveDynamicSettledCashError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GenericLiveDynamicSettledCashError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise GenericLiveDynamicSettledCashError(f"{label} must be finite and nonnegative")
    return number


def _query_after_date(value: Any, label: str) -> dt.date:
    text = str(value)
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return _time(text, label).astimezone(ET_TZ).date()


def _source(raw: bytes, *, kind: str, evaluated: dt.datetime, as_of_date: str) -> dict[str, Any]:
    source = _strict_json(raw, kind)
    if set(source) != _SOURCE_FIELDS:
        raise GenericLiveDynamicSettledCashError(f"{kind} source fields are invalid")
    schema = ORDER_SOURCE_SCHEMA if kind == "orders" else FILL_SOURCE_SCHEMA
    endpoint = "GET /v2/orders" if kind == "orders" else "GET /v2/account/activities/FILL"
    if source.get("schema_version") != schema or source.get("endpoint") != endpoint:
        raise GenericLiveDynamicSettledCashError(f"{kind} source contract is invalid")
    if source.get("pagination_complete") is not True or source.get("next_page_token") is not None:
        raise GenericLiveDynamicSettledCashError(f"{kind} history is incomplete")
    if type(source.get("pages_retrieved")) is not int or source["pages_retrieved"] < 1:
        raise GenericLiveDynamicSettledCashError(f"{kind} page evidence is invalid")
    if not isinstance(source.get("rows"), list) or any(
        not isinstance(row, Mapping) for row in source["rows"]
    ):
        raise GenericLiveDynamicSettledCashError(f"{kind} rows are invalid")
    query = source.get("query")
    expected_query_fields = (
        {"status", "after", "until", "direction"}
        if kind == "orders" else {"activity_type", "after", "until", "direction"}
    )
    if not isinstance(query, Mapping) or set(query) != expected_query_fields:
        raise GenericLiveDynamicSettledCashError(f"{kind} query is invalid")
    if query.get("direction") != "asc":
        raise GenericLiveDynamicSettledCashError(f"{kind} query direction is incomplete")
    if kind == "orders" and query.get("status") != "all":
        raise GenericLiveDynamicSettledCashError("orders query did not request all statuses")
    if kind == "fills" and query.get("activity_type") != "FILL":
        raise GenericLiveDynamicSettledCashError("fills query did not request FILL history")
    captured = _time(source.get("captured_at"), f"{kind} captured_at")
    until = _time(query.get("until"), f"{kind} query until")
    age = (evaluated - captured).total_seconds()
    if age < 0 or age >= 120:
        raise GenericLiveDynamicSettledCashError(f"{kind} source is not fresher than 120 seconds")
    if until != captured:
        raise GenericLiveDynamicSettledCashError(f"{kind} query until differs from capture")
    after_date = _query_after_date(query.get("after"), f"{kind} query after")
    required_start = dt.date.fromisoformat(prev_trading_day(as_of_date))
    if after_date > required_start:
        raise GenericLiveDynamicSettledCashError(f"{kind} history does not cover the unsettled window")
    declared = source.get("content_hash")
    if not isinstance(declared, str) or not _SHA.fullmatch(declared) or declared != _hash(source):
        raise GenericLiveDynamicSettledCashError(f"{kind} source hash mismatch")
    return source


def _reconcile_order_fills(
    orders: list[dict[str, Any]], fills: list[dict[str, Any]], *, captured_at: str,
) -> list[dict[str, Any]]:
    captured = _time(captured_at, "history captured_at")
    order_map: dict[str, dict[str, Any]] = {}
    for order in orders:
        required = {"id", "symbol", "side", "status", "filled_qty"}
        if any(field not in order or order[field] in (None, "") for field in required):
            raise GenericLiveDynamicSettledCashError("order history row is incomplete")
        order_id = str(order["id"])
        if order_id in order_map:
            raise GenericLiveDynamicSettledCashError("order history contains duplicate ids")
        filled_qty = _finite(order["filled_qty"], "order filled quantity")
        if filled_qty > 0 and (
            order.get("filled_avg_price") in (None, "")
            or order.get("filled_at") in (None, "")
        ):
            raise GenericLiveDynamicSettledCashError(
                "filled order history row lacks fill economics"
            )
        order_map[order_id] = order

    fill_ids: set[str] = set()
    by_order: dict[str, list[dict[str, Any]]] = {}
    confirmed_sells: list[dict[str, Any]] = []
    for fill in fills:
        required = {"id", "order_id", "symbol", "side", "qty", "price", "transaction_time"}
        if any(field not in fill or fill[field] in (None, "") for field in required):
            raise GenericLiveDynamicSettledCashError("fill history row is incomplete")
        fill_id = str(fill["id"])
        if fill_id in fill_ids:
            raise GenericLiveDynamicSettledCashError("fill history contains duplicate ids")
        fill_ids.add(fill_id)
        order_id = str(fill["order_id"])
        order = order_map.get(order_id)
        if order is None:
            raise GenericLiveDynamicSettledCashError("fill has no matching order")
        side = str(fill["side"]).lower()
        if str(order["symbol"]).upper() != str(fill["symbol"]).upper() or str(order["side"]).lower() != side:
            raise GenericLiveDynamicSettledCashError("fill identity differs from order")
        quantity = _finite(fill["qty"], "fill quantity")
        price = _finite(fill["price"], "fill price")
        fill_time = _time(fill["transaction_time"], "fill transaction_time")
        if fill_time > captured:
            raise GenericLiveDynamicSettledCashError("fill occurs after history capture")
        if quantity <= 0 or price <= 0:
            raise GenericLiveDynamicSettledCashError("fill economics must be positive")
        by_order.setdefault(order_id, []).append(fill)
        if side in {"sell", "sell_short", "close", "reduce"}:
            confirmed_sells.append(
                {"order_id": order_id, "symbol": fill["symbol"], "proceeds": quantity * price}
            )

    for order_id, order in order_map.items():
        filled_qty = _finite(order["filled_qty"], "order filled quantity")
        matched = by_order.get(order_id, [])
        matched_qty = sum(_finite(fill["qty"], "fill quantity") for fill in matched)
        if abs(filled_qty - matched_qty) > 1e-9:
            raise GenericLiveDynamicSettledCashError("order and fill quantities do not reconcile")
        if filled_qty > 0:
            latest_fill = max(
                _time(fill["transaction_time"], "fill transaction time")
                for fill in matched
            )
            order_filled_at = _time(order["filled_at"], "order filled_at")
            if order_filled_at != latest_fill:
                raise GenericLiveDynamicSettledCashError(
                    "order fill time differs from fill history"
                )
            weighted = sum(
                _finite(fill["qty"], "fill quantity") * _finite(fill["price"], "fill price")
                for fill in matched
            ) / filled_qty
            if abs(weighted - _finite(order["filled_avg_price"], "order average price")) > 1e-6:
                raise GenericLiveDynamicSettledCashError("order and fill prices do not reconcile")
    return confirmed_sells


def _derive(
    *, account_observation: Mapping[str, Any], raw_account_response: bytes,
    raw_order_history_source: bytes, raw_fill_history_source: bytes,
    evaluated_at: str, as_of_date: str,
) -> dict[str, Any]:
    evaluated = _time(evaluated_at, "evaluated_at")
    if evaluated.astimezone(ET_TZ).date().isoformat() != as_of_date:
        raise GenericLiveDynamicSettledCashError("as_of_date differs from evaluated ET date")
    try:
        account = validate_generic_live_dynamic_account_observation(
            account_observation, raw_account_response=raw_account_response,
            as_of=evaluated_at,
        )
    except GenericLiveDynamicAccountError as exc:
        raise GenericLiveDynamicSettledCashError(str(exc)) from exc
    orders_source = _source(
        raw_order_history_source, kind="orders", evaluated=evaluated,
        as_of_date=as_of_date,
    )
    fills_source = _source(
        raw_fill_history_source, kind="fills", evaluated=evaluated,
        as_of_date=as_of_date,
    )
    if orders_source["captured_at"] != fills_source["captured_at"]:
        raise GenericLiveDynamicSettledCashError("order and fill capture times differ")
    if orders_source["query"]["after"] != fills_source["query"]["after"] or orders_source["query"]["until"] != fills_source["query"]["until"]:
        raise GenericLiveDynamicSettledCashError("order and fill history windows differ")
    orders = [dict(row) for row in orders_source["rows"]]
    fills = [dict(row) for row in fills_source["rows"]]
    confirmed_sells = _reconcile_order_fills(
        orders, fills, captured_at=orders_source["captured_at"]
    )
    result = compute_settled_cash(
        broker_cash=account["cash_usd"], orders=orders,
        as_of_date=as_of_date, orders_available=True,
        confirmed_sells=confirmed_sells,
    )
    if result.fail_closed:
        raise GenericLiveDynamicSettledCashError("settled cash computation failed closed")
    body = {
        "schema_version": SCHEMA,
        "evaluated_at": evaluated_at,
        "as_of_date": as_of_date,
        "account_id_hash": account["account_id_hash"],
        "account_observation_hash": account["content_hash"],
        "account_source_hash": account["source_response_hash"],
        "orders_source_hash": hashlib.sha256(raw_order_history_source).hexdigest(),
        "orders_source_content_hash": orders_source["content_hash"],
        "fills_source_hash": hashlib.sha256(raw_fill_history_source).hexdigest(),
        "fills_source_content_hash": fills_source["content_hash"],
        "history_captured_at": orders_source["captured_at"],
        "history_after": orders_source["query"]["after"],
        "history_until": orders_source["query"]["until"],
        "order_count": len(orders),
        "fill_count": len(fills),
        "history_complete": True,
        "broker_cash_usd": result.broker_cash,
        "unsettled_proceeds_usd": result.unsettled_proceeds,
        "settled_cash_usd": result.settled_cash,
        "buffered_settled_cash_usd": result.buffered_settled_cash,
        "fail_closed": False,
        "reason_code": None,
        "method": METHOD,
        "raw_sources_required": True,
        "broker_write_performed": False,
        "execution_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return body


def build_generic_live_dynamic_settled_cash_evidence(
    *, account_observation: Mapping[str, Any], raw_account_response: bytes,
    raw_order_history_source: bytes, raw_fill_history_source: bytes,
    evaluated_at: str, as_of_date: str,
) -> dict[str, Any]:
    return _derive(
        account_observation=account_observation,
        raw_account_response=raw_account_response,
        raw_order_history_source=raw_order_history_source,
        raw_fill_history_source=raw_fill_history_source,
        evaluated_at=evaluated_at, as_of_date=as_of_date,
    )


def validate_generic_live_dynamic_settled_cash_evidence(
    payload: Mapping[str, Any], *, account_observation: Mapping[str, Any],
    raw_account_response: bytes, raw_order_history_source: bytes,
    raw_fill_history_source: bytes,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise GenericLiveDynamicSettledCashError("settled-cash evidence fields are invalid")
    expected = _derive(
        account_observation=account_observation,
        raw_account_response=raw_account_response,
        raw_order_history_source=raw_order_history_source,
        raw_fill_history_source=raw_fill_history_source,
        evaluated_at=str(payload.get("evaluated_at")),
        as_of_date=str(payload.get("as_of_date")),
    )
    if dict(payload) != expected:
        raise GenericLiveDynamicSettledCashError("settled-cash evidence differs from raw sources")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveDynamicSettledCashError("settled-cash evidence hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "SCHEMA", "ORDER_SOURCE_SCHEMA", "FILL_SOURCE_SCHEMA", "METHOD",
    "GenericLiveDynamicSettledCashError",
    "build_generic_live_dynamic_settled_cash_evidence",
    "validate_generic_live_dynamic_settled_cash_evidence",
]
