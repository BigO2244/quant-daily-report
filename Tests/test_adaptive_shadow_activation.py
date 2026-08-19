from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from research.adaptive_shadow_activation import (
    APPROVED_CANDIDATE_HASH,
    REQUIRED_GOVERNED_INPUTS,
    AdaptiveShadowActivationError,
    build_activation_readiness,
    build_owner_decision,
    content_hash,
    validate_activation_readiness,
    validate_candidate,
    validate_owner_decision,
)
from scripts.run_adaptive_shadow_evidence import main


ROOT = Path(__file__).parents[1]
CANDIDATE_PATH = (
    ROOT / "docs/governance/proposals/adaptive_shadow_v1_policy_candidate.json"
)
DECISION_PATH = (
    ROOT
    / "docs/governance/decision_records/adaptive_shadow_v1_owner_approval_20260818.json"
)
READINESS_PATH = (
    ROOT / "docs/baselines/adaptive_shadow_v1_activation_readiness_20260818.json"
)
REGISTRY_PATH = ROOT / "config/research/strategy_registry.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _missing() -> dict[str, None]:
    return {role: None for role in REQUIRED_GOVERNED_INPUTS}


def _registry_hash() -> str:
    return hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()


def test_owner_decision_binds_exact_candidate_and_shadow_only_terms() -> None:
    candidate = validate_candidate(_json(CANDIDATE_PATH))
    decision = validate_owner_decision(_json(DECISION_PATH), candidate=candidate)

    assert candidate["content_hash"] == APPROVED_CANDIDATE_HASH
    assert decision == build_owner_decision(
        candidate=candidate, decision_date="2026-08-18"
    )
    assert decision["approved_scope"] == "ADAPTIVE_SHADOW_OBSERVATION_ONLY"
    assert decision["approved_eligible_sleeves"] == [
        "caerus_lyra",
        "caerus_polaris",
    ]
    assert decision["authority"]["paper_lane_eligible"] is False
    assert decision["authority"]["live_lane_eligible"] is False
    assert decision["authority"]["execution_authority"] is False


def test_committed_activation_is_reproducible_blocked_static_polaris() -> None:
    candidate = _json(CANDIDATE_PATH)
    decision = _json(DECISION_PATH)
    committed = validate_activation_readiness(_json(READINESS_PATH))
    rebuilt = build_activation_readiness(
        candidate=candidate,
        owner_decision=decision,
        registry_hash=_registry_hash(),
        observed_at="2026-08-19T00:23:11Z",
        enable_requested=True,
        governed_input_hashes=_missing(),
    )

    assert committed == rebuilt
    assert committed["readiness_status"] == "BLOCKED_STATIC_POLARIS_FALLBACK"
    assert committed["fallback"]["modeled_sleeve_weights"] == {
        "caerus_lyra": 0.0,
        "caerus_polaris": 1.0,
    }
    assert committed["adaptive_evidence_emitted"] is False
    assert committed["produces_portfolio_target"] is False
    assert committed["execution_authority"] is False


def test_disabled_and_ready_states_never_gain_execution_or_promotion_authority() -> None:
    inputs = {
        "candidate": _json(CANDIDATE_PATH),
        "owner_decision": _json(DECISION_PATH),
        "registry_hash": _registry_hash(),
        "observed_at": "2026-08-19T00:23:11Z",
    }
    disabled = build_activation_readiness(
        enable_requested=False,
        governed_input_hashes=_missing(),
        **inputs,
    )
    ready = build_activation_readiness(
        enable_requested=True,
        governed_input_hashes={role: hashlib.sha256(role.encode()).hexdigest() for role in REQUIRED_GOVERNED_INPUTS},
        **inputs,
    )

    assert disabled["readiness_status"] == "DISABLED"
    assert ready["readiness_status"] == "READY_FOR_ADAPTIVE_EVIDENCE_RUN"
    assert ready["fallback"]["status"] == "ACTIVE_UNTIL_ADAPTIVE_EVIDENCE_IS_SEALED"
    for result in (disabled, ready):
        assert result["adaptive_evidence_emitted"] is False
        assert result["paper_lane_eligible"] is False
        assert result["live_lane_eligible"] is False
        assert result["automatic_promotion_enabled"] is False
        assert result["executable_target"] is False


def test_resealed_candidate_decision_or_readiness_authority_tamper_fails_closed() -> None:
    candidate = _json(CANDIDATE_PATH)
    candidate["initial_allocation"]["caerus_lyra"] = 0.6
    candidate["content_hash"] = content_hash(candidate)
    with pytest.raises(AdaptiveShadowActivationError, match="owner-approved hash"):
        validate_candidate(candidate)

    candidate = _json(CANDIDATE_PATH)
    decision = _json(DECISION_PATH)
    decision["authority"]["paper_lane_eligible"] = True
    decision["content_hash"] = content_hash(decision)
    with pytest.raises(AdaptiveShadowActivationError, match="overclaims"):
        validate_owner_decision(decision, candidate=candidate)

    readiness = _json(READINESS_PATH)
    readiness["execution_authority"] = True
    readiness["content_hash"] = content_hash(readiness)
    with pytest.raises(AdaptiveShadowActivationError, match="overclaims"):
        validate_activation_readiness(readiness)


def test_cli_enabled_missing_inputs_prints_exact_blocked_artifact(capsys) -> None:
    result = main(
        [
            "--candidate",
            str(CANDIDATE_PATH),
            "--owner-decision",
            str(DECISION_PATH),
            "--registry",
            str(REGISTRY_PATH),
            "--observed-at",
            "2026-08-19T00:23:11Z",
            "--enable-shadow-observation",
        ]
    )
    observed = json.loads(capsys.readouterr().out)

    assert result == 2
    assert observed == _json(READINESS_PATH)
    assert observed["readiness_status"] == "BLOCKED_STATIC_POLARIS_FALLBACK"
