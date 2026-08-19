from __future__ import annotations

import copy
import json

import pytest

from brokers.alpaca_broker import AlpacaBroker
from core.generic_live_dynamic_account import (
    GenericLiveDynamicAccountError,
    build_generic_live_dynamic_account_observation,
    validate_generic_live_dynamic_account_observation,
)


def _raw(**overrides) -> bytes:
    values = {
        "id": "raw-account-id-never-persisted",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "equity": "600.00",
        "cash": "200.00",
        "long_market_value": "400.00",
        "short_market_value": "0.00",
        "pending_transfer_in": "0.00",
        "pending_transfer_out": "0.00",
    }
    values.update(overrides)
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


def _observation(raw: bytes | None = None):
    source = raw if raw is not None else _raw()
    return build_generic_live_dynamic_account_observation(
        raw_account_response=source,
        observed_at="2026-08-24T13:34:30+00:00",
    )


def test_observation_is_byte_bound_redacted_and_does_not_claim_settled_cash():
    raw = _raw()
    result = validate_generic_live_dynamic_account_observation(
        _observation(raw), raw_account_response=raw,
        as_of="2026-08-24T13:35:00+00:00",
    )
    assert result["buying_power_persisted"] is False
    assert result["settled_cash_status"] == "NOT_DERIVED_FROM_ACCOUNT_ENDPOINT"
    assert result["broker_cash_is_settled_cash"] is False
    assert b"raw-account-id-never-persisted" in raw
    assert "raw-account-id-never-persisted" not in json.dumps(result)


@pytest.mark.parametrize(
    "field",
    [
        "pending_transfer_in", "pending_transfer_out", "long_market_value",
        "short_market_value", "trading_blocked", "account_blocked", "status",
    ],
)
def test_missing_factual_account_field_fails_closed(field):
    value = json.loads(_raw())
    value.pop(field)
    with pytest.raises(GenericLiveDynamicAccountError, match="missing required fields"):
        _observation(json.dumps(value).encode())


def test_non_boolean_blocked_field_fails_closed():
    with pytest.raises(GenericLiveDynamicAccountError, match="factual boolean"):
        _observation(_raw(trading_blocked="unknown"))


def test_stale_observation_fails_closed():
    raw = _raw()
    with pytest.raises(GenericLiveDynamicAccountError, match="120 seconds"):
        validate_generic_live_dynamic_account_observation(
            _observation(raw), raw_account_response=raw,
            as_of="2026-08-24T13:36:30+00:00",
        )


def test_resealed_observation_cannot_substitute_different_raw_bytes():
    raw = _raw()
    changed_raw = _raw(cash="201.00", equity="601.00")
    changed = copy.deepcopy(_observation(raw))
    from core.generic_live_dynamic_account import _hash
    changed["cash_usd"] = 201.0
    changed["net_liquidation_equity_usd"] = 601.0
    changed["source_response_hash"] = "f" * 64
    changed["content_hash"] = _hash(changed)
    with pytest.raises(GenericLiveDynamicAccountError, match="differs from raw source"):
        validate_generic_live_dynamic_account_observation(
            changed, raw_account_response=changed_raw,
        )


class _Client:
    def __init__(self, payload):
        self.payload = payload

    def get_account(self):
        return self.payload


@pytest.mark.parametrize(
    "field",
    [
        "pending_transfer_in", "pending_transfer_out", "long_market_value",
        "short_market_value", "trading_blocked", "account_blocked", "status",
    ],
)
def test_alpaca_dynamic_account_collection_never_defaults_missing_fields(field):
    payload = json.loads(_raw())
    payload.pop(field)
    broker = AlpacaBroker(
        trading_client=_Client(payload), paper=False,
        base_url="https://api.alpaca.markets",
    )
    with pytest.raises(RuntimeError, match=f"missing required field: {field}"):
        broker.get_account()


def test_alpaca_dynamic_account_collection_preserves_explicit_zero_and_false():
    broker = AlpacaBroker(
        trading_client=_Client(json.loads(_raw())), paper=False,
        base_url="https://api.alpaca.markets",
    )
    account = broker.get_account()
    assert account["pending_transfer_in"] == "0.00"
    assert account["long_market_value"] == "400.00"
    assert account["short_market_value"] == "0.00"
    assert account["trading_blocked"] is False
    assert account["account_blocked"] is False
