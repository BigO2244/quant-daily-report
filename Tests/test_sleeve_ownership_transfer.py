from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from core.sleeve_ownership_transfer import (
    net_demands, validate_transfers, apply_transfers, commit_transfer_receipt,
    load_transfer_receipts, digest,
)


def authority(delta=None):
    demands = {'MU': delta or {'caerus_orion': -1.2, 'caerus_aquila': .5}}
    decisions = {s: dict(decision_id=s, decision_hash='d'*64) for s in demands['MU']}
    residual, transfers = net_demands(demands, {'MU': 1000}, decisions)
    return dict(signed_sleeve_demands=demands, broker_sleeve_demands=residual,
                internal_transfers=transfers, quantity_contract={'sizing_mode': 'REBALANCE_WEIGHT'},
                ownership_snapshot_sha256='b'*64)


@pytest.mark.parametrize('delta,net,source,target', [
    ({'caerus_orion': -1.2, 'caerus_aquila': .5}, -.7, 'caerus_orion', 'caerus_aquila'),
    ({'caerus_orion': -.2, 'caerus_aquila': .5}, .3, 'caerus_orion', 'caerus_aquila'),
    ({'caerus_orion': -.5, 'caerus_aquila': .5}, 0, 'caerus_orion', 'caerus_aquila'),
    ({'caerus_orion': .5, 'caerus_aquila': -.8}, -.3, 'caerus_aquila', 'caerus_orion'),
])
def test_net_account_trade_and_conserved_owner_inventory(delta, net, source, target):
    qa = authority(delta); residual = validate_transfers(qa)
    assert sum(residual['MU'].values()) == pytest.approx(net)
    book = {'MU': {source: 2.0, target: 0.0}}
    # Broker's net trade belongs only to the residual-demand owner.
    for owner, quantity in residual['MU'].items(): book['MU'][owner] += quantity
    pre_transfer_total = sum(book['MU'].values())
    apply_transfers(book, qa['internal_transfers'])
    assert sum(book['MU'].values()) == pytest.approx(pre_transfer_total)
    for owner, change in delta.items():
        assert book['MU'][owner] == pytest.approx((2 if owner == source else 0) + change)


@pytest.mark.parametrize('mutation', ['quantity','missing','residual','fixed','decision'])
def test_tampered_transfer_authority_fails(mutation):
    qa = authority()
    if mutation == 'quantity': qa['internal_transfers'][0]['quantity'] += .1
    if mutation == 'missing': qa['internal_transfers'] = []
    if mutation == 'residual': qa['broker_sleeve_demands']['MU']['caerus_orion'] = -.8
    if mutation == 'fixed': qa['quantity_contract']['sizing_mode'] = 'FIXED_QUANTITY'
    if mutation == 'decision': qa['internal_transfers'][0]['decisions']['caerus_aquila']['decision_hash'] = ''
    with pytest.raises((ValueError, KeyError)): validate_transfers(qa)


def fake_plan():
    qa = authority()
    raw = dict(content_hash='a'*64, plan_id='plan', account_id_hash='c'*64, account_scope='PAPER',
               created_at='2026-09-10T13:00:00Z', constraints={'aquila_quantity_authority': qa},
               sell_orders=[dict(symbol='MU',side='SELL',quantity=.7,client_order_id='client')],buy_orders=[])
    plan = SimpleNamespace(content_hash=raw['content_hash'],plan_id='plan',account_id_hash='c'*64,
                           orders=raw['sell_orders'],to_dict=lambda: raw)
    outcome = SimpleNamespace(terminal_outcome=SimpleNamespace(value='RECONCILED_SUCCESS'),
        plan_hash_validated=True,authorization_validated=True,plan_hash_received=plan.content_hash,
        orders_filled=[dict(id='broker')])
    return plan,outcome,raw


def test_commit_is_once_and_only_after_complete_success(tmp_path):
    plan,outcome,_ = fake_plan()
    kwargs=dict(plan=plan,receipt_root=tmp_path,run_id='first',outcome=outcome,
                economic_status='RECONCILED',attainment_ok=True)
    receipt=commit_transfer_receipt(**kwargs)
    assert commit_transfer_receipt(**{**kwargs,'run_id':'retry'}) == receipt
    assert len(list(tmp_path.glob('*.json'))) == 1
    for change in [dict(economic_status='FAILED_RECONCILIATION'),dict(attainment_ok=False)]:
        with pytest.raises(ValueError): commit_transfer_receipt(**{**kwargs,**change})
    outcome.orders_filled=[]
    with pytest.raises(ValueError): commit_transfer_receipt(**kwargs)


def test_receipt_requires_real_complete_broker_fills_and_account(tmp_path):
    plan,outcome,raw=fake_plan()
    receipt=commit_transfer_receipt(plan=plan,receipt_root=tmp_path,run_id='test',outcome=outcome,
                                   economic_status='RECONCILED',attainment_ok=True)
    kwargs=dict(receipt_root=tmp_path,plans={plan.content_hash:raw},account_hash=plan.account_id_hash,
        broker_orders={'broker':dict(id='broker',client_order_id='client')},
        fills=[dict(order_id='broker',qty=.7,symbol='MU',side='sell',transaction_time_utc='2026-09-10T13:05:00.12345Z')],
        as_of='2099-01-01T00:00:00Z')
    assert load_transfer_receipts(**kwargs)==[receipt]
    for changed in [dict(account_hash='wrong'),dict(fills=[]),dict(broker_orders={})]:
        with pytest.raises(ValueError): load_transfer_receipts(**{**kwargs,**changed})


def test_transfer_never_borrows_another_owners_inventory():
    with pytest.raises(ValueError,match='exceeds'):
        apply_transfers({'MU':{'caerus_orion':.1,'caerus_aquila':10}},authority()['internal_transfers'])


def test_committed_transfer_replays_once_and_independent_audit_agrees(tmp_path):
    import datetime as dt
    from Tests.test_paper_ownership_cutover import _inputs
    from scripts.build_paper_ownership_cutover import create_cutover
    from core.causal_ownership_ledger import build_causal_ownership
    from core.daily_portfolio_audit import _audit_cutover_ownership
    root, history, kwargs = _inputs(tmp_path)
    cutover = create_cutover(**kwargs)
    ledger = kwargs['ledger_dir']; plans = root / 'outputs/paper_lane/plans'
    qa = authority({'caerus_orion': -2, 'caerus_aquila': 2})
    qa['signed_sleeve_demands']['AAPL'] = qa['signed_sleeve_demands'].pop('MU')
    qa['broker_sleeve_demands']['AAPL'] = qa['broker_sleeve_demands'].pop('MU')
    qa['internal_transfers'][0]['symbol'] = 'AAPL'
    raw = dict(schema_version='caerus.execution_plan.v3',plan_id='transfer-only',account_scope='PAPER',
               account_id_hash='a'*64,created_at='2026-08-15T13:35:00Z',
               constraints={'aquila_quantity_authority':qa},sell_orders=[],buy_orders=[])
    raw['content_hash']=digest(raw)
    (plans/'exact_execution_plan_transfer.json').write_text(json.dumps(raw))
    plan=SimpleNamespace(content_hash=raw['content_hash'],plan_id=raw['plan_id'],account_id_hash='a'*64,
                         orders=[],to_dict=lambda:raw)
    outcome=SimpleNamespace(terminal_outcome=SimpleNamespace(value='AUTHORIZED_NO_TRADE'),
        plan_hash_validated=True,authorization_validated=True,plan_hash_received=plan.content_hash,orders_filled=[])
    receipt=commit_transfer_receipt(plan=plan,receipt_root=plans.parent/'ownership_transfers',run_id='transfer',
        outcome=outcome,economic_status='RECONCILED',attainment_ok=True)
    stamp=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=1)).isoformat()
    positions=json.loads((ledger/'positions_latest.json').read_text());positions['pulled_at_utc']=stamp
    (ledger/'positions_latest.json').write_text(json.dumps(positions))
    acct=json.loads((ledger/'account_snapshots.jsonl').read_text());acct['pulled_at_utc']=stamp
    with (ledger/'account_snapshots.jsonl').open('a') as f:f.write(json.dumps(acct)+'\n')
    paths=list(plans.glob('*.json'))
    for _ in range(2):
        assert build_causal_ownership(ledger_dir=ledger,exact_plan_paths=paths,plans_root=plans)['status']=='PASS'
        ownership=json.loads((ledger/'ownership_latest.json').read_text())
        assert {r['sleeve_id']:r['quantity'] for r in ownership['positions']}=={'caerus_orion':10,'caerus_aquila':2}
        _audit_cutover_ownership(root,ownership)
        assert (ledger/'causal_fills.jsonl').read_bytes()==history


def test_monthly_shared_symbol_authorizer_and_execution_core_agree(tmp_path):
    from dataclasses import replace
    from pathlib import Path
    import hashlib
    import pandas as pd
    from Tests.test_aquila_monthly import inputs
    from Tests.test_aquila_exact_authorization import Request
    from core.aquila_monthly import build_aquila_source
    from scripts.authorize_exact_execution_plan import _apply_aquila_quantity_authority, _bind_quantity_demand_owners
    from authority.contracts import build_evidence_package, build_decision_package, build_risk_package
    from authority.pipeline import execution_package_from_risk
    from execution.core import ExecutionRequest, compute_transition_trades, live_pilot_execution_config
    book=dict(account_id_hash='a'*64,opening_contract_hash='b'*64,reconciliation={'status':'PASS'},
              positions=[dict(symbol='S0',sleeve_id='caerus_orion',quantity=100)])
    book['content_hash']=digest(book)
    path=tmp_path/'ownership.json';path.write_text(json.dumps(book))
    args=inputs();args['ownership'].update(content_hash=book['content_hash'],source_path=str(path),
                                        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    contract=build_aquila_source(**args)['quantity_contract']
    rows=[]
    for i in range(10):
        contributions=[dict(sleeve_id='caerus_aquila',sleeve_internal_weight=.1,target_weight=.05,
                            quantity_contract_hash=contract['content_hash'],sizing_mode='REBALANCE_WEIGHT')]
        if i==0: contributions.append(dict(sleeve_id='caerus_orion',sleeve_internal_weight=1,target_weight=.45))
        rows.append(dict(symbol=f'S{i}',ticker=f'S{i}',target_weight=.5 if i==0 else .05,sleeve_contributions=contributions))
    allocation=dict(trade_date='2026-09-08',targets=rows,quantity_contracts={'caerus_aquila':contract},
                    sleeve_allocations=[dict(sleeve_id=s,decision_id=s,decision_hash='d'*64) for s in ['caerus_orion','caerus_aquila']])
    prices={f'S{i}':100 for i in range(10)}
    request,qa=_apply_aquila_quantity_authority(request=Request(pd.DataFrame(rows)),allocation=allocation,
        prices=prices,account_hash='a'*64,broker_positions=[dict(symbol='S0',quantity=100)],repo_root=tmp_path)
    assert qa['internal_transfers'][0]['quantity']==5
    evidence=build_evidence_package(package_id='e',trade_date='2026-09-08',source_refs=['test'],observations=[])
    decision=build_decision_package(package_id='d',trade_date='2026-09-08',evidence=evidence,target_rows=rows,source_refs=['test'],target_cash_weight=.05)
    registry=json.loads((Path(__file__).resolve().parents[1]/'config/research/strategy_registry.json').read_text())
    policy=registry['sleeve_control_plane']['paper_allocation_policy']['account_target_attainment_policy']
    risk=build_risk_package(package_id='r',decision=decision,approved_target_rows=rows,
        constraints={'target_attainment_policy':policy},source_refs=['test'],approved_cash_weight=.05)
    package=execution_package_from_risk(risk).to_dict();qa['approved_execution_package_hash']=package['content_hash']
    request=ExecutionRequest(holdings=pd.DataFrame([dict(ticker='S0',shares=100)]),targets=request.targets,
        prices=pd.Series(prices),total_equity=10000,starting_cash=0,target_cash_weight=.05,
        planning_account={'cash':0},run_id='test',price_basis='timestamped_alpaca_latest_trade_at_authorization',
        approved_execution_package=package,quantity_authority=qa)
    config=replace(live_pilot_execution_config(approved_cap_usd=10000,allow_fractional=True,
        allow_fractional_sells=True,max_orders=50,min_trade_usd=1,ledger_enabled=False),mode='paper')
    trades,_=compute_transition_trades(request=request,config=config)
    assert len(trades)==10
    shared=trades[trades.ticker=='S0'].iloc[0]
    assert shared.side=='SELL' and shared.shares==50
    orders=[dict(symbol=r.ticker,side=r.side,quantity=r.shares) for r in trades.itertuples()]
    _bind_quantity_demand_owners(orders,qa,allocation)
    assert next(r for r in orders if r['symbol']=='S0')['sleeve_contributions'][0]['sleeve_id']=='caerus_orion'
    attained={'S0':{'caerus_orion':100}}
    for r in orders:
        for c in r['sleeve_contributions']:
            owned=attained.setdefault(r['symbol'],{});owner=c['sleeve_id']
            owned[owner]=owned.get(owner,0)+(1 if r['side']=='BUY' else -1)*r['quantity']*c['allocation_fraction']
    apply_transfers(attained,qa['internal_transfers'])
    assert attained['S0']=={'caerus_orion':45,'caerus_aquila':5}
    assert attained==qa['desired_quantities']
