from __future__ import annotations

import copy

import pytest

from core.generic_live_dynamic_operational_proofs import (
    GenericLiveDynamicOperationalProofError,
    seal_generic_live_dynamic_operational_proofs,
    validate_generic_live_dynamic_operational_proofs,
)


def _proofs():
    return seal_generic_live_dynamic_operational_proofs({
        "generated_at": "2026-08-24T13:35:20+00:00",
        "deployed_sha": "a" * 40,
        "expected_deployed_sha": "a" * 40,
        "account_observation_hash": "1" * 64,
        "positions_observed_at": "2026-08-24T13:34:30+00:00",
        "positions_source_hash": "2" * 64,
        "open_orders_observed_at": "2026-08-24T13:34:35+00:00",
        "open_orders_source_hash": "3" * 64,
        "open_order_count": 0,
        "asset_observed_at": "2026-08-24T13:34:40+00:00",
        "asset_source_hash": "4" * 64,
        "asset_status": "active",
        "asset_tradable": True,
        "legacy_executor_disabled": True,
        "legacy_kill_switch_armed": True,
        "generic_kill_switch_armed": True,
        "generic_schedule_installed": True,
        "generic_submission_adapter_deployed": True,
        "rollback_rearm_proven": True,
        "order_lifecycle_pipeline_green": True,
        "reconciliation_pipeline_green": True,
        "accounting_pipeline_green": True,
        "reporting_pipeline_green": True,
        "broker_write_performed": False,
    })


def test_operational_proofs_require_every_read_fresher_than_120_seconds():
    assert validate_generic_live_dynamic_operational_proofs(
        _proofs(), as_of="2026-08-24T13:36:00+00:00"
    )["open_order_count"] == 0


def test_stale_or_resealed_false_gate_fails_closed():
    with pytest.raises(GenericLiveDynamicOperationalProofError, match="120 seconds"):
        validate_generic_live_dynamic_operational_proofs(
            _proofs(), as_of="2026-08-24T13:36:40+00:00"
        )
    changed = copy.deepcopy(_proofs())
    changed["rollback_rearm_proven"] = False
    changed = seal_generic_live_dynamic_operational_proofs(changed)
    with pytest.raises(GenericLiveDynamicOperationalProofError, match="not green"):
        validate_generic_live_dynamic_operational_proofs(
            changed, as_of="2026-08-24T13:36:00+00:00"
        )
