from Tests.fixtures.orion_registry import orion_registry
from copy import deepcopy
import json
from pathlib import Path

import pytest

from authority.contracts import build_evidence_package, build_decision_package, build_risk_package
from authority.pipeline import execution_package_from_risk
from core.aquila_monthly import SCHEMA
from core.portfolio_operating_model import content_hash
from core.lane_target_attainment import build_lane_target_attainment
from Tests.test_exact_execution_choice2 import _plan, _rebuild_exact, _handoff


def fixture():
    date = "2026-08-12"
    contract = dict(schema_version=SCHEMA, trade_date=date, generated_at="2026-08-12T11:00:00+00:00",
        previous_session="2026-08-11", marks_as_of="2026-08-11T20:00:00+00:00", formation_session="2026-07-31",
        action="HOLD_NO_REBALANCE", sizing_mode="FIXED_QUANTITY", account_equity=10000,
        marks={"AAPL": 100}, target_quantities={"AAPL": 10}, ownership_snapshot_hash="a"*64,
        ownership_snapshot_path="immutable/book.json", ownership_snapshot_sha256="b"*64,
        formation_id="aug", formation_hash="c"*64, monthly_plan_sha256="d"*64)
    contract["content_hash"] = content_hash(contract)
    rows = [dict(symbol="AAPL", target_weight=.1, sleeve_contributions=[dict(sleeve_id="caerus_aquila", target_weight=.1,
        sleeve_internal_weight=1, sizing_mode="FIXED_QUANTITY", quantity_contract_hash=contract["content_hash"])]),
        dict(symbol="MSFT", target_weight=.85, sleeve_contributions=[dict(sleeve_id="caerus_orion", target_weight=.85, sleeve_internal_weight=1)])]
    registry = json.loads((Path(__file__).resolve().parents[1] / "config/research/strategy_registry.json").read_text())
    policy = registry["sleeve_control_plane"]["paper_allocation_policy"]["account_target_attainment_policy"]
    ev = build_evidence_package(package_id="e", trade_date=date, source_refs=["source"], observations=[])
    dec = build_decision_package(package_id="d", trade_date=date, evidence=ev, target_rows=rows, source_refs=["source"], target_cash_weight=.05)
    risk = build_risk_package(package_id="r", decision=dec, approved_target_rows=rows,
        constraints={"target_attainment_policy": policy}, source_refs=["source"], approved_cash_weight=.05)
    package = execution_package_from_risk(risk).to_dict()
    qa = dict(quantity_contract=contract, approved_execution_package_hash=package["content_hash"],
        desired_quantities={"AAPL": {"caerus_aquila": 10}, "MSFT": {"caerus_orion": 65}},
        signed_sleeve_demands={"AAPL": {"caerus_aquila": 0, "caerus_orion": -10}, "MSFT": {"caerus_aquila": 0, "caerus_orion": 65}})
    base = _plan().to_dict()
    owner = [dict(sleeve_id="caerus_orion", allocation_fraction=1, decision_id="d", decision_hash="e"*64)]
    exact = _rebuild_exact(base, portfolio_nav=10000, starting_cash=4000,
        starting_positions=[dict(symbol="AAPL", quantity=20)],
        sell_orders=[dict(symbol="AAPL", side="SELL", quantity=10, expected_price=300, notional=3000, sleeve_contributions=owner)],
        buy_orders=[dict(symbol="MSFT", side="BUY", quantity=65, expected_price=100, notional=6500, sleeve_contributions=owner)],
        expected_posttrade_positions=[dict(symbol="AAPL", quantity=10), dict(symbol="MSFT", quantity=65)], expected_posttrade_cash=500,
        constraints={**base["constraints"], "capital_cap_usd": 10000, "aquila_quantity_authority": qa})
    plan = {**_handoff(exact), "approved_execution_package": package, "allow_fractional": True}
    post = dict(account={"account_id_hash": exact.account_id_hash, "equity": 10000, "cash": 500},
        positions=[dict(symbol="AAPL", qty=10, market_value=3000), dict(symbol="MSFT", qty=65, market_value=6500)])
    return plan, post


def run(plan, post, **kwargs):
    return build_lane_target_attainment(plan=plan, post_snapshot=post, reconciliation=kwargs.get("reconciliation", {"status": "CLEAN"}),
        run_id="test", trade_date="2026-08-12", mode="paper", dry_run=False)


def test_fixed_aquila_shares_pass_despite_large_weight_drift(orion_registry, ):
    plan, post = fixture()
    result = run(plan, post)
    assert result["status"] == "OK_TARGET_ATTAINED"
    assert result["aquila_quantity_attainment"]["protected_aquila_shares_verified"]
    assert result["max_absolute_position_weight_drift"] == 0
    assert next(r for r in result["positions"] if r["symbol"] == "AAPL")["target_weight"] == .3
    assert plan["approved_execution_package"]["approved_target_rows"][0]["target_weight"] == .1


def test_quantity_mismatch_fails_even_with_small_weight_difference(orion_registry, ):
    plan, post = fixture()
    post["positions"][0]["qty"] = 9.999
    result = run(plan, post)
    assert result["status"] == "FAIL_TARGET_MISMATCH"


@pytest.mark.parametrize("mutation", ["account", "hash", "missing_authority", "package_hash"])
def test_quantity_attainment_rejects_unbound_authority(orion_registry, mutation):
    plan, post = fixture()
    if mutation == "account": post["account"]["account_id_hash"] = "f"*64
    if mutation == "hash": plan["exact_execution_plan_hash"] = "f"*64
    if mutation == "missing_authority": plan["exact_execution_plan"]["constraints"].pop("aquila_quantity_authority")
    if mutation == "package_hash": plan["approved_execution_package"]["content_hash"] = "f"*64
    assert run(plan, post)["status"] == "FAIL_QUANTITY_AUTHORITY_INVALID"


def test_quantity_attainment_preserves_cash_floor_and_reconciliation(orion_registry, ):
    plan, post = fixture()
    post["account"]["cash"] = 200
    assert run(plan, post)["status"] == "FAIL_TARGET_MISMATCH"
    plan, post = fixture()
    assert run(plan, post, reconciliation={"status": "FAILED_RECONCILIATION"})["status"] == "FAIL_EXECUTION_INCOMPLETE"


def test_reconciled_account_cannot_hide_sale_of_protected_aquila_shares(orion_registry, monkeypatch):
    plan, post = fixture()
    raw = deepcopy(plan["exact_execution_plan"])
    sells = deepcopy(raw["sell_orders"])
    sells[0]["sleeve_contributions"][0]["sleeve_id"] = "caerus_aquila"
    # Build the adversarial order under the real two-sleeve governance after
    # creating the historical risk event. Aquila must be eligible so the
    # independent ownership proof, rather than registry rejection, catches it.
    from core import sleeve_control_plane
    registry_path = Path(__file__).resolve().parents[1] / "config/research/strategy_registry.json"
    monkeypatch.setattr(sleeve_control_plane, "default_registry_path", lambda: registry_path)
    exact = _rebuild_exact(raw, sell_orders=sells, strategy_id="caerus_paper_portfolio",
        sleeve_allocations=[dict(sleeve_id="caerus_aquila", weight=.5, capital_eligible=True),
                            dict(sleeve_id="caerus_orion", weight=.45, capital_eligible=True)],
        constraints={**raw["constraints"], "paper_regime_owner": "caerus_orion"})
    plan.update(_handoff(exact))
    result = run(plan, post)
    assert result["status"] == "FAIL_QUANTITY_AUTHORITY_INVALID"
    assert "protected Aquila shares" in result["reason_code"]


@pytest.mark.parametrize("orion_quantity,expected", [(64, "OK_TARGET_ATTAINED"), (62, "FAIL_TARGET_MISMATCH")])
def test_orion_underfill_retains_existing_weight_tolerance(orion_registry, orion_quantity, expected):
    plan, post = fixture()
    raw = deepcopy(plan["exact_execution_plan"])
    buys = deepcopy(raw["buy_orders"])
    buys[0].update(quantity=orion_quantity, notional=orion_quantity*100)
    cash = 7000 - orion_quantity*100
    exact = _rebuild_exact(raw, buy_orders=buys, expected_posttrade_cash=cash,
        expected_posttrade_positions=[dict(symbol="AAPL", quantity=10), dict(symbol="MSFT", quantity=orion_quantity)])
    plan.update(_handoff(exact))
    post["positions"][1].update(qty=orion_quantity, market_value=orion_quantity*100)
    post["account"]["cash"] = cash
    assert run(plan, post)["status"] == expected
