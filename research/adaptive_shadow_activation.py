"""Owner-gated activation boundary for Adaptive Shadow v1 observation.

The contract activates only an advisory Shadow observation.  It cannot emit a
security target, order, PAPER/LIVE allocation, promotion, or execution
authority.  Missing governed inputs deterministically select the immutable
static-Polaris comparison fallback.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from typing import Any, Mapping


CANDIDATE_SCHEMA = "caerus.adaptive_shadow_owner_policy_candidate.v1"
OWNER_DECISION_SCHEMA = "caerus.adaptive_shadow_owner_decision.v1"
ACTIVATION_SCHEMA = "caerus.adaptive_shadow_activation_readiness.v1"
APPROVED_CANDIDATE_HASH = (
    "0ee486a14972fe1c3a16c19d5f275c7dafc6d1c06405bc4790d088d85749d46e"
)
REQUIRED_GOVERNED_INPUTS = (
    "SHADOW_DEPLOYMENT_MEMBERSHIP",
    "DECISION_BATCH_V2",
    "POLARIS_CAUSAL_SIGNAL",
    "LYRA_CAUSAL_SIGNAL",
    "READINESS_HISTORY_60_VALID_20_GREEN",
    "CAPACITY_LIQUIDITY_OVERLAP_CONSTRAINT_EVIDENCE",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_id",
        "status",
        "owner_directive_hash",
        "registry_revision",
        "registry_hash",
        "lane_kind",
        "eligible_set_rule",
        "initial_allocation",
        "objective",
        "constraints",
        "evidence_gate",
        "fallback",
        "authority",
        "content_hash",
    }
)
_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "decision_id",
        "decision_date",
        "decision_status",
        "owner_role",
        "approved_candidate_id",
        "approved_candidate_hash",
        "approved_scope",
        "approved_eligible_sleeves",
        "readiness_gates_remain_binding",
        "fallback_action",
        "authority",
        "source_record",
        "content_hash",
    }
)
_ACTIVATION_FIELDS = frozenset(
    {
        "schema_version",
        "activation_id",
        "observed_at",
        "enable_requested",
        "observation_scope",
        "owner_decision_hash",
        "candidate_hash",
        "registry_revision",
        "registry_hash",
        "eligible_sleeves_if_ready",
        "input_inventory",
        "readiness_status",
        "blockers",
        "reason_codes",
        "fallback",
        "adaptive_evidence_emitted",
        "adaptive_evidence_hash",
        "artifact_kind",
        "lane_kind",
        "produces_portfolio_target",
        "executable_target",
        "paper_lane_eligible",
        "live_lane_eligible",
        "automatic_promotion_enabled",
        "paper_authority_changed",
        "live_authority_changed",
        "execution_authority",
        "activation_authority",
        "approval_authority",
        "source_hashes",
        "content_hash",
    }
)


class AdaptiveShadowActivationError(ValueError):
    """Raised when Adaptive Shadow observation authority or evidence is invalid."""


def canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AdaptiveShadowActivationError(
            f"payload is not canonical JSON: {exc}"
        ) from exc


def content_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _strict_fields(
    payload: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    if set(payload) != expected:
        raise AdaptiveShadowActivationError(f"{label} fields are not exact")


def _sha(value: Any, *, label: str) -> str:
    result = str(value or "")
    if not _SHA256.fullmatch(result):
        raise AdaptiveShadowActivationError(f"{label} must be a lowercase SHA-256")
    return result


def _timestamp(value: Any, *, label: str) -> str:
    result = str(value or "")
    try:
        parsed = dt.datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdaptiveShadowActivationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdaptiveShadowActivationError(f"{label} must include a timezone")
    return result


def validate_candidate(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AdaptiveShadowActivationError("candidate must be an object")
    _strict_fields(payload, _CANDIDATE_FIELDS, label="candidate")
    if payload["schema_version"] != CANDIDATE_SCHEMA:
        raise AdaptiveShadowActivationError("candidate schema differs")
    if payload["content_hash"] != APPROVED_CANDIDATE_HASH:
        raise AdaptiveShadowActivationError("candidate is not the owner-approved hash")
    if content_hash(payload) != APPROVED_CANDIDATE_HASH:
        raise AdaptiveShadowActivationError("candidate content_hash mismatch")
    if payload["status"] != "PENDING_OWNER_APPROVAL":
        raise AdaptiveShadowActivationError(
            "immutable proposal status changed; approval belongs in the decision record"
        )
    if payload["lane_kind"] != "SHADOW":
        raise AdaptiveShadowActivationError("candidate is not Shadow-only")
    if payload["eligible_set_rule"]["expected_initial_set_if_all_gates_pass"] != [
        "caerus_lyra",
        "caerus_polaris",
    ]:
        raise AdaptiveShadowActivationError("candidate eligible set differs")
    if payload["initial_allocation"] != {
        "caerus_lyra": 0.5,
        "caerus_polaris": 0.5,
    }:
        raise AdaptiveShadowActivationError("candidate initial allocation differs")
    if payload["fallback"]["action"] != "HOLD_STATIC_POLARIS_SHADOW_BASELINE":
        raise AdaptiveShadowActivationError("candidate fallback differs")
    if payload["authority"] != {
        "automatic_promotion_enabled": False,
        "paper_live_eligible": False,
        "produces_executable_target": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
    }:
        raise AdaptiveShadowActivationError("candidate authority differs")
    if (
        payload["eligible_set_rule"]["minimum_valid_sessions"] != 60
        or payload["eligible_set_rule"]["minimum_consecutive_green_sessions"] != 20
        or payload["evidence_gate"][
            "minimum_completed_adaptive_shadow_sessions_before_owner_review"
        ]
        != 20
    ):
        raise AdaptiveShadowActivationError("candidate readiness gates differ")
    return json.loads(canonical_json(payload))


def build_owner_decision(
    *, candidate: Mapping[str, Any], decision_date: str
) -> dict[str, Any]:
    approved = validate_candidate(candidate)
    try:
        dt.date.fromisoformat(decision_date)
    except ValueError as exc:
        raise AdaptiveShadowActivationError("decision_date must be an ISO date") from exc
    body = {
        "schema_version": OWNER_DECISION_SCHEMA,
        "decision_id": "owner-decision:adaptive-shadow-v1:2026-08-18",
        "decision_date": decision_date,
        "decision_status": "APPROVED_FOR_SHADOW_OBSERVATION_ONLY",
        "owner_role": "CAERUS_CIO_AND_PRODUCT_OWNER",
        "approved_candidate_id": approved["candidate_id"],
        "approved_candidate_hash": approved["content_hash"],
        "approved_scope": "ADAPTIVE_SHADOW_OBSERVATION_ONLY",
        "approved_eligible_sleeves": ["caerus_lyra", "caerus_polaris"],
        "readiness_gates_remain_binding": True,
        "fallback_action": "HOLD_STATIC_POLARIS_SHADOW_BASELINE",
        "authority": {
            "automatic_promotion_enabled": False,
            "paper_lane_eligible": False,
            "live_lane_eligible": False,
            "produces_executable_target": False,
            "execution_authority": False,
            "activation_authority": False,
            "approval_authority_beyond_this_shadow_observation": False,
        },
        "source_record": "OWNER_EXPLICIT_DIRECTIVE_IN_ORCHESTRATED_SESSION",
    }
    body["content_hash"] = content_hash(body)
    return validate_owner_decision(body, candidate=approved)


def validate_owner_decision(
    payload: Mapping[str, Any], *, candidate: Mapping[str, Any]
) -> dict[str, Any]:
    approved = validate_candidate(candidate)
    if not isinstance(payload, Mapping):
        raise AdaptiveShadowActivationError("owner decision must be an object")
    _strict_fields(payload, _DECISION_FIELDS, label="owner decision")
    if payload["schema_version"] != OWNER_DECISION_SCHEMA:
        raise AdaptiveShadowActivationError("owner decision schema differs")
    if (
        payload["decision_id"] != "owner-decision:adaptive-shadow-v1:2026-08-18"
        or payload["decision_date"] != "2026-08-18"
        or payload["owner_role"] != "CAERUS_CIO_AND_PRODUCT_OWNER"
        or payload["source_record"]
        != "OWNER_EXPLICIT_DIRECTIVE_IN_ORCHESTRATED_SESSION"
    ):
        raise AdaptiveShadowActivationError("owner decision identity differs")
    if payload["decision_status"] != "APPROVED_FOR_SHADOW_OBSERVATION_ONLY":
        raise AdaptiveShadowActivationError("owner decision scope differs")
    if payload["approved_candidate_hash"] != approved["content_hash"]:
        raise AdaptiveShadowActivationError("owner decision candidate hash differs")
    if payload["approved_candidate_id"] != approved["candidate_id"]:
        raise AdaptiveShadowActivationError("owner decision candidate id differs")
    if payload["approved_scope"] != "ADAPTIVE_SHADOW_OBSERVATION_ONLY":
        raise AdaptiveShadowActivationError("owner decision is not Shadow-only")
    if payload["approved_eligible_sleeves"] != ["caerus_lyra", "caerus_polaris"]:
        raise AdaptiveShadowActivationError("owner decision eligible set differs")
    if payload["readiness_gates_remain_binding"] is not True:
        raise AdaptiveShadowActivationError("owner decision weakened readiness gates")
    if payload["fallback_action"] != "HOLD_STATIC_POLARIS_SHADOW_BASELINE":
        raise AdaptiveShadowActivationError("owner decision fallback differs")
    if payload["authority"] != {
        "automatic_promotion_enabled": False,
        "paper_lane_eligible": False,
        "live_lane_eligible": False,
        "produces_executable_target": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority_beyond_this_shadow_observation": False,
    }:
        raise AdaptiveShadowActivationError("owner decision overclaims authority")
    if payload["content_hash"] != content_hash(payload):
        raise AdaptiveShadowActivationError("owner decision content_hash mismatch")
    return json.loads(canonical_json(payload))


def build_activation_readiness(
    *,
    candidate: Mapping[str, Any],
    owner_decision: Mapping[str, Any],
    registry_hash: str,
    observed_at: str,
    enable_requested: bool,
    governed_input_hashes: Mapping[str, str | None],
) -> dict[str, Any]:
    """Seal activation readiness and fail closed to static Polaris."""

    approved = validate_candidate(candidate)
    decision = validate_owner_decision(owner_decision, candidate=approved)
    if type(enable_requested) is not bool:
        raise AdaptiveShadowActivationError("enable_requested must be a literal boolean")
    if not isinstance(governed_input_hashes, Mapping) or set(
        governed_input_hashes
    ) != set(REQUIRED_GOVERNED_INPUTS):
        raise AdaptiveShadowActivationError(
            "governed_input_hashes must exactly cover required inputs"
        )
    observed = _timestamp(observed_at, label="observed_at")
    registry = _sha(registry_hash, label="registry_hash")
    if registry != approved["registry_hash"]:
        raise AdaptiveShadowActivationError("registry hash differs from approved candidate")
    inventory: list[dict[str, Any]] = []
    blockers: list[str] = []
    for role in REQUIRED_GOVERNED_INPUTS:
        value = governed_input_hashes[role]
        if value is None:
            status = "MISSING"
            blockers.append(f"MISSING_{role}")
        else:
            value = _sha(value, label=role)
            status = "AVAILABLE"
        inventory.append({"input_role": role, "status": status, "content_hash": value})
    if not enable_requested:
        readiness = "DISABLED"
        reasons = ["OBSERVATION_ENABLE_NOT_REQUESTED"]
        fallback_status = "NOT_INVOKED"
    elif blockers:
        readiness = "BLOCKED_STATIC_POLARIS_FALLBACK"
        reasons = sorted(blockers)
        fallback_status = "ACTIVE_MODELED_CONTROL"
    else:
        readiness = "READY_FOR_ADAPTIVE_EVIDENCE_RUN"
        reasons = ["ALL_PREREGISTERED_INPUT_GATES_AVAILABLE"]
        fallback_status = "ACTIVE_UNTIL_ADAPTIVE_EVIDENCE_IS_SEALED"
    source_hashes = sorted(
        {
            decision["content_hash"],
            approved["content_hash"],
            registry,
            *[str(value) for value in governed_input_hashes.values() if value],
        }
    )
    seed = content_hash(
        {
            "observed_at": observed,
            "owner_decision_hash": decision["content_hash"],
            "candidate_hash": approved["content_hash"],
            "input_inventory": inventory,
            "enable_requested": enable_requested,
        }
    )
    body = {
        "schema_version": ACTIVATION_SCHEMA,
        "activation_id": f"adaptive-shadow-activation:{seed[:24]}",
        "observed_at": observed,
        "enable_requested": enable_requested,
        "observation_scope": "ADAPTIVE_SHADOW_OBSERVATION_ONLY",
        "owner_decision_hash": decision["content_hash"],
        "candidate_hash": approved["content_hash"],
        "registry_revision": approved["registry_revision"],
        "registry_hash": registry,
        "eligible_sleeves_if_ready": ["caerus_lyra", "caerus_polaris"],
        "input_inventory": inventory,
        "readiness_status": readiness,
        "blockers": sorted(blockers),
        "reason_codes": reasons,
        "fallback": {
            "action": "HOLD_STATIC_POLARIS_SHADOW_BASELINE",
            "status": fallback_status,
            "modeled_sleeve_weights": {
                "caerus_lyra": 0.0,
                "caerus_polaris": 1.0,
            },
            "automatic_recovery": False,
            "produces_executable_target": False,
        },
        "adaptive_evidence_emitted": False,
        "adaptive_evidence_hash": None,
        "artifact_kind": "ADAPTIVE_SHADOW_OBSERVATION_READINESS",
        "lane_kind": "SHADOW",
        "produces_portfolio_target": False,
        "executable_target": False,
        "paper_lane_eligible": False,
        "live_lane_eligible": False,
        "automatic_promotion_enabled": False,
        "paper_authority_changed": False,
        "live_authority_changed": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
        "source_hashes": source_hashes,
    }
    body["content_hash"] = content_hash(body)
    return validate_activation_readiness(body)


def validate_activation_readiness(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AdaptiveShadowActivationError("activation readiness must be an object")
    _strict_fields(payload, _ACTIVATION_FIELDS, label="activation readiness")
    if payload["schema_version"] != ACTIVATION_SCHEMA:
        raise AdaptiveShadowActivationError("activation readiness schema differs")
    _timestamp(payload["observed_at"], label="observed_at")
    owner_hash = _sha(payload["owner_decision_hash"], label="owner_decision_hash")
    candidate_hash = _sha(payload["candidate_hash"], label="candidate_hash")
    registry_hash = _sha(payload["registry_hash"], label="registry_hash")
    if candidate_hash != APPROVED_CANDIDATE_HASH:
        raise AdaptiveShadowActivationError("activation candidate hash differs")
    if payload["registry_revision"] != (
        "1b397d004b4d75bbcc1a7efb0e1b2ad55613fdac"
    ):
        raise AdaptiveShadowActivationError("activation registry revision differs")
    if payload["eligible_sleeves_if_ready"] != [
        "caerus_lyra",
        "caerus_polaris",
    ]:
        raise AdaptiveShadowActivationError("activation eligible sleeves differ")
    if payload["artifact_kind"] != "ADAPTIVE_SHADOW_OBSERVATION_READINESS":
        raise AdaptiveShadowActivationError("activation artifact kind differs")
    if type(payload["enable_requested"]) is not bool:
        raise AdaptiveShadowActivationError("enable_requested must be boolean")
    inventory = payload["input_inventory"]
    if not isinstance(inventory, list) or [
        row.get("input_role") for row in inventory if isinstance(row, Mapping)
    ] != list(REQUIRED_GOVERNED_INPUTS):
        raise AdaptiveShadowActivationError("input inventory roles differ")
    missing = []
    for row in inventory:
        if not isinstance(row, Mapping) or set(row) != {
            "input_role",
            "status",
            "content_hash",
        }:
            raise AdaptiveShadowActivationError("input inventory row differs")
        if row["content_hash"] is None:
            if row["status"] != "MISSING":
                raise AdaptiveShadowActivationError("missing input status differs")
            missing.append(f"MISSING_{row['input_role']}")
        else:
            _sha(row["content_hash"], label=row["input_role"])
            if row["status"] != "AVAILABLE":
                raise AdaptiveShadowActivationError("available input status differs")
    if payload["blockers"] != sorted(missing):
        raise AdaptiveShadowActivationError("activation blockers differ")
    expected_status = (
        "DISABLED"
        if payload["enable_requested"] is False
        else "BLOCKED_STATIC_POLARIS_FALLBACK"
        if missing
        else "READY_FOR_ADAPTIVE_EVIDENCE_RUN"
    )
    if payload["readiness_status"] != expected_status:
        raise AdaptiveShadowActivationError("activation readiness status differs")
    expected_reasons = (
        ["OBSERVATION_ENABLE_NOT_REQUESTED"]
        if expected_status == "DISABLED"
        else sorted(missing)
        if expected_status == "BLOCKED_STATIC_POLARIS_FALLBACK"
        else ["ALL_PREREGISTERED_INPUT_GATES_AVAILABLE"]
    )
    if payload["reason_codes"] != expected_reasons:
        raise AdaptiveShadowActivationError("activation reason codes differ")
    if not isinstance(payload["fallback"], Mapping) or set(payload["fallback"]) != {
        "action",
        "status",
        "modeled_sleeve_weights",
        "automatic_recovery",
        "produces_executable_target",
    }:
        raise AdaptiveShadowActivationError("activation fallback fields differ")
    if payload["fallback"]["action"] != "HOLD_STATIC_POLARIS_SHADOW_BASELINE":
        raise AdaptiveShadowActivationError("activation fallback differs")
    if payload["fallback"]["modeled_sleeve_weights"] != {
        "caerus_lyra": 0.0,
        "caerus_polaris": 1.0,
    }:
        raise AdaptiveShadowActivationError("activation fallback weights differ")
    expected_fallback_status = (
        "NOT_INVOKED"
        if expected_status == "DISABLED"
        else "ACTIVE_MODELED_CONTROL"
        if expected_status == "BLOCKED_STATIC_POLARIS_FALLBACK"
        else "ACTIVE_UNTIL_ADAPTIVE_EVIDENCE_IS_SEALED"
    )
    if (
        payload["fallback"]["status"] != expected_fallback_status
        or payload["fallback"]["automatic_recovery"] is not False
        or payload["fallback"]["produces_executable_target"] is not False
    ):
        raise AdaptiveShadowActivationError("activation fallback status differs")
    forbidden_true = (
        "produces_portfolio_target",
        "executable_target",
        "paper_lane_eligible",
        "live_lane_eligible",
        "automatic_promotion_enabled",
        "paper_authority_changed",
        "live_authority_changed",
        "execution_authority",
        "activation_authority",
        "approval_authority",
        "adaptive_evidence_emitted",
    )
    if any(payload[field] is not False for field in forbidden_true):
        raise AdaptiveShadowActivationError("activation overclaims authority or evidence")
    if payload["adaptive_evidence_hash"] is not None:
        raise AdaptiveShadowActivationError("readiness cannot claim adaptive evidence")
    if payload["lane_kind"] != "SHADOW" or payload["observation_scope"] != (
        "ADAPTIVE_SHADOW_OBSERVATION_ONLY"
    ):
        raise AdaptiveShadowActivationError("activation is not Shadow-only")
    sources = payload["source_hashes"]
    if not isinstance(sources, list) or sources != sorted(set(sources)):
        raise AdaptiveShadowActivationError("source_hashes must be sorted and unique")
    for value in sources:
        _sha(value, label="source_hash")
    expected_sources = sorted(
        {
            owner_hash,
            candidate_hash,
            registry_hash,
            *[
                str(row["content_hash"])
                for row in inventory
                if row["content_hash"] is not None
            ],
        }
    )
    if sources != expected_sources:
        raise AdaptiveShadowActivationError("activation source hashes differ")
    expected_seed = content_hash(
        {
            "observed_at": payload["observed_at"],
            "owner_decision_hash": owner_hash,
            "candidate_hash": candidate_hash,
            "input_inventory": inventory,
            "enable_requested": payload["enable_requested"],
        }
    )
    if payload["activation_id"] != f"adaptive-shadow-activation:{expected_seed[:24]}":
        raise AdaptiveShadowActivationError("activation identity differs")
    if payload["content_hash"] != content_hash(payload):
        raise AdaptiveShadowActivationError("activation content_hash mismatch")
    return json.loads(canonical_json(payload))


__all__ = [
    "ACTIVATION_SCHEMA",
    "APPROVED_CANDIDATE_HASH",
    "AdaptiveShadowActivationError",
    "CANDIDATE_SCHEMA",
    "OWNER_DECISION_SCHEMA",
    "REQUIRED_GOVERNED_INPUTS",
    "build_activation_readiness",
    "build_owner_decision",
    "canonical_json",
    "content_hash",
    "validate_activation_readiness",
    "validate_candidate",
    "validate_owner_decision",
]
