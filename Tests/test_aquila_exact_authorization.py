from Tests.fixtures.orion_registry import orion_registry
from dataclasses import dataclass
import hashlib
import json

import pandas as pd
import pytest

from core.aquila_monthly import build_aquila_source
from core.portfolio_operating_model import content_hash
from scripts.authorize_exact_execution_plan import (
    _apply_aquila_quantity_authority, _bind_quantity_demand_owners,
)


@dataclass
class Request:
    targets: pd.DataFrame
    total_equity: float = 10000.0
    target_cash_weight: float = .05


def case(tmp_path):
    book = {
        "account_id_hash": "a" * 64, "opening_contract_hash": "b" * 64,
        "reconciliation": {"status": "PASS"},
        "positions": [
            {"symbol": "AAPL", "sleeve_id": "caerus_aquila", "quantity": 10},
            {"symbol": "AAPL", "sleeve_id": "caerus_orion", "quantity": 10},
        ],
    }
    book["content_hash"] = content_hash(book)
    path = tmp_path / "ownership.json"
    path.write_text(json.dumps(book))
    source = build_aquila_source(
        trade_date="2026-09-08", previous_session="2026-09-04",
        generated_at="2026-09-08T11:00:00Z", account_equity=10000,
        marks={"AAPL": 100}, marks_as_of="2026-09-04T20:00:00Z",
        ownership={"trade_date": "2026-09-08", "reconciliation_status": "PASS",
                   "content_hash": book["content_hash"], "quantities": {"AAPL": 10},
                   "source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        monthly_state={"status": "RECONCILED", "formation_month": "2026-09",
                       "formation_session": "2026-08-31",
                       "quantities": {"AAPL": 10}, "formation_id": "initial",
                       "formation_hash": "c" * 64, "monthly_plan_sha256": "d" * 64},
    )
    allocation = {
        "trade_date": "2026-09-08", "quantity_contracts": {"caerus_aquila": source["quantity_contract"]},
        "targets": [
            {"symbol": "AAPL", "target_weight": .1, "sleeve_contributions": [
                {"sleeve_id": "caerus_aquila", "sleeve_internal_weight": 1}]},
            {"symbol": "MSFT", "target_weight": .85, "sleeve_contributions": [
                {"sleeve_id": "caerus_orion", "sleeve_internal_weight": 1}]},
        ],
        "sleeve_allocations": [{"sleeve_id": s, "decision_id": s, "decision_hash": "e" * 64}
                               for s in ("caerus_aquila", "caerus_orion")],
    }
    request = Request(pd.DataFrame([{"ticker": r["symbol"], "target_weight": r["target_weight"]}
                                    for r in allocation["targets"]]))
    kwargs = dict(request=request, allocation=allocation, account_hash="a" * 64,
                  broker_positions=[{"symbol": "AAPL", "quantity": 20}], repo_root=tmp_path)
    return kwargs


@pytest.mark.parametrize("aapl_price,orion_quantity", [(150, 80), (200, 75)])
def test_fresh_prices_preserve_aquila_shares_and_orion_exit_ownership(tmp_path, aapl_price, orion_quantity):
    args = case(tmp_path)
    request, evidence = _apply_aquila_quantity_authority(**args, prices={"AAPL": aapl_price, "MSFT": 100})
    assert evidence["desired_quantities"]["AAPL"] == {"caerus_aquila": 10}
    assert evidence["desired_quantities"]["MSFT"] == {"caerus_orion": orion_quantity}
    assert evidence["signed_sleeve_demands"]["AAPL"] == {"caerus_aquila": 0, "caerus_orion": -10}
    assert request.targets.target_weight.sum() == pytest.approx(.95)
    rows = [{"symbol": "AAPL", "side": "SELL", "quantity": 10}]
    _bind_quantity_demand_owners(rows, evidence, args["allocation"])
    assert rows[0]["sleeve_contributions"][0]["sleeve_id"] == "caerus_orion"
    assert rows[0]["sleeve_contributions"][0]["allocation_fraction"] == 1


def test_changed_broker_book_blocks_before_sizing(tmp_path):
    args = case(tmp_path)
    args["broker_positions"][0]["quantity"] = 21
    with pytest.raises(RuntimeError, match="fresh broker positions differ"):
        _apply_aquila_quantity_authority(**args, prices={"AAPL": 100, "MSFT": 100})


def test_wrong_account_blocks_before_sizing(tmp_path):
    args = case(tmp_path)
    args["account_hash"] = "f" * 64
    with pytest.raises(RuntimeError, match="prospective account ownership"):
        _apply_aquila_quantity_authority(**args, prices={"AAPL": 100, "MSFT": 100})


def test_partial_fill_uses_only_selling_owner(tmp_path):
    args = case(tmp_path)
    _, evidence = _apply_aquila_quantity_authority(**args, prices={"AAPL": 100, "MSFT": 100})
    rows = [{"symbol": "AAPL", "side": "SELL", "quantity": 3}]
    _bind_quantity_demand_owners(rows, evidence, args["allocation"])
    assert [r["sleeve_id"] for r in rows[0]["sleeve_contributions"]] == ["caerus_orion"]
    rows[0]["quantity"] = 11
    with pytest.raises(RuntimeError, match="exceeds governed sleeve quantity demand"):
        _bind_quantity_demand_owners(rows, evidence, args["allocation"])


def test_multi_sleeve_plan_preserves_committed_account_risk_owner(orion_registry, monkeypatch):
    from types import SimpleNamespace
    import core.sleeve_control_plane as control
    from authority.exact_plan import exact_execution_plan_from_dict
    from authority.contracts import AuthorityContractError
    from Tests.test_exact_execution_choice2 import _plan, _rebuild_exact
    base = _plan(no_trade=True).to_dict()
    monkeypatch.setattr(control, "load_sleeve_control_registry", lambda: SimpleNamespace(
        paper_capital_authority="caerus_orion",
        paper_allocation_policy={"sleeve_risk_budgets": {"caerus_orion": .45, "caerus_aquila": .5}}))
    allocations = [{"sleeve_id": name, "weight": weight, "capital_eligible": True}
                   for name, weight in (("caerus_orion", .45), ("caerus_aquila", .5))]
    constraints = {**base["constraints"], "paper_regime_owner": "caerus_orion"}
    multi = _rebuild_exact(base, strategy_id="caerus_paper_portfolio", sleeve_allocations=allocations,
                           constraints=constraints)
    validated = exact_execution_plan_from_dict(multi.to_dict())
    assert validated.regime_state == multi.regime_state == base["regime_state"]
    with pytest.raises(AuthorityContractError, match="differs from governed authority"):
        _rebuild_exact(base, strategy_id="caerus_paper_portfolio", sleeve_allocations=allocations,
                       constraints={**constraints, "paper_regime_owner": "caerus_aquila"})
    forged = _rebuild_exact(base, strategy_id="caerus_paper_portfolio", sleeve_allocations=allocations,
                           constraints={**constraints, "paper_regime_owner": "caerus_aquila"},
                           validate_current_allocator=False)
    with pytest.raises(AuthorityContractError, match="identity scope mismatch"):
        exact_execution_plan_from_dict(forged.to_dict())


def test_risk_observation_survives_later_quantity_planning_failure(orion_registry, tmp_path, monkeypatch):
    import scripts.authorize_exact_execution_plan as authorizer
    from Tests.test_regime_state_authorizer import _authorized_plan
    from Tests.test_exact_execution_choice2 import TrackingPaperBroker, _env
    from core.regime_state_store import load_regime_history
    plan, path = _authorized_plan(tmp_path, risk_controls={'regime_authority': {
        'observed_state': 'RISK_OFF', 'confidence': .99, 'acute_risk': True,
        'market_state_id': 'market:acute'}})
    def rejected(**kwargs):
        raise RuntimeError('quantity intervention required')
    monkeypatch.setattr(authorizer, '_apply_aquila_quantity_authority', rejected)
    root = tmp_path/'risk'
    broker = TrackingPaperBroker()
    for _ in range(2):
        with pytest.raises(RuntimeError, match='quantity intervention'):
            authorizer.authorize_exact_execution_plan(plan=plan, broker=broker, env=_env(),
                run_id='risk-before-quantity', plan_path=path, created_at='2026-08-12T13:35:01Z', regime_state_root=root)
    events = list(root.glob('PAPER/*/*/events/*.json'))
    assert len(events) == 1
    assert json.loads(events[0].read_text())['acute_risk'] is True
    assert broker.submit_calls == 0
