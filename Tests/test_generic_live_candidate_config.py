import copy
import json
from pathlib import Path

import pytest

from core.generic_live_candidate_config import (
    GenericLiveCandidateError,
    build_generic_live_candidate_config,
    build_generic_live_candidate_preflight,
    validate_generic_live_candidate_config,
    validate_generic_live_candidate_preflight,
    validate_redacted_live_account_observation,
)
from core.generic_paper_live_rehearsal import build_generic_paper_live_rehearsal
from core.scheduled_v2_factual_pipeline import run_scheduled_v2_factual_pipeline
from Tests.test_scheduled_v2_factual_pipeline import _inputs as _pipeline_inputs


ROOT = Path(__file__).resolve().parents[1]
STAGING_COMMIT = "13f07fdd34e819d3b1c211c421c683445049dcfa"
STAGING_EVIDENCE_HASH = "52b674a8c47287b741875de87aef502099cbfeb2995661e056f397c3228e0e87"


def _inputs():
    inventory = json.loads(
        (ROOT / "docs/evidence/generic_live_vm_preflight_2026-08-18.json").read_text()
    )
    observation = json.loads(
        (ROOT / "docs/evidence/generic_live_account_observation_2026-08-18.json").read_text()
    )
    return inventory, observation


def _candidate(**overrides):
    inventory, observation = _inputs()
    args = {
        "vm_inventory": inventory,
        "live_account_observation": observation,
        "created_at": "2026-08-19T02:00:00+00:00",
        "lane_id": "generic-live",
        "deployment_version": "generic-live-disabled-candidate-v1",
    }
    args.update(overrides)
    return build_generic_live_candidate_config(**args)


def _rehearsal():
    return build_generic_paper_live_rehearsal(
        created_at="2026-08-19T02:15:00+00:00",
        pipeline_receipt=run_scheduled_v2_factual_pipeline(**_pipeline_inputs()),
    )


def test_candidate_is_account_pinned_and_cap_is_lower_of_owner_and_live_equity():
    candidate = _candidate()
    assert candidate["observed_broker_equity_usd"] == 460.90
    assert candidate["owner_capital_ceiling_usd"] == 460.0
    assert candidate["effective_capital_ceiling_usd"] == 460.0
    assert candidate["account_id_hash"] == "cfdc5d0aa0e3fdc38adadc78f1ebc30cbc83df187a4223c22597e787cd8a7c85"
    assert candidate["maximum_order_count"] == 1
    assert candidate["minimum_trade_usd"] == 100.0
    assert candidate["maximum_gross_fraction"] == 0.95
    assert candidate["candidate_execution_enabled"] is False
    assert candidate["candidate_submission_enabled"] is False
    assert candidate["candidate_schedule_enabled"] is False
    assert candidate["secrets_persisted"] is False


def test_equity_below_owner_limit_becomes_the_ceiling():
    candidate = _candidate(owner_capital_ceiling_usd=500.0)
    assert candidate["effective_capital_ceiling_usd"] == 460.90


def test_preflight_reports_exact_active_cutover_blockers_and_no_authority():
    inventory, observation = _inputs()
    result = build_generic_live_candidate_preflight(
        candidate_config=_candidate(),
        vm_inventory=inventory,
        live_account_observation=observation,
        evaluated_at="2026-08-19T02:05:00+00:00",
        staging_commit=STAGING_COMMIT,
        staging_evidence_hash=STAGING_EVIDENCE_HASH,
        generic_staging_present=True,
        paper_live_rehearsal=_rehearsal(),
    )
    assert result["status"] == "BLOCKED"
    assert result["account_pin_match"] is True
    assert result["legacy_live_disabled"] is True
    assert result["kill_switch_engaged"] is True
    assert result["generic_staging_present"] is True
    assert result["paper_live_adapter_parity_proven"] is True
    assert result["paper_live_rehearsal_artifact_hash"] == "c7686e4f2b8f8d4a615b2a30b726334a977040e9d7be3478f17d1a0bc6410fe0"
    assert "PAPER_LIVE_ADAPTER_PARITY_NOT_PROVEN" not in result["reason_codes"]
    assert "OWNER_APPROVAL_NOT_RECORDED" in result["reason_codes"]
    assert "ACTIVE_ACCOUNT_PIN_NOT_CONFIGURED" in result["reason_codes"]
    assert result["candidate_execution_enabled"] is False
    assert result["execution_authority"] is False


def test_limit_weakening_is_rejected():
    with pytest.raises(GenericLiveCandidateError, match="legacy limit of 1"):
        _candidate(maximum_order_count=2)
    with pytest.raises(GenericLiveCandidateError, match="legacy \\$100 floor"):
        _candidate(minimum_trade_usd=10)
    with pytest.raises(GenericLiveCandidateError, match="0.95"):
        _candidate(maximum_gross_fraction=0.96)


def test_observation_and_candidate_tamper_fail_closed():
    _, observation = _inputs()
    tampered_observation = copy.deepcopy(observation)
    tampered_observation["equity"] = "999999.00"
    with pytest.raises(GenericLiveCandidateError, match="content_hash"):
        validate_redacted_live_account_observation(tampered_observation)

    tampered_candidate = _candidate()
    tampered_candidate["candidate_submission_enabled"] = True
    with pytest.raises(GenericLiveCandidateError):
        validate_generic_live_candidate_config(tampered_candidate)


def test_candidate_contains_no_raw_account_or_credential_material():
    serialized = json.dumps(_candidate(), sort_keys=True).lower()
    assert "account_number" not in serialized
    assert "api_key" not in serialized
    assert "secret_key" not in serialized


def test_persisted_redacted_candidate_and_blocked_preflight_are_sealed():
    candidate = json.loads(
        (ROOT / "docs/evidence/generic_live_disabled_candidate_config_2026-08-18.json").read_text()
    )
    preflight = json.loads(
        (ROOT / "docs/evidence/generic_live_disabled_candidate_preflight_2026-08-18.json").read_text()
    )
    assert validate_generic_live_candidate_config(candidate)["effective_capital_ceiling_usd"] == 460.0
    checked = validate_generic_live_candidate_preflight(preflight)
    assert checked["status"] == "BLOCKED"
    assert checked["paper_live_adapter_parity_proven"] is True
