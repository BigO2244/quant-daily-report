import copy
import json
from pathlib import Path

import pytest

from Tests.fixtures.orion_registry import orion_registry
from core import target_replay as replay
from core.portfolio_operating_model import build_session_manifest, build_sleeve_decision_batch, allocate_portfolio


@pytest.fixture
def capsule(tmp_path, monkeypatch, orion_registry):
    from authority.contracts import build_evidence_package, build_decision_package, build_risk_package
    from authority.pipeline import execution_package_from_risk
    from Tests.test_exact_execution_choice2 import _plan, _rebuild_exact
    date = '2026-08-12'
    root = tmp_path/'capsule'
    def write(relative, value):
        path = root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True)+'\n')
        return replay._sha(path)
    registry = json.loads(orion_registry.read_text())
    registry_hash = write('config/research/strategy_registry.json', registry)
    manifest_hash = write('research_registry/sleeves/manifest.json', {'fixture': 'manifest'})
    monkeypatch.setattr(replay, 'CURRENT_REGISTRY', root/'config/research/strategy_registry.json')
    source_hash = write('source.json', {'target_weights': {'AAPL': 1.}})
    registry_ref = {'path': 'config/research/strategy_registry.json', 'sha256': registry_hash,
                    'manifest_path': 'research_registry/sleeves/manifest.json', 'manifest_sha256': manifest_hash}
    evaluations = {'trade_date': date, 'registry': registry_ref,
        'expected_non_frozen_sleeve_ids': ['caerus_orion'],
        'envelopes': [{'sleeve_id': 'caerus_orion', 'strategy_type': 'security_selection',
            'evaluation': {'status': 'OK'}, 'opportunity': {'available': True},
            'eligibility': {'capital_eligible': True, 'paper_execution_eligible': True},
            'lifecycle': {'status': 'paper'}, 'provenance': {
                'registry_sha256': registry_hash, 'manifest_sha256': manifest_hash,
                'source_artifacts': [{'path': 'source.json', 'sha256': source_hash, 'exists': True},
                                     {'path': 'originally_missing.json', 'sha256': None, 'exists': False}]}}]}
    bundle = f'outputs/precompute/{date}/'
    eval_hash = write(bundle+'sleeve_evaluations.json', evaluations)
    session = build_session_manifest(trade_date=date, run_id='fixture', as_of=date+'T09:00:00Z',
        repo_root=root, created_at=date+'T09:00:00Z', inputs=[
            {'name': 'registry', **registry_ref},
            {'name': 'manifest', 'path': registry_ref['manifest_path'], 'sha256': manifest_hash},
            {'name': 'source', 'path': 'source.json', 'sha256': source_hash},
            {'name': 'evaluation', 'path': bundle+'sleeve_evaluations.json', 'sha256': eval_hash}])
    decisions = build_sleeve_decision_batch(evaluation_batch=evaluations, session_manifest=session,
        repo_root=root, generated_at=date+'T09:01:00Z')
    allocation = allocate_portfolio(decision_batch=decisions,
        allocation_policy=registry['sleeve_control_plane']['paper_allocation_policy'], allocated_at=date+'T09:01:01Z')
    docs = {'session_manifest': session, 'sleeve_evaluations': evaluations,
            'sleeve_decisions': decisions, 'portfolio_allocation': allocation}
    hashes = {role: write(bundle+role+'.json', doc) for role, doc in docs.items()}
    projection = replay._target_projection(allocation['targets'])
    evidence = build_evidence_package(package_id='evidence:fixture', trade_date=date,
        source_refs=['source.json'], observations=projection)
    decision = build_decision_package(package_id='decision:fixture', trade_date=date, evidence=evidence,
        target_rows=projection, source_refs=['source.json'], target_cash_weight=.05)
    consumed = execution_package_from_risk(build_risk_package(package_id='risk:fixture', decision=decision,
        approved_target_rows=projection, constraints={}, source_refs=['source.json']))
    target = {'trade_date': date, 'session_id': session['session_id'], 'session_content_hash': session['content_hash'],
        'allocation_id': allocation['allocation_id'], 'allocation_content_hash': allocation['content_hash'],
        'sleeve_decision_batch_hash': decisions['content_hash'], 'decision_package': decision.to_dict(),
        'approved_target_hash': decision.content_hash, 'target_rows': projection,
        **{'source_'+role: {'path': bundle+role+'.json', 'sha256': digest} for role, digest in hashes.items()}}
    hashes['paper_target_package'] = write(bundle+'paper_target_package.json', target)
    write(bundle+'contract.json', {'schema_version': 3, 'trade_date': date,
        'session_id': session['session_id'], 'session_content_hash': session['content_hash'],
        'allocation_id': allocation['allocation_id'], 'allocation_content_hash': allocation['content_hash'],
        'approved_target_hash': decision.content_hash,
        'files': {role: role+'.json' for role in hashes}, 'file_sha256': hashes})
    plan = _rebuild_exact(_plan().to_dict(), source_artifact_hashes={
        **{bundle+role+'.json': digest for role, digest in hashes.items()},
        'approved_target_package': consumed.content_hash,
        'sealed_precompute_decision_target': decision.content_hash})
    return root, {'exact_execution_plan': plan.to_dict(), 'approved_execution_package': consumed.to_dict()}, date


def test_recomputes_twice_from_original_inputs(capsule, monkeypatch):
    root, payload, date = capsule
    calls = []
    original = replay.build_sleeve_decision_batch
    def record(**kwargs):
        calls.append(kwargs['evaluation_batch'])
        return original(**kwargs)
    monkeypatch.setattr(replay, 'build_sleeve_decision_batch', record)
    report = replay.verify_target_replay(repo_root=root, payload=payload, trade_date=date)
    assert report['pass'], report['reasons']
    assert len(calls) == 2 and calls[0] is not calls[1]
    assert report['independent_recomputations'] == 2
    assert report['originally_absent_paths'] == ['originally_missing.json']
    assert not report['production_authority']
    assert report['complete_plan_source_hashes'] == payload['exact_execution_plan']['source_artifact_hashes']


@pytest.mark.parametrize('corruption', ['source', 'missing_source', 'absence', 'registry', 'role',
    'contract', 'decisions', 'allocation', 'target', 'consumed', 'plan', 'date', 'missing_anchor', 'wrong_prefix'])
def test_mutations_cannot_certify(capsule, corruption):
    from Tests.test_exact_execution_choice2 import _rebuild_exact
    root, payload, date = capsule
    bundle = root/f'outputs/precompute/{date}'
    if corruption == 'source': (root/'source.json').write_text('{"target_weights":{"MSFT":1}}')
    elif corruption == 'missing_source': (root/'source.json').unlink()
    elif corruption == 'absence': (root/'originally_missing.json').write_text('{}')
    elif corruption == 'registry': (root/'config/research/strategy_registry.json').write_text('{}')
    elif corruption == 'role': (bundle/'session_manifest.json').write_text((bundle/'sleeve_decisions.json').read_text())
    elif corruption in {'contract', 'decisions', 'allocation', 'target'}:
        name = {'contract': 'contract', 'decisions': 'sleeve_decisions', 'allocation': 'portfolio_allocation',
                'target': 'paper_target_package'}[corruption]
        path = bundle/(name+'.json'); data=json.loads(path.read_text())
        if corruption == 'contract': data['file_sha256']['session_manifest'] = '0'*64
        else: data['extra']='mutation'
        path.write_text(json.dumps(data))
    elif corruption == 'consumed': payload['approved_execution_package']['approved_target_rows'][0]['target_weight'] = .5
    elif corruption == 'plan': payload['exact_execution_plan']['portfolio_nav'] = 500
    elif corruption == 'date': date = '2026-08-13'
    else:
        sources = dict(payload['exact_execution_plan']['source_artifact_hashes'])
        key = f'outputs/precompute/{date}/session_manifest.json'
        value = sources.pop(key)
        if corruption == 'wrong_prefix': sources['/untrusted/runtime/'+key] = value
        payload['exact_execution_plan'] = _rebuild_exact(payload['exact_execution_plan'], source_artifact_hashes=sources).to_dict()
    result = replay.verify_target_replay(repo_root=root, payload=payload, trade_date=date)
    assert not result['pass']
    assert result['reasons']


def test_implicit_policy_drift_fails_even_when_capsule_unchanged(capsule, tmp_path, monkeypatch):
    root, payload, date = capsule
    changed = tmp_path/'changed_registry.json'; changed.write_text('{}')
    monkeypatch.setattr(replay, 'CURRENT_REGISTRY', changed)
    report = replay.verify_target_replay(repo_root=root, payload=payload, trade_date=date)
    assert not report['pass']
    assert 'implicit_current_registry_drift' in report['reasons'][0]


def test_copied_target_receipt_cannot_replace_inputs(capsule):
    root, payload, date = capsule
    (root/'source.json').unlink()
    receipt = root/f'outputs/execution_certification/{date}/target_replay.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'pass': True, 'producer_verified': True, 'targets': ['copy1', 'copy2']}))
    assert not replay.verify_target_replay(repo_root=root, payload=payload, trade_date=date)['pass']


def test_full_output_must_match_even_if_second_copy_matches_first(capsule, monkeypatch):
    root, payload, date = capsule
    original = replay.allocate_portfolio
    def wrong(**kwargs):
        output = original(**kwargs)
        output['targets'][0]['target_weight'] = .1
        return output
    monkeypatch.setattr(replay, 'allocate_portfolio', wrong)
    result = replay.verify_target_replay(repo_root=root, payload=payload, trade_date=date)
    assert not result['pass']
    assert 'producer_allocation_differs' in result['reasons'][0]


def test_certifier_executes_producer_instead_of_trusting_receipt(capsule, monkeypatch):
    import core.execution_certification as certification
    root, payload, date = capsule
    run = root/'run'; run.mkdir()
    (run/'execution_payload.json').write_text(json.dumps(payload))
    receipt = root/f'outputs/execution_certification/{date}/target_replay.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'pass': False, 'targets': []}))
    monkeypatch.setattr(certification, '_find_submit_run', lambda *args: run)
    monkeypatch.setattr(certification, 'integrity_session', lambda **kwargs: {'controls': {
        'broker_reconciliation': {'pass': False, 'reasons': ['fixture_missing']},
        'execution_consumed_exact_artifact': {'pass': False, 'reasons': ['fixture_missing']}}})
    report = certification.certify_session(repo_root=root, trade_date=date)
    assert report['controls']['deterministic_target']['pass']
    assert not report['certified']
    assert str(receipt.relative_to(root)) in report['evidence_sha256']
