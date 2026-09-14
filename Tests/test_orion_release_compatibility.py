from __future__ import annotations
import datetime as dt
import hashlib
import json
import subprocess
import pytest
from Tests.fixtures.orion_registry import orion_registry
from Tests.test_orion_downstream_freshness import _lineaged_source, _canonical_hash, _write_json
from core.orion_precompute_guard import validate_orion_precompute_dependency
from core.orion_release_compatibility import BRIDGE_PATH, SCHEMA, content_digest
from core.deployment_precompute_dependency import dependency_report_date, validate_candidate_dependency


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state(head):
    return {'schema_version':'caerus.deploy_state.v2','deployed_sha':head,'validated_sha':head,'target_sha':head,'validation_status':'PASS'}


@pytest.fixture
def ready_repo(orion_registry,tmp_path):
    r=tmp_path
    git(r,'init','-q');git(r,'config','user.email','test@example.com');git(r,'config','user.name','Test')
    (r/'.gitignore').write_text('outputs/\n');(r/'producer.py').write_text('VALUE=1\n')
    git(r,'add','.');git(r,'commit','-qm','Friday producer');producer=git(r,'rev-parse','HEAD')
    day='2026-09-11';source=_lineaged_source(day,salt='Friday');sp=r/'outputs/shadow_candidates'/day/'caerus_orion.json'
    _write_json(sp,source);_write_json(r/'outputs/shadow_candidates/2026-09-10/caerus_orion.json',_lineaged_source('2026-09-10',salt='Thursday'))
    hp=r/'outputs/price_hydration'/day/'status.json'
    _write_json(hp,{'status':'OK','as_of_date':day,'coverage_validation':{'status':'OK'},'shadow_refresh':{'status':'OK'}})
    marker={'schema_version':'caerus.orion_decision_readiness.v1','status':'READY','trade_date':day,'effective_trade_date':day,'generated_at_utc':day+'T23:00:00+00:00','source_artifact':{'path':str(sp.relative_to(r)),'sha256':sha(sp)},'decision_lineage':source['decision_lineage'],'decision_lineage_hash':_canonical_hash(source['decision_lineage']),'hydration_status':{'path':str(hp.relative_to(r)),'sha256':sha(hp)},'deployed_git_sha':producer}
    mp=hp.with_name('orion_decision_ready.json');_write_json(mp,marker)
    (r/'consumer.py').write_text('NAV_POLICY="current"\n');git(r,'add','.');git(r,'commit','-qm','Saturday consumers');parent=git(r,'rev-parse','HEAD')
    _write_json(r/'outputs/deploy_state.json',state(parent))
    assert validate_orion_precompute_dependency(repo_root=r,report_date='2026-09-14')['status']=='BLOCKED'
    (r/'bridge_guard.py').write_text('POLICY="exact reviewed release"\n');git(r,'add','.')
    bridge={'schema_version':SCHEMA,'report_date':'2026-09-14','effective_trade_date':day,'producer_sha':producer,'parent_sha':parent,'runtime_content_sha256':content_digest(r,git(r,'write-tree')),'marker_sha256':sha(mp),'source_sha256':sha(sp),'hydration_sha256':sha(hp),'decision_lineage_hash':marker['decision_lineage_hash'],'review':'fixture exact content review'}
    _write_json(r/BRIDGE_PATH,bridge);git(r,'add','.');git(r,'commit','-qm','Reviewed bridge')
    _write_json(r/'outputs/deploy_state.json',state(git(r,'rev-parse','HEAD')))
    return r


def result(r):
    return validate_orion_precompute_dependency(repo_root=r,report_date='2026-09-14')


def test_friday_saturday_monday_exact_bridge_and_actual_preflight(ready_repo):
    r=ready_repo;mp=r/'outputs/price_hydration/2026-09-11/orion_decision_ready.json';before=mp.read_bytes()
    got=result(r);assert got['status']=='READY',got
    assert got['release_compatibility']['status']=='APPROVED_RELEASE_COMPATIBILITY_BRIDGE'
    proof=validate_candidate_dependency(runtime_root=r,evidence_root=r,candidate_sha=git(r,'rev-parse','HEAD'),now=dt.datetime(2026,9,14,11,tzinfo=dt.timezone.utc))
    assert proof['status']=='READY',proof
    assert mp.read_bytes()==before


@pytest.mark.parametrize('key',['report_date','effective_trade_date','producer_sha','parent_sha','runtime_content_sha256','marker_sha256','source_sha256','hydration_sha256','decision_lineage_hash','schema_version'])
def test_each_bridge_binding_fails_closed(ready_repo,key):
    r=ready_repo;p=r/BRIDGE_PATH;d=json.loads(p.read_text());d[key]='wrong';_write_json(p,d)
    git(r,'add','.');git(r,'commit','--amend','--no-edit','-q');_write_json(r/'outputs/deploy_state.json',state(git(r,'rev-parse','HEAD')))
    assert result(r)['status']=='BLOCKED'


@pytest.mark.parametrize('path',['producer.py','consumer.py','new_model.py'])
def test_changed_any_nonbridge_source_rejects_even_with_new_attestation(ready_repo,path):
    r=ready_repo;(r/path).write_text('changed\n');git(r,'add','.');git(r,'commit','--amend','--no-edit','-q')
    _write_json(r/'outputs/deploy_state.json',state(git(r,'rev-parse','HEAD')))
    proof=validate_candidate_dependency(runtime_root=r,evidence_root=r,candidate_sha=git(r,'rev-parse','HEAD'),now=dt.datetime(2026,9,14,11,tzinfo=dt.timezone.utc))
    assert proof['status']=='BLOCKED'


@pytest.mark.parametrize('path',['outputs/price_hydration/2026-09-11/orion_decision_ready.json','outputs/price_hydration/2026-09-11/status.json','outputs/shadow_candidates/2026-09-11/caerus_orion.json'])
def test_artifact_bytes_not_rewritten_or_relaxed(ready_repo,path):
    p=ready_repo/path;p.write_text(p.read_text()+' ');assert result(ready_repo)['status']=='BLOCKED'


def test_runtime_dirty_attestation_and_other_day_block(ready_repo):
    r=ready_repo;p=r/'outputs/deploy_state.json';original=p.read_bytes();d=json.loads(original);d['validated_sha']='0'*40;_write_json(p,d)
    assert result(r)['status']=='BLOCKED'
    p.write_bytes(original);(r/'producer.py').write_text('dirty\n')
    assert 'orion_dependency:repo_runtime_not_clean_or_unavailable' in result(r)['failures']
    assert validate_orion_precompute_dependency(repo_root=r,report_date='2026-09-15')['status']=='BLOCKED'


@pytest.mark.parametrize('now,expected',[('2026-09-12T12:00:00+00:00','2026-09-14'),('2026-09-14T11:00:00+00:00','2026-09-14'),('2026-09-14T20:01:00+00:00','2026-09-15'),('2026-11-27T18:01:00+00:00','2026-11-30')])
def test_completed_session_selection_including_weekend_and_early_close(now,expected):
    assert dependency_report_date(dt.datetime.fromisoformat(now))==expected


def test_actual_dependency_failure_preserves_deployment_marker(tmp_path,monkeypatch):
    from Tests.test_deployment_attestation import _repo
    from scripts.finalize_deployment import finalize_deployment,DeploymentAttestationError
    import core.deployment_precompute_dependency as dep
    r=_repo(tmp_path);head=git(r,'rev-parse','HEAD');p=r/'outputs/deploy_state.json';p.parent.mkdir();p.write_text('original')
    monkeypatch.setattr(dep,'validate_candidate_dependency',lambda **kw:{'status':'BLOCKED','failures':['real_marker_mismatch']})
    with pytest.raises(DeploymentAttestationError,match='actual precompute dependency failed'):
        finalize_deployment(repo_root=r,expected_sha=head,validation_script=r/'validate.sh',runtime_root=r)
    assert p.read_text()=='original'


@pytest.mark.parametrize('streak',[1,2,3,4])
def test_clean_current_session_observes_without_claiming_certification(streak):
    from scripts.build_remediation_reports import current_session_status
    c={'through_date':'2026-09-14','status':'NOT_CERTIFIED','required_sessions':5,
       'consecutive_clean_sessions':streak,'sessions':[{'trade_date':'2026-09-14',
       'certified':True,'unexplained_count':0,'unexplained_discrepancies':[]}]}
    assert current_session_status(c,'2026-09-14')=='OBSERVING'
    assert c['status']=='NOT_CERTIFIED'
    c['sessions'][0]['certified']=False
    assert current_session_status(c,'2026-09-14')=='FAILED'


def test_missing_duplicate_or_wrong_date_session_still_fails():
    from scripts.build_remediation_reports import current_session_status
    c={'through_date':'2026-09-14','status':'NOT_CERTIFIED','required_sessions':5,
       'consecutive_clean_sessions':1,'sessions':[]}
    assert current_session_status(c,'2026-09-14')=='FAILED'
    row={'trade_date':'2026-09-14','certified':True,'unexplained_count':0,'unexplained_discrepancies':[]}
    c['sessions']=[row,row]
    assert current_session_status(c,'2026-09-14')=='FAILED'
    c['sessions']=[row];c['through_date']='2026-09-11'
    assert current_session_status(c,'2026-09-14')=='FAILED'


def extend_reviewed_bridge(r):
    path = r / BRIDGE_PATH
    prior = json.loads(path.read_text())
    old_head = git(r, 'rev-parse', 'HEAD')
    (r / 'recovered_parent_reader.py').write_text('CLOSED_CHAIN=True\n')
    git(r, 'add', '.')
    prior['intermediate_shas'] = [prior['parent_sha']]
    prior['parent_sha'] = old_head
    prior['runtime_content_sha256'] = content_digest(r, git(r, 'write-tree'))
    _write_json(path, prior)
    git(r, 'add', '.'); git(r, 'commit', '-qm', 'Reviewed exact successor')
    _write_json(r / 'outputs/deploy_state.json', state(git(r, 'rev-parse', 'HEAD')))
    return prior


def test_exact_reviewed_successor_preserves_friday_producer(ready_repo):
    r = ready_repo
    marker = r / 'outputs/price_hydration/2026-09-11/orion_decision_ready.json'
    before = marker.read_bytes()
    extend_reviewed_bridge(r)
    assert result(r)['status'] == 'READY'
    assert marker.read_bytes() == before


@pytest.mark.parametrize('mutation', ['missing', 'wrong', 'duplicate', 'too_long', 'not_list'])
def test_reviewed_successor_rejects_inexact_ancestor_chain(ready_repo, mutation):
    r = ready_repo; bridge = extend_reviewed_bridge(r)
    bad = {'missing': [], 'wrong': ['0'*40], 'duplicate': [bridge['parent_sha']],
           'too_long': ['0'*40]*9, 'not_list': bridge['producer_sha']}[mutation]
    bridge['intermediate_shas'] = bad
    _write_json(r / BRIDGE_PATH, bridge)
    git(r, 'add', '.'); git(r, 'commit', '--amend', '--no-edit', '-q')
    _write_json(r / 'outputs/deploy_state.json', state(git(r, 'rev-parse', 'HEAD')))
    assert result(r)['status'] == 'BLOCKED'
