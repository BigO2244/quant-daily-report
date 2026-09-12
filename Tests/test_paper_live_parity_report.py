import hashlib
import json
from pathlib import Path
import pytest
from Tests.fixtures.orion_registry import orion_registry
from core.paper_live_parity import REQUIRED, compare, digest


def intent():
    return {'trade_date':'2026-09-11', 'model_version':'lyra-v1', 'data_cutoff':'2026-09-10',
            'universe':'a'*64,'security_master':'b'*64, 'plan_hash':'c'*64,
            'prices':{'ABC':100.}, 'target_weights':{'ABC':.95}, 'target_shares':{'ABC':4.75},
            'target_cash':25.,'sizing_logic':'broker_nav','rebudgeting_logic':'confirmed_cash',
            'execution_version':'v1','account_nav':500.,'positions':{},'available_cash':500.,
            'fractional_constraints':True,'risk_capital_limits':{'max':600.}}


def test_expected_account_difference(tmp_path):
    left, right = intent(), intent()
    left['account_nav'], right['account_nav'] = 10000, 495.69
    assert compare(paper=left,live=right,trade_date='2026-09-11',repo_root=tmp_path)['UNEXPLAINED']==0


def test_target_difference_missing_field_and_wrong_date_fail(tmp_path):
    left, right = intent(), intent()
    right['target_weights'] = {'ABC':1}
    right.pop('prices')
    right['trade_date']='2026-09-10'
    report=compare(paper=left,live=right,trade_date='2026-09-11',repo_root=tmp_path)
    assert report['UNEXPLAINED']==3
    assert not report['execution_gate_pass']


def test_explanations_bound_to_values_date_and_evidence(tmp_path):
    left,right=intent(),intent();right['model_version']='different'
    (tmp_path/'decision.md').write_text('Separately authorized model versions')
    explain={'model_version':{'evidence_path':'decision.md','evidence_sha256':hashlib.sha256((tmp_path/'decision.md').read_bytes()).hexdigest(),
        'paper_value_hash':digest(left['model_version']), 'live_value_hash':digest(right['model_version']),
        'trade_date':'2026-09-11','reason':'separately authorized models'}}
    assert compare(paper=left,live=right,trade_date='2026-09-11',repo_root=tmp_path,explanations=explain)['UNEXPLAINED']==0
    right['model_version']='another'
    assert compare(paper=left,live=right,trade_date='2026-09-11',repo_root=tmp_path,explanations=explain)['UNEXPLAINED']==1


def test_empty_values_never_certify(tmp_path):
    bad={'trade_date':'2026-09-11',**{name:'' for name in REQUIRED}}
    assert compare(paper=bad,live=bad,trade_date='2026-09-11',repo_root=tmp_path)['UNEXPLAINED']==len(REQUIRED)


def test_risk_changes_require_evidence(tmp_path):
    left,right=intent(),intent();right['risk_capital_limits']={'max':100000}
    assert compare(paper=left,live=right,trade_date='2026-09-11',repo_root=tmp_path)['UNEXPLAINED']==1


def test_pretrade_gate_recomputes_and_binds_submitted_plan(tmp_path):
    import json,pytest
    from core.paper_live_parity import require_pretrade_parity
    inputs=tmp_path/'outputs/paper_live_parity/2026-09-11';inputs.mkdir(parents=True)
    with pytest.raises(ValueError,match='not_aligned'):
        require_pretrade_parity(repo_root=tmp_path,trade_date='2026-09-11',lane='paper',plan_hash='c'*64)
    for lane in ('paper','live'): (inputs/(lane+'_intent.json')).write_text(json.dumps(intent()))
    assert require_pretrade_parity(repo_root=tmp_path,trade_date='2026-09-11',lane='paper',plan_hash='c'*64)['execution_gate_pass']
    with pytest.raises(ValueError,match='not_aligned'):
        require_pretrade_parity(repo_root=tmp_path,trade_date='2026-09-11',lane='live',plan_hash='d'*64)


@pytest.fixture
def paused_paper(tmp_path, monkeypatch, orion_registry):
    from authority.exact_plan import build_exact_execution_plan
    from core.regime_state_store import persist_regime_authority
    account = hashlib.sha256(b'parity-paper-fixture').hexdigest()
    state = persist_regime_authority(tmp_path/'regime', account_scope='PAPER',
        account_id=account, sleeve_id='caerus_orion', authorization_run_id='parity-fixture',
        trade_date='2026-09-11', recorded_at='2026-09-11T13:35:00Z', observed_state='NORMAL',
        confidence=1., acute_risk=False, risk_package_id='risk:fixture',
        risk_package_hash='b'*64, market_state_id='market:fixture')
    exact = build_exact_execution_plan(run_id='parity-fixture',
        as_of='2026-09-11T13:35:00Z', created_at='2026-09-11T13:35:01Z',
        orchestrator_version='fixture', source_precompute_ids=['precompute:fixture'],
        source_artifact_hashes={'precompute': 'a'*64}, market_state_id='market:fixture',
        market_state={'session': 'OPEN'}, regime_state=state.regime_state(),
        sleeve_allocations=[{'sleeve_id': 'caerus_orion', 'weight': 1., 'capital_eligible': True}],
        portfolio_nav=1000., starting_positions=[], starting_cash=1000., account_id_hash=account,
        risk_state={'status': 'PASS'}, sell_orders=[], buy_orders=[],
        expected_posttrade_positions=[], expected_posttrade_cash=1000.,
        constraints={'capital_cap_usd': 1000., 'max_orders': 2},
        authorization_state={'status': 'AUTHORIZED', 'authority': 'CAERUS_ORCHESTRATOR',
                             'authorization_reason': 'AUTHORIZED_NO_TRADE',
                             'authorized_at': '2026-09-11T13:35:01Z'})
    source = Path(__file__).resolve().parents[1]/'config/operations/operating_lane_registry.json'
    registry_path = tmp_path/'config/operations/operating_lane_registry.json'
    registry_path.parent.mkdir(parents=True)
    registry_path.write_bytes(source.read_bytes())
    home = tmp_path/'home'
    env = home/'.caerus/lyra_live.env'
    env.parent.mkdir(parents=True)
    env.write_text('CAERUS_LYRA_LIVE_ENABLED=0\nSECRET_FIXTURE=not-for-reporting\n')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    return exact, env, registry_path


def invoke_paused(tmp_path, exact, **overrides):
    from core.paper_live_parity import require_pretrade_parity
    return require_pretrade_parity(**{'repo_root': tmp_path, 'trade_date': '2026-09-11',
        'lane': 'paper', 'plan_hash': exact.content_hash, 'actual_paper_plan': exact.to_dict(),
        **overrides})


def test_paused_live_preserves_failed_parity_but_satisfies_paper_dependency(tmp_path, paused_paper):
    exact, env, _ = paused_paper
    result = invoke_paused(tmp_path, exact)
    assert result['status'] == 'NOT_COMPARABLE_LIVE_PAUSED'
    assert result['UNEXPLAINED'] == len(REQUIRED)+3
    assert not result['execution_gate_pass']
    assert not result['production_authority']
    assert result['submission_dependency_satisfied']
    assert 'not-for-reporting' not in json.dumps(result)
    assert result['submission_dependency_evidence']['paper_plan_hash'] == exact.content_hash
    # A cached successful dependency result must never mask a newly enabled lane.
    env.write_text('CAERUS_LYRA_LIVE_ENABLED=1\n')
    with pytest.raises(ValueError, match='not_aligned'):
        invoke_paused(tmp_path, exact)
    report = json.loads((tmp_path/'outputs/paper_live_parity/2026-09-11/paper_live_parity_report.json').read_text())
    assert not report['submission_dependency_satisfied']


@pytest.mark.parametrize('bad_gate', ['missing', 'unknown', 'enabled', 'symlink'])
def test_pause_requires_direct_explicit_runtime_flag(tmp_path, paused_paper, bad_gate):
    exact, env, _ = paused_paper
    if bad_gate == 'missing': env.unlink()
    elif bad_gate == 'unknown': env.write_text('CAERUS_LYRA_LIVE_ENABLED=unknown\n')
    elif bad_gate == 'enabled': env.write_text('CAERUS_LYRA_LIVE_ENABLED=1\n')
    else:
        env.unlink()
        cached = env.parent/'cached.env'
        cached.write_text('CAERUS_LYRA_LIVE_ENABLED=0\n')
        env.symlink_to(cached)
    with pytest.raises(ValueError, match='not_aligned'):
        invoke_paused(tmp_path, exact)


@pytest.mark.parametrize('invalid', ['missing', 'tamper', 'hash', 'date', 'scope', 'authority'])
def test_pause_requires_valid_actual_paper_plan(tmp_path, paused_paper, invalid):
    exact, _, _ = paused_paper
    actual = exact.to_dict()
    kwargs = {}
    if invalid == 'missing': actual = None
    elif invalid == 'tamper': actual['portfolio_nav'] = 500
    elif invalid == 'hash': kwargs['plan_hash'] = 'e'*64
    elif invalid == 'date': kwargs['trade_date'] = '2026-09-14'
    elif invalid == 'scope': actual['account_scope'] = 'LIVE'
    elif invalid == 'authority': actual['authorization_state']['status'] = 'UNAUTHORIZED'
    with pytest.raises(ValueError, match='not_aligned'):
        invoke_paused(tmp_path, exact, actual_paper_plan=actual, **kwargs)


def test_invalid_actual_plan_cannot_use_even_green_normalized_json(tmp_path, paused_paper):
    exact, _, _ = paused_paper
    inputs = tmp_path/'outputs/paper_live_parity/2026-09-11'
    inputs.mkdir(parents=True)
    for lane in ('paper', 'live'):
        (inputs/(lane+'_intent.json')).write_text(json.dumps({**intent(), 'plan_hash': exact.content_hash}))
    actual = exact.to_dict()
    actual['starting_cash'] = 500
    with pytest.raises(ValueError, match='not_aligned'):
        invoke_paused(tmp_path, exact, actual_paper_plan=actual)


@pytest.mark.parametrize('mismatch', ['hash', 'env_path', 'required_gate', 'owner_gate', 'broker', 'sleeve'])
def test_pause_requires_matching_canonical_registry(tmp_path, paused_paper, mismatch):
    from core.operating_truth import content_hash
    exact, _, path = paused_paper
    registry = json.loads(path.read_text())
    lanes = {row['lane_id']: row for row in registry['lanes']}
    if mismatch == 'hash': registry['content_hash'] = '0'*64
    else:
        if mismatch == 'env_path': lanes['lyra_live']['runtime']['env_path'] = '.caerus/other.env'
        elif mismatch == 'required_gate': lanes['lyra_live']['runtime']['required_gates']['CAERUS_LYRA_LIVE_ENABLED'] = '0'
        elif mismatch == 'owner_gate': lanes['lyra_live']['runtime']['required_gates']['CAERUS_LYRA_LIVE_OWNER_DECISION_HASH'] = '0'*64
        elif mismatch == 'broker': lanes['orion_paper']['broker_environment'] = 'ALPACA_LIVE'
        elif mismatch == 'sleeve': lanes['orion_paper']['strategy_ids'] = ['caerus_other']
        registry['content_hash'] = content_hash(registry)
    path.write_text(json.dumps(registry))
    with pytest.raises(ValueError, match='not_aligned'):
        invoke_paused(tmp_path, exact)


def test_live_never_receives_paused_paper_exception(tmp_path, paused_paper):
    exact, _, _ = paused_paper
    with pytest.raises(ValueError, match='not_aligned'):
        invoke_paused(tmp_path, exact, lane='live')


def test_normalized_json_cannot_turn_paused_live_into_alignment(tmp_path, paused_paper):
    exact, _, _ = paused_paper
    inputs = tmp_path/'outputs/paper_live_parity/2026-09-11'
    inputs.mkdir(parents=True)
    for lane in ('paper', 'live'):
        (inputs/(lane+'_intent.json')).write_text(json.dumps({**intent(), 'plan_hash': exact.content_hash}))
    report = invoke_paused(tmp_path, exact)
    assert report['status'] == 'NOT_COMPARABLE_LIVE_PAUSED'
    assert not report['execution_gate_pass']
    assert not report['production_authority']
