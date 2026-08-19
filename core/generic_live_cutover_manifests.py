"""Immutable, non-authoritative manifests for generic Live policy cutover."""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json
from core.lane_environment_adapter import (
    validate_generic_live_cutover_preflight,
    validate_lane_environment_binding,
)


DEPLOYMENT_REPLACEMENT_TEMPLATE_SCHEMA = "caerus.generic_live_deployment_replacement_template.v1"
ROLLBACK_MANIFEST_SCHEMA = "caerus.generic_live_rollback_manifest.v1"
PREFLIGHT_MANIFEST_SCHEMA = "caerus.generic_live_preflight_manifest.v1"
_REPLACEMENT_FIELDS = frozenset(
    {
        "schema_version", "template_id", "generated_at", "status",
        "cutover_preflight_hash", "owner_decision_id", "owner_decision_hash",
        "lane_id", "lane_kind", "account_id_hash", "broker_environment",
        "environment_binding_hash", "required_policy_schema", "required_candidate_status",
        "required_effective_session", "required_rollback_deployment_version",
        "legacy_registry_reference", "legacy_registry_hash", "legacy_registry_live_enabled",
        "legacy_registry_mutation_allowed", "sleeve_selection_allowed",
        "paper_policy_mutation_allowed", "execution_authority", "activation_authority",
        "approval_authority", "content_hash",
    }
)
_ROLLBACK_FIELDS = frozenset(
    {
        "schema_version", "manifest_id", "created_at", "status", "lane_id",
        "deployment_template_hash", "prior_deployment_version", "prior_deployment_hash",
        "candidate_deployment_version", "candidate_deployment_hash",
        "rollback_deployment_version", "rollback_triggers", "rollback_action",
        "kill_switch_must_remain_engaged", "legacy_executor_must_remain_disabled",
        "configuration_mutated", "execution_authority", "activation_authority", "content_hash",
    }
)
_PREFLIGHT_FIELDS = frozenset(
    {
        "schema_version", "manifest_id", "created_at", "status", "lane_id",
        "cutover_preflight_hash", "deployment_template_hash", "rollback_manifest_hash",
        "required_external_action", "active_config_changed", "schedule_changed",
        "kill_switch_disengaged", "broker_call_performed", "execution_authority",
        "activation_authority", "content_hash",
    }
)


class GenericLiveManifestError(ValueError):
    """Raised when generic Live cutover lineage is incomplete or mutable."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _seal(body: dict[str, Any]) -> dict[str, Any]:
    body["content_hash"] = _hash(body)
    return body


def build_deployment_policy_replacement_template(
    *, cutover_preflight: Mapping[str, Any], environment_binding: Mapping[str, Any], generated_at: str
) -> dict[str, Any]:
    preflight = validate_generic_live_cutover_preflight(cutover_preflight)
    binding = validate_lane_environment_binding(environment_binding)
    if preflight["live_binding_hash"] != binding["content_hash"] or binding["lane_kind"] != "LIVE":
        raise GenericLiveManifestError("replacement template scope differs from Live binding")
    return validate_deployment_policy_replacement_template(
        _seal(
            {
                "schema_version": DEPLOYMENT_REPLACEMENT_TEMPLATE_SCHEMA,
                "template_id": f"generic-live-policy-template:{binding['lane_id']}:{preflight['content_hash'][:24]}",
                "generated_at": generated_at,
                "status": "TEMPLATE_ONLY_NOT_ACTIVE",
                "cutover_preflight_hash": preflight["content_hash"],
                "owner_decision_id": preflight["owner_decision_id"],
                "owner_decision_hash": preflight["owner_decision_hash"],
                "lane_id": binding["lane_id"],
                "lane_kind": "LIVE",
                "account_id_hash": binding["account_id_hash"],
                "broker_environment": binding["broker_environment"],
                "environment_binding_hash": binding["content_hash"],
                "required_policy_schema": "caerus.lane_deployment_policy.v1",
                "required_candidate_status": "PENDING",
                "required_effective_session": preflight["effective_session"],
                "required_rollback_deployment_version": preflight["rollback_deployment_version"],
                "legacy_registry_reference": preflight["read_only_inventory"]["strategy_registry_path"],
                "legacy_registry_hash": preflight["read_only_inventory"]["strategy_registry_hash"],
                "legacy_registry_live_enabled": preflight["read_only_inventory"]["registry_live_enabled"],
                "legacy_registry_mutation_allowed": False,
                "sleeve_selection_allowed": False,
                "paper_policy_mutation_allowed": False,
                "execution_authority": False,
                "activation_authority": False,
                "approval_authority": False,
            }
        )
    )


def validate_deployment_policy_replacement_template(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _REPLACEMENT_FIELDS:
        raise GenericLiveManifestError("deployment replacement template fields are invalid")
    if payload.get("schema_version") != DEPLOYMENT_REPLACEMENT_TEMPLATE_SCHEMA:
        raise GenericLiveManifestError("unsupported deployment replacement template")
    if payload.get("status") != "TEMPLATE_ONLY_NOT_ACTIVE" or payload.get("required_candidate_status") != "PENDING":
        raise GenericLiveManifestError("replacement template cannot be active")
    for field in (
        "legacy_registry_mutation_allowed", "sleeve_selection_allowed",
        "paper_policy_mutation_allowed", "execution_authority", "activation_authority",
        "approval_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLiveManifestError(f"replacement template flag must remain false: {field}")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveManifestError("replacement template content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_generic_live_rollback_manifest(
    *, deployment_template: Mapping[str, Any], prior_deployment_version: str,
    prior_deployment_hash: str, candidate_deployment_version: str,
    candidate_deployment_hash: str, created_at: str,
) -> dict[str, Any]:
    template = validate_deployment_policy_replacement_template(deployment_template)
    body = _seal(
        {
            "schema_version": ROLLBACK_MANIFEST_SCHEMA,
            "manifest_id": f"generic-live-rollback:{template['lane_id']}:{candidate_deployment_hash[:24]}",
            "created_at": created_at,
            "status": "ADVISORY_ROLLBACK_TEMPLATE",
            "lane_id": template["lane_id"],
            "deployment_template_hash": template["content_hash"],
            "prior_deployment_version": prior_deployment_version,
            "prior_deployment_hash": prior_deployment_hash,
            "candidate_deployment_version": candidate_deployment_version,
            "candidate_deployment_hash": candidate_deployment_hash,
            "rollback_deployment_version": template["required_rollback_deployment_version"],
            "rollback_triggers": [
                "ACCOUNT_PIN_MISMATCH", "DEPLOYMENT_SHA_MISMATCH", "RECONCILIATION_NOT_PASS",
                "ACCOUNTING_LINEAGE_NOT_GREEN", "OWNER_ROLLBACK_DECISION",
            ],
            "rollback_action": "EXTERNAL_OWNER_APPROVED_POLICY_TRANSITION_REQUIRED",
            "kill_switch_must_remain_engaged": True,
            "legacy_executor_must_remain_disabled": True,
            "configuration_mutated": False,
            "execution_authority": False,
            "activation_authority": False,
        }
    )
    return validate_generic_live_rollback_manifest(body)


def validate_generic_live_rollback_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _ROLLBACK_FIELDS:
        raise GenericLiveManifestError("rollback manifest fields are invalid")
    if payload.get("schema_version") != ROLLBACK_MANIFEST_SCHEMA:
        raise GenericLiveManifestError("unsupported rollback manifest")
    if payload.get("status") != "ADVISORY_ROLLBACK_TEMPLATE":
        raise GenericLiveManifestError("rollback manifest status is invalid")
    if payload.get("kill_switch_must_remain_engaged") is not True or payload.get("legacy_executor_must_remain_disabled") is not True:
        raise GenericLiveManifestError("rollback safety requirements are invalid")
    for field in ("configuration_mutated", "execution_authority", "activation_authority"):
        if payload.get(field) is not False:
            raise GenericLiveManifestError("rollback manifest cannot mutate or authorize")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveManifestError("rollback manifest content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_generic_live_preflight_manifest(
    *, cutover_preflight: Mapping[str, Any], deployment_template: Mapping[str, Any],
    rollback_manifest: Mapping[str, Any], created_at: str,
) -> dict[str, Any]:
    preflight = validate_generic_live_cutover_preflight(cutover_preflight)
    template = validate_deployment_policy_replacement_template(deployment_template)
    rollback = validate_generic_live_rollback_manifest(rollback_manifest)
    if preflight["status"] != "READY_FOR_SEPARATE_ACTIVATION":
        raise GenericLiveManifestError("blocked cutover cannot produce a preflight manifest")
    if template["cutover_preflight_hash"] != preflight["content_hash"] or rollback["deployment_template_hash"] != template["content_hash"]:
        raise GenericLiveManifestError("preflight/rollback/template lineage differs")
    return validate_generic_live_preflight_manifest(
        _seal(
            {
                "schema_version": PREFLIGHT_MANIFEST_SCHEMA,
                "manifest_id": f"generic-live-preflight:{template['lane_id']}:{rollback['content_hash'][:24]}",
                "created_at": created_at,
                "status": "READY_FOR_OWNER_POLICY_COMPILATION",
                "lane_id": template["lane_id"],
                "cutover_preflight_hash": preflight["content_hash"],
                "deployment_template_hash": template["content_hash"],
                "rollback_manifest_hash": rollback["content_hash"],
                "required_external_action": "OWNER_COMPILE_AND_APPROVE_PENDING_DEPLOYMENT_POLICY",
                "active_config_changed": False,
                "schedule_changed": False,
                "kill_switch_disengaged": False,
                "broker_call_performed": False,
                "execution_authority": False,
                "activation_authority": False,
            }
        )
    )


def validate_generic_live_preflight_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _PREFLIGHT_FIELDS:
        raise GenericLiveManifestError("preflight manifest fields are invalid")
    if payload.get("schema_version") != PREFLIGHT_MANIFEST_SCHEMA:
        raise GenericLiveManifestError("unsupported preflight manifest")
    if payload.get("status") != "READY_FOR_OWNER_POLICY_COMPILATION":
        raise GenericLiveManifestError("preflight manifest status is invalid")
    for field in (
        "active_config_changed", "schedule_changed", "kill_switch_disengaged",
        "broker_call_performed", "execution_authority", "activation_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLiveManifestError("preflight manifest cannot mutate or authorize")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveManifestError("preflight manifest content_hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [name for name in globals() if name.startswith(("build_", "validate_"))] + [
    "DEPLOYMENT_REPLACEMENT_TEMPLATE_SCHEMA", "ROLLBACK_MANIFEST_SCHEMA",
    "PREFLIGHT_MANIFEST_SCHEMA", "GenericLiveManifestError",
]
