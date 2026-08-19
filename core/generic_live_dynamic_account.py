"""Strict byte-bound factual account evidence for dynamic generic Live.

The Alpaca account endpoint reports broker cash, not settled cash. This
contract deliberately makes no settled-cash claim; that requires the separate
complete order/fill-history adapter.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json


SCHEMA = "caerus.generic_live_dynamic_account_observation.v2"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_RAW_REQUIRED = frozenset(
    {
        "id", "status", "trading_blocked", "account_blocked", "equity",
        "cash", "long_market_value", "short_market_value",
        "pending_transfer_in", "pending_transfer_out",
    }
)
_FIELDS = frozenset(
    {
        "schema_version", "observed_at", "environment", "endpoint",
        "account_id_hash", "status", "trading_blocked", "account_blocked",
        "net_liquidation_equity_usd", "cash_usd", "long_market_value_usd",
        "short_market_value_usd", "pending_transfer_in_usd",
        "pending_transfer_out_usd", "settled_cash_status",
        "broker_cash_is_settled_cash", "source_response_hash",
        "source_response_size_bytes", "raw_source_required", "request_method",
        "credentials_printed", "raw_account_id_printed",
        "broker_write_performed", "buying_power_persisted", "content_hash",
    }
)


class GenericLiveDynamicAccountError(ValueError):
    """Raised when factual raw account evidence is incomplete or inconsistent."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw:
        raise GenericLiveDynamicAccountError("raw account response bytes are required")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise GenericLiveDynamicAccountError(
                    "raw account response contains duplicate keys"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                GenericLiveDynamicAccountError(
                    "raw account response contains non-finite values"
                )
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenericLiveDynamicAccountError("raw account response is invalid JSON") from exc
    if not isinstance(value, dict):
        raise GenericLiveDynamicAccountError("raw account response must be an object")
    missing = sorted(
        field for field in _RAW_REQUIRED
        if field not in value or value[field] is None or value[field] == ""
    )
    if missing:
        raise GenericLiveDynamicAccountError(
            "raw account response is missing required fields: " + ",".join(missing)
        )
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise GenericLiveDynamicAccountError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GenericLiveDynamicAccountError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise GenericLiveDynamicAccountError(f"{label} must be finite")
    return number


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise GenericLiveDynamicAccountError(f"{label} must be a factual boolean")
    return value


def _derive(raw: bytes, observed_at: str) -> dict[str, Any]:
    source = _strict_json_object(raw)
    status = str(source["status"]).strip().upper()
    if status.startswith("ACCOUNTSTATUS."):
        status = status.split(".", 1)[1]
    account_id = str(source["id"]).strip()
    if not account_id:
        raise GenericLiveDynamicAccountError("raw account id is empty")
    body = {
        "schema_version": SCHEMA,
        "observed_at": observed_at,
        "environment": "LIVE",
        "endpoint": "GET /v2/account",
        "account_id_hash": hashlib.sha256(account_id.encode()).hexdigest(),
        "status": status,
        "trading_blocked": _boolean(source["trading_blocked"], "trading_blocked"),
        "account_blocked": _boolean(source["account_blocked"], "account_blocked"),
        "net_liquidation_equity_usd": _number(source["equity"], "equity"),
        "cash_usd": _number(source["cash"], "cash"),
        "long_market_value_usd": _number(source["long_market_value"], "long market value"),
        "short_market_value_usd": _number(source["short_market_value"], "short market value"),
        "pending_transfer_in_usd": _number(source["pending_transfer_in"], "pending transfer in"),
        "pending_transfer_out_usd": _number(source["pending_transfer_out"], "pending transfer out"),
        "settled_cash_status": "NOT_DERIVED_FROM_ACCOUNT_ENDPOINT",
        "broker_cash_is_settled_cash": False,
        "source_response_hash": hashlib.sha256(raw).hexdigest(),
        "source_response_size_bytes": len(raw),
        "raw_source_required": True,
        "request_method": "GET",
        "credentials_printed": False,
        "raw_account_id_printed": False,
        "broker_write_performed": False,
        "buying_power_persisted": False,
    }
    body["content_hash"] = _hash(body)
    return body


def validate_generic_live_dynamic_account_observation(
    payload: Mapping[str, Any], *, raw_account_response: bytes,
    as_of: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise GenericLiveDynamicAccountError("account observation fields are invalid")
    if payload.get("schema_version") != SCHEMA:
        raise GenericLiveDynamicAccountError("account observation schema is invalid")
    if payload.get("environment") != "LIVE" or payload.get("endpoint") != "GET /v2/account":
        raise GenericLiveDynamicAccountError("only the factual Live account endpoint is accepted")
    if payload.get("request_method") != "GET":
        raise GenericLiveDynamicAccountError("account observation must be read-only GET")
    try:
        observed = dt.datetime.fromisoformat(str(payload.get("observed_at")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveDynamicAccountError("observed_at is invalid") from exc
    if observed.tzinfo is None:
        raise GenericLiveDynamicAccountError("observed_at needs a timezone")
    if as_of is not None:
        try:
            evaluated = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GenericLiveDynamicAccountError("as_of is invalid") from exc
        if evaluated.tzinfo is None:
            raise GenericLiveDynamicAccountError("as_of needs a timezone")
        age = (evaluated - observed).total_seconds()
        if age < 0 or age >= 120:
            raise GenericLiveDynamicAccountError("account observation is not fresher than 120 seconds")
    if payload.get("settled_cash_status") != "NOT_DERIVED_FROM_ACCOUNT_ENDPOINT":
        raise GenericLiveDynamicAccountError("account endpoint cannot claim settled cash")
    if payload.get("broker_cash_is_settled_cash") is not False:
        raise GenericLiveDynamicAccountError("broker cash cannot be relabelled as settled cash")
    if payload.get("raw_source_required") is not True:
        raise GenericLiveDynamicAccountError("raw account source must remain required")
    for field in (
        "credentials_printed", "raw_account_id_printed", "broker_write_performed",
        "buying_power_persisted",
    ):
        if payload.get(field) is not False:
            raise GenericLiveDynamicAccountError(f"{field} must remain false")
    if not isinstance(payload.get("account_id_hash"), str) or not _SHA.fullmatch(payload["account_id_hash"]):
        raise GenericLiveDynamicAccountError("account pin is invalid")
    if not isinstance(payload.get("source_response_hash"), str) or not _SHA.fullmatch(payload["source_response_hash"]):
        raise GenericLiveDynamicAccountError("source response hash is invalid")
    if payload.get("source_response_size_bytes") != len(raw_account_response):
        raise GenericLiveDynamicAccountError("raw account response size differs")
    expected = _derive(raw_account_response, str(payload["observed_at"]))
    if dict(payload) != expected:
        raise GenericLiveDynamicAccountError("account observation differs from raw source")
    equity = _number(payload.get("net_liquidation_equity_usd"), "net liquidation equity")
    cash = _number(payload.get("cash_usd"), "cash")
    long_value = _number(payload.get("long_market_value_usd"), "long market value")
    short_value = _number(payload.get("short_market_value_usd"), "short market value")
    if equity <= 0 or cash < 0 or long_value < 0 or short_value != 0:
        raise GenericLiveDynamicAccountError("account is not positive, cash-funded, and long-only")
    if abs(equity - (cash + long_value + short_value)) > 0.02:
        raise GenericLiveDynamicAccountError("net liquidation equity does not reconcile to cash and positions")
    if payload.get("pending_transfer_in_usd") != 0 or payload.get("pending_transfer_out_usd") != 0:
        raise GenericLiveDynamicAccountError("pending transfers are not zero")
    if payload.get("status") != "ACTIVE" or payload.get("trading_blocked") is not False or payload.get("account_blocked") is not False:
        raise GenericLiveDynamicAccountError("Live account is not active and unblocked")
    if not isinstance(payload.get("content_hash"), str) or not _SHA.fullmatch(payload["content_hash"]):
        raise GenericLiveDynamicAccountError("content_hash is invalid")
    if payload["content_hash"] != _hash(payload):
        raise GenericLiveDynamicAccountError("content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_generic_live_dynamic_account_observation(
    *, raw_account_response: bytes, observed_at: str,
) -> dict[str, Any]:
    body = _derive(raw_account_response, observed_at)
    return validate_generic_live_dynamic_account_observation(
        body, raw_account_response=raw_account_response
    )


__all__ = [
    "SCHEMA", "GenericLiveDynamicAccountError",
    "build_generic_live_dynamic_account_observation",
    "validate_generic_live_dynamic_account_observation",
]
