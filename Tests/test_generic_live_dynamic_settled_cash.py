from __future__ import annotations

import copy
import json

import pytest

from core.generic_live_dynamic_account import (
    build_generic_live_dynamic_account_observation,
)
from core.generic_live_dynamic_settled_cash import (
    FILL_SOURCE_SCHEMA,
    ORDER_SOURCE_SCHEMA,
    GenericLiveDynamicSettledCashError,
    build_generic_live_dynamic_settled_cash_evidence,
    validate_generic_live_dynamic_settled_cash_evidence,
)
from Tests.test_generic_live_dynamic_account import _raw as _raw_account


CAPTURED = "2026-08-24T13:34:40+00:00"
EVALUATED = "2026-08-24T13:35:00+00:00"
AS_OF = "2026-08-24"


def _sealed_bytes(value: dict) -> bytes:
    from core.generic_live_dynamic_settled_cash import _hash
    value = copy.deepcopy(value)
    value["content_hash"] = _hash(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sources(*, same_day_sell: bool = True) -> tuple[bytes, bytes]:
    orders = []
    fills = []
    if same_day_sell:
        orders.append(
            {
                "id": "order-sell-1", "symbol": "DELL", "side": "sell",
                "status": "filled", "filled_qty": "1",
                "filled_avg_price": "100", "filled_at": CAPTURED,
            }
        )
        fills.append(
            {
                "id": "fill-sell-1", "order_id": "order-sell-1",
                "symbol": "DELL", "side": "sell", "qty": "1",
                "price": "100", "transaction_time": CAPTURED,
            }
        )
    common = {
        "captured_at": CAPTURED,
        "pagination_complete": True,
        "next_page_token": None,
        "pages_retrieved": 1,
    }
    order_source = {
        **common,
        "schema_version": ORDER_SOURCE_SCHEMA,
        "endpoint": "GET /v2/orders",
        "query": {
            "status": "all", "after": "2026-08-21T00:00:00-04:00",
            "until": CAPTURED, "direction": "asc",
        },
        "rows": orders,
    }
    fill_source = {
        **common,
        "schema_version": FILL_SOURCE_SCHEMA,
        "endpoint": "GET /v2/account/activities/FILL",
        "query": {
            "activity_type": "FILL", "after": "2026-08-21T00:00:00-04:00",
            "until": CAPTURED, "direction": "asc",
        },
        "rows": fills,
    }
    return _sealed_bytes(order_source), _sealed_bytes(fill_source)


def _account() -> tuple[bytes, dict]:
    raw = _raw_account()
    return raw, build_generic_live_dynamic_account_observation(
        raw_account_response=raw,
        observed_at="2026-08-24T13:34:30+00:00",
    )


def _build(order_source: bytes | None = None, fill_source: bytes | None = None):
    account_raw, account = _account()
    default_orders, default_fills = _sources()
    result = build_generic_live_dynamic_settled_cash_evidence(
        account_observation=account,
        raw_account_response=account_raw,
        raw_order_history_source=default_orders if order_source is None else order_source,
        raw_fill_history_source=default_fills if fill_source is None else fill_source,
        evaluated_at=EVALUATED,
        as_of_date=AS_OF,
    )
    return (
        result,
        account_raw,
        account,
        default_orders if order_source is None else order_source,
        default_fills if fill_source is None else fill_source,
    )


def test_same_day_sell_proceeds_are_not_falsely_classified_as_settled():
    result, account_raw, account, orders, fills = _build()
    assert result["broker_cash_usd"] == 200.0
    assert result["unsettled_proceeds_usd"] == 100.0
    assert result["settled_cash_usd"] == 100.0
    assert result["fail_closed"] is False
    assert result["history_complete"] is True
    assert result["execution_authority"] is False
    assert validate_generic_live_dynamic_settled_cash_evidence(
        result,
        account_observation=account,
        raw_account_response=account_raw,
        raw_order_history_source=orders,
        raw_fill_history_source=fills,
    ) == result


@pytest.mark.parametrize("missing", ["orders", "fills"])
def test_missing_raw_history_fails_closed(missing):
    orders, fills = _sources()
    with pytest.raises(GenericLiveDynamicSettledCashError, match="source bytes are required"):
        _build(b"" if missing == "orders" else orders, b"" if missing == "fills" else fills)


def test_filled_order_without_complete_fill_history_fails_closed():
    orders, _fills = _sources()
    _empty_orders, empty_fills = _sources(same_day_sell=False)
    with pytest.raises(GenericLiveDynamicSettledCashError, match="quantities do not reconcile"):
        _build(orders, empty_fills)


def test_complete_all_status_history_accepts_unfilled_orders_without_fill_fields():
    orders, fills = _sources(same_day_sell=False)
    source = json.loads(orders)
    source["rows"] = [
        {
            "id": "order-canceled-1", "symbol": "MU", "side": "buy",
            "status": "canceled", "filled_qty": "0",
            "filled_avg_price": None, "filled_at": None,
        }
    ]
    result, *_ = _build(_sealed_bytes(source), fills)
    assert result["settled_cash_usd"] == 200.0
    assert result["order_count"] == 1
    assert result["fill_count"] == 0


def test_incomplete_pagination_fails_closed():
    orders, fills = _sources()
    source = json.loads(orders)
    source["pagination_complete"] = False
    bad_orders = _sealed_bytes(source)
    with pytest.raises(GenericLiveDynamicSettledCashError, match="history is incomplete"):
        _build(bad_orders, fills)


def test_same_day_fill_cannot_be_relabelled_as_prior_settled_order():
    orders, fills = _sources()
    source = json.loads(orders)
    source["rows"][0]["filled_at"] = "2026-08-21T19:30:00+00:00"
    with pytest.raises(GenericLiveDynamicSettledCashError, match="fill time differs"):
        _build(_sealed_bytes(source), fills)


def test_stale_history_fails_closed():
    orders, fills = _sources()
    account_raw, account = _account()
    with pytest.raises(GenericLiveDynamicSettledCashError, match="120 seconds"):
        build_generic_live_dynamic_settled_cash_evidence(
            account_observation=account,
            raw_account_response=account_raw,
            raw_order_history_source=orders,
            raw_fill_history_source=fills,
            evaluated_at="2026-08-24T13:37:00+00:00",
            as_of_date=AS_OF,
        )


def test_resealed_claim_cannot_replace_raw_history():
    result, account_raw, account, orders, fills = _build()
    changed = copy.deepcopy(result)
    changed["settled_cash_usd"] = 200.0
    from core.generic_live_dynamic_settled_cash import _hash
    changed["content_hash"] = _hash(changed)
    with pytest.raises(GenericLiveDynamicSettledCashError, match="differs from raw sources"):
        validate_generic_live_dynamic_settled_cash_evidence(
            changed,
            account_observation=account,
            raw_account_response=account_raw,
            raw_order_history_source=orders,
            raw_fill_history_source=fills,
        )
