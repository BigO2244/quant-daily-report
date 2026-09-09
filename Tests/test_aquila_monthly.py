from copy import deepcopy

import pytest

from core.aquila_monthly import AquilaContractError, build_aquila_source, validate_quantity_contract
from core.portfolio_operating_model import content_hash


def inputs():
    return dict(trade_date="2026-09-08", previous_session="2026-09-04",
                generated_at="2026-09-08T11:00:00+00:00", account_equity=10000, marks_as_of="2026-09-04T20:00:00+00:00",
                marks={f"S{i}": 100 for i in range(11)},
                ownership=dict(trade_date="2026-09-08", reconciliation_status="PASS", content_hash="a"*64, quantities={}, source_path="immutable/ownership.json", source_sha256="d"*64),
                ranking=dict(accepted=True, formation_session="2026-09-04", formation_id="formation:sep",
                             captured_at="2026-09-04T21:00:00+00:00", content_hash="b"*64,
                             issuers=[dict(issuer_id=f"issuer{i}", execution_symbol=f"S{i}", market_cap=1000-i) for i in range(11)]))


def test_first_formation_uses_ten_equal_names_including_rank_one():
    source = build_aquila_source(**inputs())
    c = source["quantity_contract"]
    assert c["action"] == "MONTHLY_REBALANCE"
    assert c["sizing_mode"] == "REBALANCE_WEIGHT"
    assert set(c["target_quantities"]) == {f"S{i}" for i in range(10)}
    assert set(c["target_quantities"].values()) == {5}
    assert set(source["target_weights"].values()) == {.1}


def hold_inputs():
    kwargs = inputs()
    kwargs["monthly_state"] = dict(status="RECONCILED", formation_month="2026-09",
                                    formation_id="formation:sep", formation_hash="b"*64, formation_session="2026-08-31",
                                    monthly_plan_sha256="c"*64, quantities={f"S{i}": 5 for i in range(10)})
    kwargs["ownership"]["quantities"] = kwargs["monthly_state"]["quantities"].copy()
    kwargs["ranking"] = None
    return kwargs


def test_hold_fixes_quantities_despite_prices_and_account_nav():
    kwargs = hold_inputs()
    kwargs["marks"]["S0"] = 200
    kwargs["account_equity"] = 12000
    source = build_aquila_source(**kwargs)
    assert source["quantity_contract"]["sizing_mode"] == "FIXED_QUANTITY"
    assert source["quantity_contract"]["target_quantities"] == kwargs["monthly_state"]["quantities"]
    assert source["target_weights"]["S0"] == pytest.approx(2/11)


def test_first_session_of_new_month_resets_equal_weights():
    kwargs = hold_inputs()
    kwargs.update(trade_date="2026-10-01", previous_session="2026-09-30", generated_at="2026-10-01T11:00:00+00:00", marks_as_of="2026-09-30T20:00:00+00:00")
    kwargs["ownership"]["trade_date"] = "2026-10-01"
    kwargs["ranking"] = inputs()["ranking"]
    kwargs["ranking"].update(formation_session="2026-09-30", captured_at="2026-09-30T21:00:00+00:00")
    assert build_aquila_source(**kwargs)["quantity_contract"]["action"] == "MONTHLY_REBALANCE"


@pytest.mark.parametrize("mutation", ["stale_ownership", "unreconciled", "stale_rank", "future_rank", "duplicate_issuer", "unordered"])
def test_rejects_invalid_causal_inputs(mutation):
    kwargs = inputs()
    if mutation == "stale_ownership": kwargs["ownership"]["trade_date"] = "2026-09-04"
    if mutation == "unreconciled": kwargs["ownership"]["reconciliation_status"] = "FAIL"
    if mutation == "stale_rank": kwargs["ranking"]["formation_session"] = "2026-09-03"
    if mutation == "future_rank": kwargs["ranking"]["captured_at"] = "2026-09-08T12:00:00+00:00"
    if mutation == "duplicate_issuer": kwargs["ranking"]["issuers"][1]["issuer_id"] = "issuer0"
    if mutation == "unordered": kwargs["ranking"]["issuers"].reverse()
    with pytest.raises(AquilaContractError): build_aquila_source(**kwargs)


def test_hold_rejects_missing_fill_plan_or_changed_logical_ownership():
    kwargs = hold_inputs()
    kwargs["ownership"]["quantities"]["S0"] = 4
    with pytest.raises(AquilaContractError, match="ownership"):
        build_aquila_source(**kwargs)
    kwargs = hold_inputs()
    kwargs["monthly_state"]["monthly_plan_sha256"] = None
    with pytest.raises(AquilaContractError, match="plan hash"):
        build_aquila_source(**kwargs)


def test_contract_mutation_cannot_change_daily_hold():
    c = deepcopy(build_aquila_source(**hold_inputs())["quantity_contract"])
    c["target_quantities"]["S0"] = 10
    with pytest.raises(AquilaContractError, match="hash"):
        validate_quantity_contract(c, trade_date="2026-09-08")


def test_zero_logical_rows_are_not_orders_or_missing_target_failures():
    kwargs = hold_inputs()
    kwargs["monthly_state"]["quantities"]["S0"] = 0
    kwargs["ownership"]["quantities"]["S0"] = 0
    source = build_aquila_source(**kwargs)
    assert "S0" not in source["quantity_contract"]["target_quantities"]
    assert "S0" not in source["target_weights"]


def test_empty_reconciled_book_cannot_silently_reactivate_midmonth():
    kwargs = hold_inputs()
    kwargs["monthly_state"]["quantities"] = {}
    kwargs["ownership"]["quantities"] = {}
    with pytest.raises(AquilaContractError):
        build_aquila_source(**kwargs)


def test_owner_excluded_azo_cannot_enter_formation_even_if_ranked_first():
    kwargs = current_inputs()
    kwargs['ranking']['issuers'][0]['execution_symbol'] = 'AZO'
    kwargs['marks']['AZO'] = 100
    with pytest.raises(AquilaContractError, match='owner-excluded'):
        build_aquila_source(**kwargs)


def test_owner_excluded_azo_cannot_enter_quantity_contract():
    contract = build_aquila_source(**current_inputs())['quantity_contract']
    contract['target_quantities']['AZO'] = contract['target_quantities'].pop('S0')
    contract['marks']['AZO'] = contract['marks'].pop('S0')
    contract['issuer_map']['AZO'] = contract['issuer_map'].pop('S0')
    contract['content_hash'] = content_hash({k: v for k, v in contract.items() if k != 'content_hash'})
    with pytest.raises(AquilaContractError, match='owner-excluded'):
        validate_quantity_contract(contract, trade_date='2026-09-09')


def current_inputs():
    kwargs = inputs()
    kwargs.update(trade_date='2026-09-09', previous_session='2026-09-08', generated_at='2026-09-09T09:00:00+00:00', marks_as_of='2026-09-08T20:00:00+00:00')
    kwargs['ownership']['trade_date'] = '2026-09-09'
    kwargs['ranking'].update(formation_session='2026-09-08', captured_at='2026-09-08T21:00:00+00:00')
    return kwargs


def test_pre_effective_date_azo_replay_remains_reproducible():
    kwargs = inputs()
    kwargs['ranking']['issuers'][0]['execution_symbol'] = 'AZO'
    kwargs['marks']['AZO'] = 100
    source = build_aquila_source(**kwargs)
    assert source['quantity_contract']['target_quantities']['AZO'] == 5
    validate_quantity_contract(source['quantity_contract'], trade_date='2026-09-08')


@pytest.mark.parametrize('field,value', [('effective_date', 'not-a-date'), ('owner', ''), ('reason', None), ('symbols', ['AZO', 'AZO']), ('issuer_ids', ['bad'])])
def test_malformed_owner_exclusion_policy_fails_closed(monkeypatch, field, value):
    import json
    import core.aquila_monthly as module
    registry = json.loads((module.Path(__file__).resolve().parents[1] / 'config/research/strategy_registry.json').read_text())
    registry['sleeve_control_plane']['strategy_overrides']['caerus_aquila']['owner_exclusion_policy'][field] = value
    monkeypatch.setattr(module.Path, 'read_text', lambda self, *a, **kw: json.dumps(registry))
    with pytest.raises(AquilaContractError):
        module.owner_exclusion_policy()


def test_renamed_excluded_issuer_cannot_pass_post_effective_hold():
    kwargs = hold_inputs()
    kwargs.update(trade_date='2026-09-09', previous_session='2026-09-08', generated_at='2026-09-09T09:00:00+00:00', marks_as_of='2026-09-08T20:00:00+00:00')
    kwargs['ownership']['trade_date'] = '2026-09-09'
    kwargs['monthly_state']['quantities']['RENAMED'] = kwargs['monthly_state']['quantities'].pop('S0')
    kwargs['ownership']['quantities'] = kwargs['monthly_state']['quantities'].copy()
    kwargs['marks']['RENAMED'] = 100
    kwargs['holding_issuer_map'] = {s: 'issuer-' + s for s in kwargs['ownership']['quantities']}
    kwargs['holding_issuer_map']['RENAMED'] = '0000866787'
    with pytest.raises(AquilaContractError, match='owner-excluded'):
        build_aquila_source(**kwargs)


def test_quantity_validator_rejects_excluded_issuer_even_with_other_symbol():
    contract = build_aquila_source(**current_inputs())['quantity_contract']
    contract['issuer_map']['S0'] = '0000866787'
    contract['content_hash'] = content_hash({k: v for k, v in contract.items() if k != 'content_hash'})
    with pytest.raises(AquilaContractError, match='owner-excluded'):
        validate_quantity_contract(contract, trade_date='2026-09-09')
