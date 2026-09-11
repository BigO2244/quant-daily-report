"""Integration: a verified bridge changes residual sizing, never the frozen book."""
import pytest
from Tests.test_aquila_exact_authorization import case
from scripts.authorize_exact_execution_plan import _apply_aquila_quantity_authority


def test_verified_bridge_sizes_only_unsold_owner_quantity(tmp_path, monkeypatch):
    args = case(tmp_path)
    args['broker_positions'][0]['quantity'] = 15
    frozen = (tmp_path / 'ownership.json').read_bytes()
    bridge = {'content_hash': 'a' * 64, 'applied_fills': [{'symbol': 'AAPL', 'quantity': 5}]}
    monkeypatch.setattr('core.aquila_recovery_ownership.build_orion_sell_recovery_bridge',
                        lambda **kwargs: ({'AAPL': {'caerus_aquila': 10, 'caerus_orion': 5}}, bridge))
    _, result = _apply_aquila_quantity_authority(
        **args, prices={'AAPL': 100, 'MSFT': 100},
        recovery_context={'policy': {}, 'epoch': 'approved', 'cash': 500,
                          'lookup': None, 'open_orders': []})
    assert result['signed_sleeve_demands']['AAPL'] == {'caerus_aquila': 0, 'caerus_orion': -5}
    assert result['recovery_ownership_bridge'] == bridge
    assert (tmp_path / 'ownership.json').read_bytes() == frozen


def test_rejected_bridge_cannot_fall_back_to_unverified_sizing(tmp_path, monkeypatch):
    args = case(tmp_path)
    args['broker_positions'][0]['quantity'] = 15
    def reject(**kwargs):
        raise RuntimeError('economic proof missing')
    monkeypatch.setattr('core.aquila_recovery_ownership.build_orion_sell_recovery_bridge', reject)
    with pytest.raises(RuntimeError, match='economic proof missing'):
        _apply_aquila_quantity_authority(
            **args, prices={'AAPL': 100, 'MSFT': 100},
            recovery_context={'policy': {}, 'epoch': 'approved', 'cash': 500,
                              'lookup': None, 'open_orders': []})
