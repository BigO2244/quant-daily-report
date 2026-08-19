"""Redacted factual account evidence for cash-only dynamic Live capital."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import math
import re
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json


SCHEMA = "caerus.generic_live_dynamic_account_observation.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset(
    {
        "schema_version", "observed_at", "environment", "endpoint",
        "account_id_hash", "status", "trading_blocked", "account_blocked",
        "net_liquidation_equity_usd", "cash_usd", "long_market_value_usd",
        "short_market_value_usd", "pending_transfer_in_usd",
        "pending_transfer_out_usd", "settled_cash_status",
        "source_response_hash", "request_method", "credentials_printed",
        "raw_account_id_printed", "broker_write_performed",
        "buying_power_persisted", "content_hash",
    }
)


class GenericLiveDynamicAccountError(ValueError):
    """Raised when broker balance evidence is incomplete or inconsistent."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


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


def validate_generic_live_dynamic_account_observation(
    payload: Mapping[str, Any], *, as_of: str | None = None,
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
        age = (evaluated - observed).total_seconds()
        if age < 0 or age >= 120:
            raise GenericLiveDynamicAccountError("account observation is not fresher than 120 seconds")
    if not isinstance(payload.get("account_id_hash"), str) or not _SHA.fullmatch(payload["account_id_hash"]):
        raise GenericLiveDynamicAccountError("account pin is invalid")
    if not isinstance(payload.get("source_response_hash"), str) or not _SHA.fullmatch(payload["source_response_hash"]):
        raise GenericLiveDynamicAccountError("source response hash is invalid")
    equity = _number(payload.get("net_liquidation_equity_usd"), "net liquidation equity")
    cash = _number(payload.get("cash_usd"), "cash")
    long_value = _number(payload.get("long_market_value_usd"), "long market value")
    short_value = _number(payload.get("short_market_value_usd"), "short market value")
    pending_in = _number(payload.get("pending_transfer_in_usd"), "pending transfer in")
    pending_out = _number(payload.get("pending_transfer_out_usd"), "pending transfer out")
    if equity <= 0 or cash < 0 or long_value < 0 or short_value != 0:
        raise GenericLiveDynamicAccountError("account is not positive, cash-funded, and long-only")
    if abs(equity - (cash + long_value + short_value)) > 0.02:
        raise GenericLiveDynamicAccountError("net liquidation equity does not reconcile to cash and positions")
    if pending_in != 0 or pending_out != 0 or payload.get("settled_cash_status") != "FACTUAL_ZERO_PENDING_TRANSFERS":
        raise GenericLiveDynamicAccountError("settled cash is not factually proven")
    if str(payload.get("status")).upper() != "ACTIVE" or payload.get("trading_blocked") is not False or payload.get("account_blocked") is not False:
        raise GenericLiveDynamicAccountError("Live account is not active and unblocked")
    for field in (
        "credentials_printed", "raw_account_id_printed", "broker_write_performed",
        "buying_power_persisted",
    ):
        if payload.get(field) is not False:
            raise GenericLiveDynamicAccountError(f"{field} must remain false")
    if not isinstance(payload.get("content_hash"), str) or not _SHA.fullmatch(payload["content_hash"]):
        raise GenericLiveDynamicAccountError("content_hash is invalid")
    if payload["content_hash"] != _hash(payload):
        raise GenericLiveDynamicAccountError("content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_generic_live_dynamic_account_observation(
    *, observed_at: str, account_id_hash: str, status: str,
    trading_blocked: bool, account_blocked: bool,
    net_liquidation_equity_usd: float, cash_usd: float,
    long_market_value_usd: float, short_market_value_usd: float,
    pending_transfer_in_usd: float, pending_transfer_out_usd: float,
    source_response_hash: str,
) -> dict[str, Any]:
    body = {
        "schema_version": SCHEMA,
        "observed_at": observed_at,
        "environment": "LIVE",
        "endpoint": "GET /v2/account",
        "account_id_hash": account_id_hash,
        "status": status,
        "trading_blocked": trading_blocked,
        "account_blocked": account_blocked,
        "net_liquidation_equity_usd": float(net_liquidation_equity_usd),
        "cash_usd": float(cash_usd),
        "long_market_value_usd": float(long_market_value_usd),
        "short_market_value_usd": float(short_market_value_usd),
        "pending_transfer_in_usd": float(pending_transfer_in_usd),
        "pending_transfer_out_usd": float(pending_transfer_out_usd),
        "settled_cash_status": "FACTUAL_ZERO_PENDING_TRANSFERS",
        "source_response_hash": source_response_hash,
        "request_method": "GET",
        "credentials_printed": False,
        "raw_account_id_printed": False,
        "broker_write_performed": False,
        "buying_power_persisted": False,
    }
    body["content_hash"] = _hash(body)
    return validate_generic_live_dynamic_account_observation(body)


__all__ = [
    "SCHEMA", "GenericLiveDynamicAccountError",
    "build_generic_live_dynamic_account_observation",
    "validate_generic_live_dynamic_account_observation",
]
