from __future__ import annotations

import copy
import hashlib
import json

import pytest

from authority.lane_exact_plan import artifact_content_hash, canonical_json
from core.generic_live_dynamic_account import build_generic_live_dynamic_account_observation
from core.generic_live_dynamic_capital import (
    FEE_SCHEDULE_SCHEMA,
    GenericLiveDynamicCapitalError,
    build_generic_live_dynamic_capital_proof,
    build_worst_case_fee_proof,
    derive_dynamic_capital_limits,
    validate_generic_live_dynamic_capital_proof,
)
from core.generic_live_dynamic_owner_decision import build_generic_live_dynamic_owner_decision
from core.generic_live_dynamic_settled_cash import (
    FILL_SOURCE_SCHEMA,
    ORDER_SOURCE_SCHEMA,
    build_generic_live_dynamic_settled_cash_evidence,
)
import Tests.test_generic_live_v1_activation as activation_fixture


def _hash(payload):
    body = copy.deepcopy(dict(payload)); body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _raw(value):
    return canonical_json(value).encode()


def _fixture(*, order_rows=(), fill_rows=()):
    account_id = "synthetic-dynamic-account"
    account_hash = hashlib.sha256(account_id.encode()).hexdigest()
    old_hash = activation_fixture.OBSERVATION["account_id_hash"]
    activation_fixture.OBSERVATION["account_id_hash"] = account_hash
    try:
        plan = activation_fixture._plan(activation_fixture._decision())
    finally:
        activation_fixture.OBSERVATION["account_id_hash"] = old_hash
    capital_policy = {
        "policy_id": "generic-live-v1-dynamic-capital",
        "capital_basis": "FULL_ACCOUNT_EQUITY", "capital_ceiling_usd": None,
    }
    capital_hash = hashlib.sha256(canonical_json(capital_policy).encode()).hexdigest()
    plan["capital_policy_hash"] = capital_hash
    plan["source_hashes"]["capital_policy"] = capital_hash
    plan["content_hash"] = artifact_content_hash(plan)
    raw_account = _raw({
        "id": account_id, "status": "ACTIVE", "trading_blocked": False,
        "account_blocked": False, "equity": plan["starting_equity"],
        "cash": plan["starting_cash"],
        "long_market_value": plan["starting_equity"] - plan["starting_cash"],
        "short_market_value": 0, "pending_transfer_in": 0, "pending_transfer_out": 0,
    })
    account = build_generic_live_dynamic_account_observation(
        raw_account_response=raw_account, observed_at="2026-08-25T13:30:20+00:00",
    )
    common = {
        "captured_at": "2026-08-25T13:30:30+00:00",
        "pagination_complete": True, "next_page_token": None,
        "pages_retrieved": 1,
    }
    order_source = {
        **common, "schema_version": ORDER_SOURCE_SCHEMA, "endpoint": "GET /v2/orders",
        "query": {"status": "all", "after": "2026-08-21T00:00:00-04:00", "until": common["captured_at"], "direction": "asc"},
        "rows": list(order_rows),
    }
    order_source["content_hash"] = _hash(order_source)
    fill_source = {
        **common, "schema_version": FILL_SOURCE_SCHEMA,
        "endpoint": "GET /v2/account/activities/FILL",
        "query": {"activity_type": "FILL", "after": "2026-08-21T00:00:00-04:00", "until": common["captured_at"], "direction": "asc"},
        "rows": list(fill_rows),
    }
    fill_source["content_hash"] = _hash(fill_source)
    raw_orders, raw_fills = _raw(order_source), _raw(fill_source)
    settled = build_generic_live_dynamic_settled_cash_evidence(
        account_observation=account, raw_account_response=raw_account,
        raw_order_history_source=raw_orders, raw_fill_history_source=raw_fills,
        evaluated_at="2026-08-25T13:31:00+00:00", as_of_date="2026-08-25",
    )
    owner = build_generic_live_dynamic_owner_decision(
        decided_at="2026-08-19T13:26:24+00:00", effective_session="2026-08-25",
        expires_at="2026-08-26T20:00:00+00:00",
    )
    schedule = {
        "schema_version": FEE_SCHEDULE_SCHEMA, "provider": "ALPACA_LIVE",
        "effective_from": "2026-08-01", "effective_through": "2026-08-31",
        "base_fee_usd": 0.0, "buy_notional_bps": 0.0,
        "sell_notional_bps": 1.0, "sell_per_share_usd": 0.0002,
        "minimum_fee_usd": 0.01, "maximum_fee_usd": 5.0,
        "source_document_hash": "d" * 64,
    }
    schedule["content_hash"] = _hash(schedule)
    fee = build_worst_case_fee_proof(
        exact_plan=plan, governed_fee_schedule=schedule,
        trusted_fee_schedule_hash=schedule["content_hash"],
    )
    return plan, owner, account, raw_account, settled, raw_orders, raw_fills, schedule, fee, capital_policy


def _build(**changes):
    plan, owner, account, raw_account, settled, raw_orders, raw_fills, schedule, fee, capital_policy = _fixture()
    args = {
        "exact_plan": plan, "owner_decision": owner,
        "trusted_owner_decision_hash": owner["content_hash"],
        "account_observation": account, "raw_account_response": raw_account,
        "settled_cash_evidence": settled, "raw_order_history_source": raw_orders,
        "raw_fill_history_source": raw_fills, "fee_proof": fee,
        "governed_fee_schedule": schedule,
        "trusted_fee_schedule_hash": schedule["content_hash"],
        "governed_capital_policy": capital_policy,
        "evaluated_at": "2026-08-25T13:31:00+00:00",
    }
    args.update(changes)
    return build_generic_live_dynamic_capital_proof(**args), args


def test_dynamic_capital_uses_settled_cash_and_governed_fee_not_buying_power():
    proof, args = _build()
    assert proof["nominal_capital_ceiling_usd"] is None
    assert proof["dynamic_gross_cap_usd"] == pytest.approx(437.855)
    assert proof["fee_proof_hash"] == args["fee_proof"]["content_hash"]
    assert proof["settled_cash_evidence_hash"] == args["settled_cash_evidence"]["content_hash"]
    assert proof["buying_power_used"] is False
    assert validate_generic_live_dynamic_capital_proof(proof, **args) == proof


def test_same_day_sell_proceeds_are_subtracted_from_settled_cash():
    order = {
        "id": "sold-1", "symbol": "ABC", "side": "sell", "status": "filled",
        "filled_qty": "1", "filled_avg_price": "100", "filled_at": "2026-08-25T13:00:00Z",
    }
    fill = {
        "id": "fill-1", "order_id": "sold-1", "symbol": "ABC", "side": "sell",
        "qty": "1", "price": "100", "transaction_time": "2026-08-25T13:00:00Z",
    }
    _, _, _, _, settled, *_ = _fixture(order_rows=[order], fill_rows=[fill])
    assert settled["unsettled_proceeds_usd"] == pytest.approx(100.0)
    assert settled["settled_cash_usd"] == pytest.approx(0.0)


def test_untrusted_fee_or_owner_and_resealed_false_pass_fail_closed():
    proof, args = _build()
    with pytest.raises(GenericLiveDynamicCapitalError, match="independently pinned"):
        build_generic_live_dynamic_capital_proof(**{**args, "trusted_fee_schedule_hash": "f" * 64})
    with pytest.raises(Exception, match="trusted authority"):
        build_generic_live_dynamic_capital_proof(**{**args, "trusted_owner_decision_hash": "e" * 64})
    changed = copy.deepcopy(proof); changed["gross_limit_pass"] = False; changed["content_hash"] = _hash(changed)
    with pytest.raises(GenericLiveDynamicCapitalError, match="differs"):
        validate_generic_live_dynamic_capital_proof(changed, **args)


def test_stale_fixed_capital_policy_plan_is_rejected():
    _, args = _build()
    plan = copy.deepcopy(args["exact_plan"])
    stale = {
        "policy_id": "generic-live-v1-capital", "capital_basis": "FULL_ACCOUNT_EQUITY",
        "capital_ceiling_usd": 460.0,
    }
    stale_hash = hashlib.sha256(canonical_json(stale).encode()).hexdigest()
    plan["capital_policy_hash"] = stale_hash
    plan["source_hashes"]["capital_policy"] = stale_hash
    plan["content_hash"] = artifact_content_hash(plan)
    with pytest.raises(GenericLiveDynamicCapitalError, match="stale fixed-capital"):
        build_generic_live_dynamic_capital_proof(**{**args, "exact_plan": plan})


def test_deposit_and_withdrawal_adjust_dynamic_limits_without_margin():
    base = derive_dynamic_capital_limits(equity_usd=460.9, settled_cash_usd=460.9)
    deposit = derive_dynamic_capital_limits(equity_usd=960.9, settled_cash_usd=960.9)
    withdrawal = derive_dynamic_capital_limits(equity_usd=300.0, settled_cash_usd=300.0)
    assert deposit["dynamic_gross_cap_usd"] > base["dynamic_gross_cap_usd"]
    assert withdrawal["dynamic_gross_cap_usd"] < base["dynamic_gross_cap_usd"]


def test_fractional_or_subminimum_order_is_rejected_before_authority():
    _, args = _build()
    plan = copy.deepcopy(args["exact_plan"])
    orders = [*plan["sell_orders"], *plan["buy_orders"]]
    if not orders:
        pytest.skip("fixture needs an order")
    orders[0]["quantity"] = 0.5
    # The exact-plan validator rejects the tamper before dynamic proof authority.
    with pytest.raises(GenericLiveDynamicCapitalError, match="exact plan is invalid"):
        build_generic_live_dynamic_capital_proof(**{**args, "exact_plan": plan})
