"""Offline evaluated-input -> decision -> allocation -> target verification.

This does not rerun alpha models, orders, NAV sizing, or runtime authorization.
Receipts describe the result; only executing this verifier can establish it.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath

from authority.exact_plan import exact_execution_plan_from_dict
from authority.pipeline import decision_package_from_dict, execution_package_from_dict
from core.portfolio_operating_model import (
    allocate_portfolio, build_sleeve_decision_batch, content_hash,
    validate_operating_model_lineage,
)
from core.paper_target_authority import _target_projection

CODE_ROOT = Path(__file__).resolve().parents[1]
CURRENT_REGISTRY = CODE_ROOT/'config/research/strategy_registry.json'
VM_PREFIX = '/home/brettolson/quant-daily-report/'
ROLES = ('session_manifest', 'sleeve_evaluations', 'sleeve_decisions',
         'portfolio_allocation', 'paper_target_package')
PRODUCER_FILES = ('core/target_replay.py', 'core/portfolio_operating_model.py',
                  'core/aquila_monthly.py', 'core/paper_target_authority.py',
                  'authority/exact_plan.py', 'authority/pipeline.py', 'authority/contracts.py')


class TargetReplayError(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise TargetReplayError(reason)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(raw):
    _require(isinstance(raw, str) and bool(raw), 'input_path_missing')
    if raw.startswith(VM_PREFIX):
        raw = raw[len(VM_PREFIX):]
    path = PurePosixPath(raw)
    _require(not path.is_absolute() and '..' not in path.parts and str(path) != '.',
             'input_path_outside_canonical_root')
    return path.as_posix()


def _path(root, raw):
    relative = _relative(raw)
    path = (root/relative).resolve()
    _require(path.is_relative_to(root), 'input_path_escapes_capsule')
    return path


def _read(path):
    value = json.loads(path.read_text())
    _require(isinstance(value, dict), 'input_not_object:'+path.name)
    return value


def _weights(rows):
    result = {}
    for row in rows:
        symbol = row['symbol']
        _require(symbol not in result, 'duplicate_target_symbol')
        result[symbol] = row['target_weight']
    _require(bool(result), 'target_weights_missing')
    return result


def _once(*, root, payload, trade_date):
    exact = exact_execution_plan_from_dict(payload['exact_execution_plan'], require_authorized=False)
    _require(exact.trade_date == trade_date, 'plan_trade_date_mismatch')
    _require(exact.authorization_state['status'] == 'AUTHORIZED'
             and exact.authorization_state['authority'] == 'CAERUS_ORCHESTRATOR',
             'sealed_authorization_identity_invalid')
    complete_sources = dict(exact.source_artifact_hashes)
    bundle = 'outputs/precompute/'+trade_date+'/'
    paths = {role: bundle+role+'.json' for role in ROLES}
    anchors = {}
    for raw, digest in complete_sources.items():
        # Semantic non-target anchors (e.g. NAV and market state) are preserved
        # in the full map, but are not inputs to this earlier target producer.
        if '/' in raw:
            relative = _relative(raw)
            if relative in paths.values():
                _require(relative not in anchors, 'duplicate_role_anchor')
                anchors[relative] = digest
    census = {}
    def check_file(raw, expected):
        relative = _relative(raw)
        path = _path(root, relative)
        _require(isinstance(expected, str) and len(expected) == 64, 'input_hash_missing:'+relative)
        _require(path.is_file() and _sha(path) == expected, 'input_missing_or_hash_mismatch:'+relative)
        census[relative] = expected
        return path
    docs = {}
    for role, relative in paths.items():
        _require(relative in anchors, 'plan_role_anchor_missing:'+role)
        docs[role] = _read(check_file(relative, anchors[relative]))
        _require(docs[role].get('trade_date') == trade_date, 'role_trade_date_mismatch:'+role)
    session, evaluations, decisions, allocation, target = (docs[role] for role in ROLES)
    _require(not validate_operating_model_lineage(session_manifest=session,
        decision_batch=decisions, allocation=allocation), 'session_decision_allocation_lineage_invalid')
    _require(decisions['content_hash'] == content_hash(decisions['decisions']), 'decision_batch_hash_invalid')

    contract_path = _path(root, bundle+'contract.json')
    contract = _read(contract_path)
    census[bundle+'contract.json'] = _sha(contract_path)
    _require(contract['trade_date'] == trade_date and contract['schema_version'] == 3,
             'contract_identity_invalid')
    for role, filename in contract['files'].items():
        _require('/' not in filename and filename == role+'.json', 'contract_file_role_mismatch')
        check_file(bundle+filename, contract['file_sha256'][role])
    for role in ROLES:
        _require(contract['files'][role] == role+'.json'
                 and contract['file_sha256'][role] == anchors[paths[role]], 'contract_plan_anchor_mismatch:'+role)
    _require(contract['session_id'] == target['session_id'] == session['session_id']
             and contract['session_content_hash'] == target['session_content_hash'] == session['content_hash']
             and contract['allocation_id'] == target['allocation_id'] == allocation['allocation_id']
             and contract['allocation_content_hash'] == target['allocation_content_hash'] == allocation['content_hash']
             and target['sleeve_decision_batch_hash'] == decisions['content_hash'], 'target_contract_chain_mismatch')
    for role in ROLES[:-1]:
        reference = target['source_'+role]
        _require(_relative(reference['path']) == paths[role] and reference['sha256'] == anchors[paths[role]],
                 'target_source_role_mismatch:'+role)
    decision = decision_package_from_dict(target['decision_package'])
    _require(decision.trade_date == trade_date
             and complete_sources['sealed_precompute_decision_target'] == decision.content_hash
             == target['approved_target_hash'] == contract['approved_target_hash'], 'sealed_target_hash_mismatch')
    _require(decision.to_dict()['target_rows'] == target['target_rows'], 'sealed_decision_target_mismatch')
    consumed = execution_package_from_dict(payload['approved_execution_package'])
    _require(consumed.content_hash == complete_sources['approved_target_package']
             and consumed.trade_date == trade_date, 'consumed_package_binding_mismatch')

    admission = {}
    absent = []
    for row in session['inputs']:
        relative = _relative(row['path'])
        _require(relative not in admission, 'duplicate_session_input')
        admission[relative] = row
        if row['exists'] is True:
            check_file(relative, row['sha256'])
        else:
            _require(row['exists'] is False and row['required'] is False and row['sha256'] is None
                     and not _path(root, relative).exists(), 'original_absence_changed:'+relative)
            absent.append(relative)
    registry_ref = evaluations['registry']
    registry_path = _relative(registry_ref['path'])
    _require(registry_path == 'config/research/strategy_registry.json'
             and admission[registry_path]['sha256'] == registry_ref['sha256'], 'registry_not_session_bound')
    registry = _read(check_file(registry_path, registry_ref['sha256']))
    # Aquila's existing validator implicitly reads this file. No injected policy
    # or current default may replace the captured policy behind this check.
    _require(_sha(CURRENT_REGISTRY) == registry_ref['sha256'], 'implicit_current_registry_drift')
    manifest_path = _relative(registry_ref['manifest_path'])
    _require(admission[manifest_path]['sha256'] == registry_ref['manifest_sha256'], 'manifest_not_session_bound')
    check_file(manifest_path, registry_ref['manifest_sha256'])
    replay_evaluations = copy.deepcopy(evaluations)
    for envelope in replay_evaluations['envelopes']:
        provenance = envelope['provenance']
        _require(provenance['registry_sha256'] == registry_ref['sha256']
                 and provenance['manifest_sha256'] == registry_ref['manifest_sha256'], 'evaluation_registry_mismatch')
        for source in provenance['source_artifacts']:
            relative = _relative(source['path'])
            if source['exists'] is True:
                check_file(relative, source['sha256'])
            else:
                _require(source['exists'] is False and source.get('sha256') is None
                         and not _path(root, relative).exists(), 'original_absence_changed:'+relative)
                absent.append(relative)
            # Equivalent filesystem relocation only; bytes of original evidence
            # are untouched and its original SHA remains in the census.
            source['path'] = relative
    rebuilt_decisions = build_sleeve_decision_batch(evaluation_batch=replay_evaluations,
        session_manifest=session, repo_root=root, generated_at=decisions['generated_at'])
    _require(rebuilt_decisions == decisions, 'producer_decisions_differ')
    rebuilt_allocation = allocate_portfolio(decision_batch=rebuilt_decisions,
        allocation_policy=registry['sleeve_control_plane']['paper_allocation_policy'],
        allocated_at=allocation['allocated_at'])
    _require(rebuilt_allocation == allocation, 'producer_allocation_differs')
    projection = _target_projection(list(rebuilt_allocation['targets']))
    _require(projection == target['target_rows'] and decision.target_cash_weight == allocation['target_cash_weight'],
             'producer_target_projection_differs')
    _require(_weights(projection) == _weights(consumed.to_dict()['approved_target_rows']), 'consumed_weights_differ')
    return {'plan_hash': exact.content_hash, 'complete_plan_source_hashes': complete_sources,
            'role_input_hashes': {role: {'path': paths[role], 'sha256': anchors[paths[role]]} for role in ROLES},
            'input_sha256': census, 'originally_absent_paths': sorted(set(absent)),
            'decision_batch_hash': rebuilt_decisions['content_hash'],
            'allocation_hash': rebuilt_allocation['content_hash'],
            'projection_hash': content_hash(projection), 'target_weights_hash': content_hash(_weights(projection))}


def verify_target_replay(*, repo_root: Path, payload, trade_date: str):
    result = {'schema_version': 'caerus.target_producer_replay.v1', 'trade_date': trade_date,
              'pass': False, 'production_authority': False,
              'coverage': 'evaluated_inputs_to_decisions_allocation_and_consumed_target_weights',
              'out_of_scope': ['alpha_model_economics', 'broker_NAV_sizing',
                               'committed_runtime_regime_verification', 'submission_authority'],
              'reasons': []}
    try:
        dt.date.fromisoformat(trade_date)
        root = Path(repo_root).resolve()
        code = {name: _sha(CODE_ROOT/name) for name in PRODUCER_FILES}
        passes = [_once(root=root, payload=json.loads(json.dumps(payload)), trade_date=trade_date)
                  for _ in range(2)]
        _require(passes[0] == passes[1], 'independent_reloads_differ')
        _require(code == {name: _sha(CODE_ROOT/name) for name in PRODUCER_FILES}, 'producer_code_changed_during_replay')
        result.update(passes[0], producer_code_sha256=code, independent_recomputations=2)
        result['pass'] = True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
        result['reasons'] = [type(exc).__name__+':'+str(exc)]
    return result
