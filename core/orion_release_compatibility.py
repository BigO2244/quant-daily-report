"""Exact reviewed release bridge; never a generic stale-evidence exception."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Mapping, Any

BRIDGE_PATH = 'config/operations/orion_release_bridge_20260914.json'
SCHEMA = 'caerus.orion_release_bridge.v1'


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=root, text=True,
                                   stderr=subprocess.PIPE, timeout=20).strip()


def content_digest(root: Path, revision: str = 'HEAD') -> str:
    """Bind modes, object types, paths and blob IDs, excluding only this config."""
    data = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', revision],
                                   cwd=root, timeout=20)
    rows = []
    for record in data.split(b'\0'):
        if not record:
            continue
        metadata, path = record.split(b'\t', 1)
        if path.decode() == BRIDGE_PATH:
            continue
        rows.append(path + b'\0' + metadata)
    return hashlib.sha256(b'\0'.join(sorted(rows))).hexdigest()


def verify_bridge(*, runtime_root: Path, evidence_root: Path, report_date: str,
                  marker_path: Path, marker: Mapping[str, Any], head: str) -> dict:
    path = runtime_root / BRIDGE_PATH
    bridge = json.loads(path.read_text())
    expected_keys = {'schema_version', 'report_date', 'effective_trade_date',
                     'producer_sha', 'parent_sha', 'runtime_content_sha256',
                     'marker_sha256', 'source_sha256', 'hydration_sha256',
                     'decision_lineage_hash', 'review'}
    if set(bridge) not in (expected_keys, expected_keys | {'intermediate_shas'}) or bridge['schema_version'] != SCHEMA:
        raise ValueError('schema')
    if report_date != bridge['report_date'] or marker['effective_trade_date'] != bridge['effective_trade_date']:
        raise ValueError('date')
    if marker['deployed_git_sha'] != bridge['producer_sha']:
        raise ValueError('producer')
    for key in ('producer_sha', 'parent_sha'):
        if len(bridge[key]) != 40 or any(c not in '0123456789abcdef' for c in bridge[key]):
            raise ValueError('invalid_sha')
    if _git(runtime_root, 'show', '-s', '--format=%P', head) != bridge['parent_sha']:
        raise ValueError('runtime_parent')
    intermediates = bridge.get('intermediate_shas', [])
    if (not isinstance(intermediates, list) or len(intermediates) > 8
            or any(not isinstance(v, str) or len(v) != 40
                   or any(c not in '0123456789abcdef' for c in v)
                   for v in intermediates)):
        raise ValueError('intermediate_shas')
    chain = [bridge['parent_sha'], *intermediates, bridge['producer_sha']]
    if len(chain) != len(set(chain)):
        raise ValueError('duplicate_release_ancestor')
    for child, parent in zip(chain, chain[1:]):
        if _git(runtime_root, 'show', '-s', '--format=%P', child) != parent:
            raise ValueError('producer_parent')
    if content_digest(runtime_root) != bridge['runtime_content_sha256']:
        raise ValueError('runtime_content')
    day = bridge['effective_trade_date']
    paths = {'marker_sha256': marker_path,
             'source_sha256': evidence_root / 'outputs/shadow_candidates' / day / 'caerus_orion.json',
             'hydration_sha256': evidence_root / 'outputs/price_hydration' / day / 'status.json'}
    for key, artifact in paths.items():
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != bridge[key]:
            raise ValueError(key)
    if marker['decision_lineage_hash'] != bridge['decision_lineage_hash']:
        raise ValueError('lineage')
    return {'status': 'APPROVED_RELEASE_COMPATIBILITY_BRIDGE',
            'bridge_path': BRIDGE_PATH, 'bridge_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'producer_sha': bridge['producer_sha'], 'runtime_sha': head,
            'runtime_content_sha256': bridge['runtime_content_sha256'],
            'report_date': report_date, 'effective_trade_date': day,
            'marker_sha256': bridge['marker_sha256']}
