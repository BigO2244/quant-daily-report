"""Explicit historical Orion-only configuration for pre-Aquila execution tests.

This fixture changes input paths, never registry validation or eligibility rules.
It is opt-in through a test parameter; production inventory tests do not use it.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def orion_registry_payload():
    payload = json.loads((ROOT / 'config/research/strategy_registry.json').read_text())
    payload['strategies'] = [row for row in payload['strategies'] if row['strategy_id'] != 'caerus_aquila']
    control = payload['sleeve_control_plane']
    control['strategy_overrides'].pop('caerus_aquila', None)
    control['paper_allocation_policy']['sleeve_risk_budgets'] = {'caerus_orion': 1.0}
    return payload


def orion_manifest_payload():
    payload = json.loads((ROOT / 'research_registry/sleeves/manifest.json').read_text())
    payload['sleeves'] = [row for row in payload['sleeves'] if row['strategy_id'] != 'caerus_aquila']
    return payload


def write_orion_registry(root):
    registry = root / 'config/research/strategy_registry.json'
    manifest = root / 'research_registry/sleeves/manifest.json'
    for path, payload in ((registry, orion_registry_payload()), (manifest, orion_manifest_payload())):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True) + '\n')
    return registry, manifest


@pytest.fixture
def orion_registry(tmp_path, monkeypatch):
    from core import sleeve_control_plane
    registry, manifest = write_orion_registry(tmp_path)
    # Exercise normal production parsing and matching-manifest validation.
    sleeve_control_plane.SleeveControlRegistry.from_path(registry, manifest_path=manifest)
    monkeypatch.setattr(sleeve_control_plane, 'default_registry_path', lambda: registry)
    return registry
