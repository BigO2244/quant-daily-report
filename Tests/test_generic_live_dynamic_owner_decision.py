from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from core.generic_live_dynamic_owner_decision import (
    GenericLiveDynamicOwnerDecisionError,
    build_generic_live_dynamic_owner_decision,
    content_hash,
    validate_generic_live_dynamic_owner_decision,
)


SUPERSEDED = ["a" * 64, "b" * 64]
ROOT = Path(__file__).resolve().parents[1]


def _decision():
    return build_generic_live_dynamic_owner_decision(
        decided_at="2026-08-19T13:26:24+00:00",
        effective_session="2026-08-24",
        expires_at="2026-08-24T20:00:00+00:00",
        supersedes_fixed_capital_artifact_hashes=SUPERSEDED,
    )


def test_dynamic_owner_decision_has_no_nominal_ceiling_or_execution_authority():
    decision = _decision()
    assert decision["capital_policy"]["nominal_capital_ceiling_usd"] is None
    assert decision["capital_policy"]["gross_capital_basis"].endswith("NET_LIQUIDATION_EQUITY")
    assert decision["capital_policy"]["buy_cash_basis"].endswith("SETTLED_CASH")
    assert decision["capital_policy"]["buying_power_allowed"] is False
    assert decision["execution_authority"] is False
    assert decision["activation_authority"] is False
    assert decision["paper_authority_changed"] is False


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("capital_policy", "nominal_capital_ceiling_usd", 460.0),
        ("capital_policy", "buying_power_allowed", True),
        ("capital_policy", "unsettled_funds_allowed", True),
        ("trading_constraints", "maximum_orders_per_session", 2),
        ("trading_constraints", "leverage_allowed", True),
    ],
)
def test_dynamic_owner_decision_rejects_resealed_policy_drift(section, field, value):
    changed = copy.deepcopy(_decision())
    changed[section][field] = value
    changed["content_hash"] = content_hash(changed)
    with pytest.raises(GenericLiveDynamicOwnerDecisionError):
        validate_generic_live_dynamic_owner_decision(changed)


def test_dynamic_owner_decision_rejects_unlisted_or_duplicate_supersession():
    changed = copy.deepcopy(_decision())
    changed["supersedes_fixed_capital_artifact_hashes"] = ["a" * 64, "a" * 64]
    changed["content_hash"] = content_hash(changed)
    with pytest.raises(GenericLiveDynamicOwnerDecisionError):
        validate_generic_live_dynamic_owner_decision(changed)


def test_sealed_owner_record_is_valid_and_invalidates_fixed_cap_artifacts():
    record = json.loads((
        ROOT / "docs/governance/decision_records/"
        "generic_live_v1_dynamic_balance_owner_decision_20260824.json"
    ).read_text())
    validated = validate_generic_live_dynamic_owner_decision(record)
    assert validated["content_hash"] == "abc334ba680afe4b9ae50ce815ad2f591a842931f8c3a12b797e2fdadc58b506"
    assert "ef5d9ae5ab9833208c0c3979a4ce743fbd0c36fa5676c3910af799444bc94ba7" in validated["supersedes_fixed_capital_artifact_hashes"]
    assert "4b697447a9f5760c10df7ba0e65cbef2ab627feb177a3ec4f1437ace57b8068b" in validated["supersedes_fixed_capital_artifact_hashes"]
