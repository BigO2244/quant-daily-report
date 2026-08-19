from __future__ import annotations

import copy

import pytest

from core.generic_live_dynamic_account import (
    GenericLiveDynamicAccountError,
    build_generic_live_dynamic_account_observation,
    validate_generic_live_dynamic_account_observation,
)


def _observation(**overrides):
    values = {
        "observed_at": "2026-08-24T13:34:30+00:00",
        "account_id_hash": "a" * 64,
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "net_liquidation_equity_usd": 600.0,
        "cash_usd": 200.0,
        "long_market_value_usd": 400.0,
        "short_market_value_usd": 0.0,
        "pending_transfer_in_usd": 0.0,
        "pending_transfer_out_usd": 0.0,
        "source_response_hash": "b" * 64,
    }
    values.update(overrides)
    return build_generic_live_dynamic_account_observation(**values)


def test_observation_is_cash_only_redacted_and_fresh():
    result = validate_generic_live_dynamic_account_observation(
        _observation(), as_of="2026-08-24T13:35:00+00:00",
    )
    assert result["buying_power_persisted"] is False
    assert result["settled_cash_status"] == "FACTUAL_ZERO_PENDING_TRANSFERS"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pending_transfer_in_usd", 1.0),
        ("pending_transfer_out_usd", 1.0),
        ("short_market_value_usd", -1.0),
    ],
)
def test_unsettled_or_short_account_fails_closed(field, value):
    with pytest.raises(GenericLiveDynamicAccountError):
        _observation(**{field: value})


def test_stale_observation_fails_closed():
    with pytest.raises(GenericLiveDynamicAccountError, match="120 seconds"):
        validate_generic_live_dynamic_account_observation(
            _observation(), as_of="2026-08-24T13:36:30+00:00",
        )


def test_resealed_equity_inconsistency_is_rejected():
    changed = copy.deepcopy(_observation())
    changed["net_liquidation_equity_usd"] = 700.0
    from core.generic_live_dynamic_account import _hash
    changed["content_hash"] = _hash(changed)
    with pytest.raises(GenericLiveDynamicAccountError, match="reconcile"):
        validate_generic_live_dynamic_account_observation(changed)
