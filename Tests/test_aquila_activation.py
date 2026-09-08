"""Active-registry Aquila+Orion chain, with an entirely simulated PAPER broker."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from core.aquila_monthly import build_aquila_source
from core.portfolio_operating_model import content_hash
from core.sleeve_control_plane import load_sleeve_control_registry
from authority.exact_plan import exact_execution_plan_from_dict
from scripts.authorize_exact_execution_plan import authorize_exact_execution_plan
from Tests.test_exact_execution_choice2 import TrackingPaperBroker, _env, _finalize_direct_authorization, TEST_NOW_ET
from Tests.test_live_pilot_build_plan_from_precompute import _bundle, _build, _orion_shadow

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("hold", [False, True])
def test_active_aquila_registry_monthly_chain(tmp_path, monkeypatch, hold):
    registry = load_sleeve_control_registry()
    assert set(registry.paper_allocation_policy['sleeve_risk_budgets']) == {'caerus_aquila', 'caerus_orion'}
    for name in ('config/research/strategy_registry.json', 'research_registry/sleeves/manifest.json'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    date = '2026-08-12'
    symbols = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'AVGO', 'TSLA', 'WMT', 'LLY']
    book = {'account_id_hash': hashlib.sha256(b'paper-account').hexdigest(),
            'opening_contract_hash': 'b'*64, 'reconciliation': {'status': 'PASS'},
            'positions': [{'symbol': 'OLD', 'sleeve_id': 'caerus_orion', 'quantity': 1}]}
    if hold:
        book['positions'] = [{'symbol':s,'sleeve_id':'caerus_aquila','quantity':1} for s in symbols]
    book['content_hash'] = content_hash(book)
    book_path = tmp_path / 'outputs/ledger/paper/ownership_latest.json'
    book_path.parent.mkdir(parents=True, exist_ok=True)
    book_path.write_text(json.dumps(book))
    ranking = {'accepted': True, 'formation_session': '2026-08-11', 'formation_id': 'aug',
               'captured_at': '2026-08-12T10:00:00Z',
               'issuers': [{'issuer_id': str(i), 'execution_symbol': s, 'market_cap': 100-i}
                           for i,s in enumerate(symbols+['XOM'])]}
    ranking['content_hash'] = content_hash(ranking)
    source = build_aquila_source(trade_date=date, previous_session='2026-08-11', generated_at='2026-08-12T11:00:00Z',
        account_equity=800 if hold else 1000, marks={s:50 for s in symbols}, marks_as_of='2026-08-11T20:00:00Z',
        ownership={'trade_date':date,'reconciliation_status':'PASS','content_hash':book['content_hash'],
                   'quantities':{s:1 for s in symbols} if hold else {},'source_path':str(book_path),'source_sha256':hashlib.sha256(book_path.read_bytes()).hexdigest()}, ranking=None if hold else ranking,
        monthly_state={'status':'RECONCILED','formation_month':'2026-08','formation_session':'2026-07-31',
            'formation_id':'aug','formation_hash':'c'*64,'monthly_plan_sha256':'d'*64,
            'quantities':{s:1 for s in symbols}} if hold else None)
    source_path = tmp_path / f'outputs/shadow_candidates/{date}/caerus_aquila.json'
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(source))
    payload = _bundle(tmp_path, signals=[], trade_date=date)
    _orion_shadow(tmp_path, trade_date=date, weights={'INTC': .2, 'LRCX': .2, 'MU': .2, 'STX': .2, 'WDC': .2})
    prices = {s:50 for s in symbols+['INTC','LRCX','MU','STX','WDC']}
    from Tests.test_live_pilot_build_plan_from_precompute import _write_sleeve_evaluations
    from core.paper_target_authority import seal_paper_target_bundle
    _write_sleeve_evaluations(payload, tmp_path)
    seal_paper_target_bundle(bundle_dir=payload.parent, trade_date=date, repo_root=tmp_path,
                            sealed_at='2026-08-12T11:01:00Z')
    plan = _build(tmp_path, payload, prices=prices, approved_sleeve='caerus_paper_portfolio', capital_cap=1000,
                  lane='paper', shadow_root=tmp_path/'outputs/shadow_candidates', output_dir=tmp_path/'outputs/paper_lane/plans',
                  state_dir=tmp_path/'outputs/paper_lane/state')
    assert plan['status'] == 'READY_FOR_MANUAL_APPROVAL', plan
    class FractionalBroker(TrackingPaperBroker):
        def get_asset(self, symbol):
            return {**super().get_asset(symbol), 'fractionable': True}
        def get_latest_trades(self, tickers):
            result = super().get_latest_trades(tickers)
            if hold:
                for symbol, row in result.items():
                    if symbol in symbols: row['price'] = '70'
            return result
    broker = FractionalBroker()
    if hold:
        broker.cash = 300
        broker.positions = [{'symbol':s,'qty':'1','market_value':'70'} for s in symbols]
    state_root = tmp_path/'outputs/paper_lane/state/regime_authority'
    result = authorize_exact_execution_plan(plan=plan, broker=broker, env=_env(), run_id='aquila-active-chain',
        plan_path=Path(plan['json_path']), created_at='2026-08-12T13:35:01Z', regime_state_root=state_root)
    result = _finalize_direct_authorization(state_root, result)
    exact = exact_execution_plan_from_dict(result['exact_execution_plan'])
    assert exact.strategy_id == 'caerus_paper_portfolio'
    assert exact.constraints['paper_regime_owner'] == 'caerus_orion'
    qa = exact.constraints['aquila_quantity_authority']
    assert qa['aquila_account_weight_at_decision'] == pytest.approx(.7 if hold else .5)
    assert qa['orion_account_weight_at_decision'] == pytest.approx(.25 if hold else .45)
    assert len(qa['desired_quantities']) == 15
    assert all(qa['desired_quantities'][s]['caerus_aquila'] == 1 for s in symbols)
    assert broker.submit_calls == 0
    if hold:
        assert not set(symbols).intersection(row['symbol'] for row in exact.orders)

    from Tests.test_exact_execution_choice2 import run_live_pilot
    import scripts.live_pilot_execute as executor
    monkeypatch.setattr(executor, "_now_utc", lambda: "2026-08-12T13:35:03Z")
    completed = run_live_pilot(plan={**plan, **result}, broker=broker,
        env={**_env(), "CAERUS_EXACT_MAX_PLAN_AGE_SECONDS": "315360000"},
        run_id="aquila-active-chain-execute", output_root=tmp_path/"outputs/paper_lane")
    assert completed["terminal_status"] == "SUBMITTED", completed
    assert completed["reconciliation_status"] == "CLEAN"
    assert completed["execution_target_attainment_status"] == "OK_TARGET_ATTAINED"
    assert broker.submit_calls == len(exact.orders)
