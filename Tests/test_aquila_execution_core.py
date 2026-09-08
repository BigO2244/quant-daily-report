from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest

from authority.contracts import build_evidence_package, build_decision_package, build_risk_package
from authority.pipeline import execution_package_from_risk
from execution.core import ExecutionRequest, compute_transition_trades, live_pilot_execution_config
from scripts.authorize_exact_execution_plan import _apply_aquila_quantity_authority
from Tests.test_aquila_exact_authorization import case


def request_fixture(tmp_path):
    args = case(tmp_path)
    request, qa = _apply_aquila_quantity_authority(**args, prices={"AAPL": 150, "MSFT": 100})
    contract = qa["quantity_contract"]
    rows = deepcopy(args["allocation"]["targets"])
    for row in rows:
        for c in row["sleeve_contributions"]:
            c["target_weight"] = row["target_weight"]
            if c["sleeve_id"] == "caerus_aquila":
                c.update(quantity_contract_hash=contract["content_hash"], sizing_mode="FIXED_QUANTITY", target_quantity=10)
    registry = json.loads((Path(__file__).resolve().parents[1] / "config/research/strategy_registry.json").read_text())
    policy = registry["sleeve_control_plane"]["paper_allocation_policy"]["account_target_attainment_policy"]
    evidence = build_evidence_package(package_id="e", trade_date="2026-09-08", source_refs=["fixture"], observations=[])
    decision = build_decision_package(package_id="d", trade_date="2026-09-08", evidence=evidence, target_rows=rows, source_refs=["fixture"], target_cash_weight=.05)
    risk = build_risk_package(package_id="r", decision=decision, approved_target_rows=rows,
                              constraints={"target_attainment_policy": policy}, source_refs=["fixture"], approved_cash_weight=.05)
    package = execution_package_from_risk(risk).to_dict()
    qa["approved_execution_package_hash"] = package["content_hash"]
    result = ExecutionRequest(holdings=pd.DataFrame([{"ticker": "AAPL", "shares": 20}]), targets=request.targets,
                               prices=pd.Series({"AAPL": 150, "MSFT": 100}), total_equity=10000, starting_cash=7000,
                               target_cash_weight=.05, planning_account={"cash": 7000}, run_id="fixture",
                               price_basis="timestamped_alpaca_latest_trade_at_authorization",
                               approved_execution_package=package, quantity_authority=qa)
    config = replace(live_pilot_execution_config(approved_cap_usd=10000, allow_fractional=True,
                      allow_fractional_sells=True, max_orders=50, min_trade_usd=1, ledger_enabled=False), mode="paper")
    return result, config


def test_real_approved_package_does_not_override_frozen_aquila_shares(tmp_path):
    request, config = request_fixture(tmp_path)
    original = deepcopy(request.approved_execution_package)
    trades, meta = compute_transition_trades(request=request, config=config)
    by_name = {row.ticker: row for row in trades.itertuples()}
    assert by_name["AAPL"].side == "SELL"
    assert by_name["AAPL"].shares == 10  # Orion exits; Aquila retains its ten shares.
    assert by_name["MSFT"].shares == 80
    assert request.approved_execution_package == original
    assert meta["authority_package_hash"] == original["content_hash"]


def test_declared_quantity_cannot_override_independent_package_derivation(tmp_path):
    request, config = request_fixture(tmp_path)
    qa = deepcopy(request.quantity_authority)
    qa["desired_quantities"]["AAPL"]["caerus_aquila"] = 11
    with pytest.raises(ValueError, match="independently derived"):
        compute_transition_trades(request=replace(request, quantity_authority=qa), config=config)


def test_quantity_intent_requires_original_approved_package(tmp_path):
    request, config = request_fixture(tmp_path)
    with pytest.raises(ValueError, match="requires approved"):
        compute_transition_trades(request=replace(request, approved_execution_package=None), config=config)
    qa = deepcopy(request.quantity_authority)
    qa["approved_execution_package_hash"] = "f" * 64
    with pytest.raises(ValueError, match="package hash"):
        compute_transition_trades(request=replace(request, quantity_authority=qa), config=config)


def test_quantity_path_remains_paper_only(tmp_path):
    request, config = request_fixture(tmp_path)
    with pytest.raises(ValueError, match="PAPER"):
        compute_transition_trades(request=request, config=replace(config, mode="live"))
