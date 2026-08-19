"""Redacted, account-pinned generic Live candidate configuration.

All inputs are explicit read-only evidence.  The resulting candidate and
preflight are immutable advisory artifacts: submission, execution, scheduling,
activation, and configuration mutation are structurally false.  No raw account
identifier or credential is accepted or persisted.
"""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json
from core.generic_paper_live_rehearsal import validate_generic_paper_live_rehearsal


REDACTED_LIVE_ACCOUNT_OBSERVATION_SCHEMA = "caerus.redacted_live_account_observation.v1"
GENERIC_LIVE_CANDIDATE_CONFIG_SCHEMA = "caerus.generic_live_candidate_config.v1"
GENERIC_LIVE_CANDIDATE_PREFLIGHT_SCHEMA = "caerus.generic_live_candidate_preflight.v1"

_SHA = frozenset("0123456789abcdef")
_OBSERVATION_FIELDS = frozenset(
    {
        "schema_version", "observed_at", "environment", "endpoint",
        "account_id_hash", "equity", "cash", "status", "trading_blocked",
        "account_blocked", "source_response_hash", "credentials_printed",
        "raw_account_id_printed", "request_method", "write_performed", "content_hash",
    }
)
_CONFIG_FIELDS = frozenset(
    {
        "schema_version", "candidate_id", "created_at", "status", "lane_id",
        "lane_kind", "deployment_version", "broker_environment", "account_id_hash",
        "account_pin_source_hash", "owner_capital_ceiling_usd",
        "observed_broker_equity_usd", "effective_capital_ceiling_usd",
        "minimum_trade_usd", "maximum_order_count", "maximum_gross_fraction",
        "whole_share_only", "long_only", "leverage_allowed", "shorting_allowed",
        "kill_switch_required_state", "owner_approval_required",
        "submission_approval_required", "schedule_approval_required",
        "candidate_execution_enabled", "candidate_submission_enabled",
        "candidate_schedule_enabled", "broker_read_performed", "broker_write_performed",
        "active_config_mutated", "active_schedule_mutated", "kill_switch_mutated",
        "legacy_live_executor_enabled", "secrets_persisted", "raw_account_id_persisted",
        "execution_authority", "activation_authority", "approval_authority",
        "source_hashes", "content_hash",
    }
)
_PREFLIGHT_FIELDS = frozenset(
    {
        "schema_version", "preflight_id", "evaluated_at", "status", "reason_codes",
        "candidate_config_hash", "vm_inventory_hash", "live_observation_hash",
        "staging_commit", "staging_evidence_hash", "lane_id", "lane_kind",
        "account_id_hash", "effective_capital_ceiling_usd", "observed_live_equity_usd",
        "account_pin_match", "legacy_live_disabled", "kill_switch_engaged",
        "generic_active_schedule_present", "generic_active_checkout_contains_candidate",
        "generic_staging_present", "paper_live_adapter_parity_proven",
        "paper_live_rehearsal_evidence_hash", "paper_live_rehearsal_artifact_hash",
        "owner_approval_recorded", "submission_approval_recorded",
        "schedule_approval_recorded", "active_account_pin_configured",
        "active_capital_ceiling_configured", "active_max_orders_configured",
        "candidate_execution_enabled", "candidate_submission_enabled",
        "candidate_schedule_enabled", "broker_write_performed", "active_config_mutated",
        "active_schedule_mutated", "kill_switch_mutated", "legacy_live_executor_enabled",
        "execution_authority", "activation_authority", "approval_authority", "content_hash",
    }
)


class GenericLiveCandidateError(ValueError):
    """Raised when a Live candidate cannot be safely grounded in evidence."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["content_hash"] = _hash(payload)
    return payload


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _SHA for c in value):
        raise GenericLiveCandidateError(f"{label} must be a lowercase SHA-256 hash")
    return value


def _git_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(c not in _SHA for c in value):
        raise GenericLiveCandidateError(f"{label} must be a lowercase full Git SHA")
    return value


def _text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GenericLiveCandidateError(f"{label} must be a non-blank string")
    forbidden = ("api_key", "secret_key", "account_number", "apca-api")
    if any(marker in value.lower() for marker in forbidden):
        raise GenericLiveCandidateError(f"{label} contains prohibited credential/account material")
    return value


def _number(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise GenericLiveCandidateError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GenericLiveCandidateError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise GenericLiveCandidateError(f"{label} must be finite and {'positive' if positive else 'non-negative'}")
    return result


def validate_redacted_live_account_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _OBSERVATION_FIELDS:
        raise GenericLiveCandidateError("redacted Live account observation fields are invalid")
    if payload.get("schema_version") != REDACTED_LIVE_ACCOUNT_OBSERVATION_SCHEMA:
        raise GenericLiveCandidateError("unsupported redacted Live account observation")
    if payload.get("environment") != "LIVE" or payload.get("endpoint") != "GET /v2/account":
        raise GenericLiveCandidateError("account observation must prove the Live read-only endpoint")
    if payload.get("request_method") != "GET":
        raise GenericLiveCandidateError("account observation must use GET")
    _text(payload.get("observed_at"), label="observed_at")
    _sha(payload.get("account_id_hash"), label="account_id_hash")
    _sha(payload.get("source_response_hash"), label="source_response_hash")
    _number(payload.get("equity"), label="equity", positive=True)
    _number(payload.get("cash"), label="cash")
    _text(payload.get("status"), label="status")
    for field in ("trading_blocked", "account_blocked"):
        if type(payload.get(field)) is not bool:
            raise GenericLiveCandidateError(f"{field} must be boolean")
    for field in ("credentials_printed", "raw_account_id_printed", "write_performed"):
        if payload.get(field) is not False:
            raise GenericLiveCandidateError(f"redacted observation {field} must remain false")
    if _sha(payload.get("content_hash"), label="content_hash") != _hash(payload):
        raise GenericLiveCandidateError("redacted account observation content_hash mismatch")
    return copy.deepcopy(dict(payload))


def validate_vm_generic_live_preflight_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the minimal immutable/safety subset of the prior VM inventory."""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != "caerus.vm_generic_live_preflight.v1":
        raise GenericLiveCandidateError("unsupported VM generic Live inventory")
    declared = _sha(payload.get("content_hash"), label="vm_inventory.content_hash")
    if declared != _hash(payload):
        raise GenericLiveCandidateError("VM inventory content_hash mismatch")
    for field in (
        "read_only", "secrets_redacted",
    ):
        if payload.get(field) is not True:
            raise GenericLiveCandidateError(f"VM inventory {field} must be true")
    for field in (
        "broker_write_performed", "configuration_mutated", "schedule_mutated",
        "kill_switch_mutated", "remote_files_written", "execution_authority",
        "activation_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLiveCandidateError(f"VM inventory {field} must remain false")
    proof = payload.get("legacy_live_disabled_proof")
    environment = payload.get("live_environment_redacted")
    schedule = payload.get("schedule_redacted")
    deployment = payload.get("deployment")
    if not all(isinstance(row, Mapping) for row in (proof, environment, schedule, deployment)):
        raise GenericLiveCandidateError("VM inventory safety sections are missing")
    if (
        proof.get("registry_live_enabled") is not False
        or proof.get("cron_structural_disable_present") is not True
        or proof.get("executor_structural_disable_present") is not True
    ):
        raise GenericLiveCandidateError("legacy Live is not proven structurally disabled")
    if environment.get("kill_switch_state") != "ARMED":
        raise GenericLiveCandidateError("Live kill switch is not proven ARMED")
    if schedule.get("generic_entry_count") != 0:
        raise GenericLiveCandidateError("active generic Live schedule unexpectedly exists")
    _git_sha(deployment.get("head_sha"), label="deployment.head_sha")
    return copy.deepcopy(dict(payload))


def build_generic_live_candidate_config(
    *, vm_inventory: Mapping[str, Any], live_account_observation: Mapping[str, Any],
    created_at: str, lane_id: str, deployment_version: str,
    owner_capital_ceiling_usd: float = 460.0, minimum_trade_usd: float = 100.0,
    maximum_order_count: int = 1, maximum_gross_fraction: float = 0.95,
) -> dict[str, Any]:
    inventory = validate_vm_generic_live_preflight_evidence(vm_inventory)
    observation = validate_redacted_live_account_observation(live_account_observation)
    if observation["status"].upper() != "ACTIVE":
        raise GenericLiveCandidateError("Live broker account is not ACTIVE")
    if observation["trading_blocked"] or observation["account_blocked"]:
        raise GenericLiveCandidateError("Live broker account is blocked")
    owner_ceiling = _number(owner_capital_ceiling_usd, label="owner_capital_ceiling_usd", positive=True)
    observed_equity = _number(observation["equity"], label="observed broker equity", positive=True)
    minimum_trade = _number(minimum_trade_usd, label="minimum_trade_usd", positive=True)
    gross = _number(maximum_gross_fraction, label="maximum_gross_fraction", positive=True)
    if gross > 0.95:
        raise GenericLiveCandidateError("maximum_gross_fraction cannot exceed stricter shared limit 0.95")
    if minimum_trade < 100.0:
        raise GenericLiveCandidateError("minimum_trade_usd cannot weaken the legacy $100 floor")
    if type(maximum_order_count) is not int or maximum_order_count != 1:
        raise GenericLiveCandidateError("maximum_order_count must retain the legacy limit of 1")
    effective = min(owner_ceiling, observed_equity)
    source_hashes = sorted([inventory["content_hash"], observation["content_hash"]])
    body = {
        "schema_version": GENERIC_LIVE_CANDIDATE_CONFIG_SCHEMA,
        "candidate_id": "pending",
        "created_at": _text(created_at, label="created_at"),
        "status": "VALIDATED_DISABLED_CANDIDATE",
        "lane_id": _text(lane_id, label="lane_id"),
        "lane_kind": "LIVE",
        "deployment_version": _text(deployment_version, label="deployment_version"),
        "broker_environment": "LIVE",
        "account_id_hash": observation["account_id_hash"],
        "account_pin_source_hash": observation["content_hash"],
        "owner_capital_ceiling_usd": owner_ceiling,
        "observed_broker_equity_usd": observed_equity,
        "effective_capital_ceiling_usd": effective,
        "minimum_trade_usd": minimum_trade,
        "maximum_order_count": maximum_order_count,
        "maximum_gross_fraction": gross,
        "whole_share_only": True,
        "long_only": True,
        "leverage_allowed": False,
        "shorting_allowed": False,
        "kill_switch_required_state": "ARMED",
        "owner_approval_required": True,
        "submission_approval_required": True,
        "schedule_approval_required": True,
        "candidate_execution_enabled": False,
        "candidate_submission_enabled": False,
        "candidate_schedule_enabled": False,
        "broker_read_performed": True,
        "broker_write_performed": False,
        "active_config_mutated": False,
        "active_schedule_mutated": False,
        "kill_switch_mutated": False,
        "legacy_live_executor_enabled": False,
        "secrets_persisted": False,
        "raw_account_id_persisted": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
        "source_hashes": source_hashes,
    }
    seed = _hash(body)
    body["candidate_id"] = f"generic-live-candidate:{observation['account_id_hash'][:16]}:{seed[:16]}"
    body["content_hash"] = _hash(body)
    return validate_generic_live_candidate_config(body)


def validate_generic_live_candidate_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _CONFIG_FIELDS:
        raise GenericLiveCandidateError("generic Live candidate config fields are invalid")
    if payload.get("schema_version") != GENERIC_LIVE_CANDIDATE_CONFIG_SCHEMA:
        raise GenericLiveCandidateError("unsupported generic Live candidate config")
    if payload.get("status") != "VALIDATED_DISABLED_CANDIDATE" or payload.get("lane_kind") != "LIVE":
        raise GenericLiveCandidateError("candidate must remain a disabled LIVE candidate")
    if payload.get("broker_environment") != "LIVE" or payload.get("kill_switch_required_state") != "ARMED":
        raise GenericLiveCandidateError("candidate Live environment gates are invalid")
    for field in ("candidate_id", "created_at", "lane_id", "deployment_version"):
        _text(payload.get(field), label=field)
    for field in ("account_id_hash", "account_pin_source_hash", "content_hash"):
        _sha(payload.get(field), label=field)
    hashes = payload.get("source_hashes")
    if not isinstance(hashes, list) or hashes != sorted(set(hashes)) or len(hashes) != 2:
        raise GenericLiveCandidateError("candidate source_hashes must bind inventory and account observation")
    for value in hashes:
        _sha(value, label="source_hash")
    owner = _number(payload.get("owner_capital_ceiling_usd"), label="owner ceiling", positive=True)
    equity = _number(payload.get("observed_broker_equity_usd"), label="observed equity", positive=True)
    effective = _number(payload.get("effective_capital_ceiling_usd"), label="effective ceiling", positive=True)
    if effective != min(owner, equity):
        raise GenericLiveCandidateError("effective ceiling must be min(owner ceiling, observed Live equity)")
    minimum_trade = _number(payload.get("minimum_trade_usd"), label="minimum_trade_usd", positive=True)
    if minimum_trade < 100.0:
        raise GenericLiveCandidateError("minimum_trade_usd weakens the legacy strict floor")
    if type(payload.get("maximum_order_count")) is not int or payload["maximum_order_count"] != 1:
        raise GenericLiveCandidateError("maximum_order_count differs from the legacy strict limit")
    gross = _number(payload.get("maximum_gross_fraction"), label="maximum_gross_fraction", positive=True)
    if gross > 0.95:
        raise GenericLiveCandidateError("maximum_gross_fraction exceeds the shared strict limit")
    for field in (
        "whole_share_only", "long_only", "owner_approval_required",
        "submission_approval_required", "schedule_approval_required", "broker_read_performed",
    ):
        if payload.get(field) is not True:
            raise GenericLiveCandidateError(f"candidate {field} must be true")
    for field in (
        "leverage_allowed", "shorting_allowed", "candidate_execution_enabled",
        "candidate_submission_enabled", "candidate_schedule_enabled", "broker_write_performed",
        "active_config_mutated", "active_schedule_mutated", "kill_switch_mutated",
        "legacy_live_executor_enabled", "secrets_persisted", "raw_account_id_persisted",
        "execution_authority", "activation_authority", "approval_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLiveCandidateError(f"candidate {field} must remain false")
    if payload["account_pin_source_hash"] not in hashes:
        raise GenericLiveCandidateError("account pin source is not bound to candidate sources")
    if payload["content_hash"] != _hash(payload):
        raise GenericLiveCandidateError("generic Live candidate content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_generic_live_candidate_preflight(
    *, candidate_config: Mapping[str, Any], vm_inventory: Mapping[str, Any],
    live_account_observation: Mapping[str, Any], evaluated_at: str,
    staging_commit: str, staging_evidence_hash: str,
    generic_staging_present: bool = True,
    paper_live_rehearsal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = validate_generic_live_candidate_config(candidate_config)
    inventory = validate_vm_generic_live_preflight_evidence(vm_inventory)
    observation = validate_redacted_live_account_observation(live_account_observation)
    _git_sha(staging_commit, label="staging_commit")
    _sha(staging_evidence_hash, label="staging_evidence_hash")
    if type(generic_staging_present) is not bool:
        raise GenericLiveCandidateError("generic_staging_present must be a literal boolean")
    rehearsal = (
        validate_generic_paper_live_rehearsal(paper_live_rehearsal)
        if paper_live_rehearsal is not None
        else None
    )
    if candidate["source_hashes"] != sorted([inventory["content_hash"], observation["content_hash"]]):
        raise GenericLiveCandidateError("candidate source evidence differs from preflight inputs")
    account_match = candidate["account_id_hash"] == observation["account_id_hash"]
    environment = inventory["live_environment_redacted"]
    schedule = inventory["schedule_redacted"]
    proof = inventory["legacy_live_disabled_proof"]
    blockers: list[str] = []
    facts = {
        "account_pin_match": account_match,
        "legacy_live_disabled": bool(
            proof["registry_live_enabled"] is False
            and proof["cron_structural_disable_present"] is True
            and proof["executor_structural_disable_present"] is True
        ),
        "kill_switch_engaged": environment["kill_switch_state"] == "ARMED",
        "generic_active_schedule_present": schedule["generic_entry_count"] > 0,
        "generic_active_checkout_contains_candidate": False,
        "generic_staging_present": generic_staging_present,
        "paper_live_adapter_parity_proven": rehearsal is not None,
        "owner_approval_recorded": environment["owner_approval_state"] == "APPROVED",
        "submission_approval_recorded": environment["submit_approval_state"] == "APPROVED",
        "schedule_approval_recorded": environment["schedule_enabled_state"] == "ENABLED",
        "active_account_pin_configured": environment["account_pin_configured"] is True,
        "active_capital_ceiling_configured": environment["capital_ceiling_configured"] is True,
        "active_max_orders_configured": environment["max_orders_configured"] is True,
    }
    requirements = {
        "ACCOUNT_PIN_MISMATCH": facts["account_pin_match"],
        "LEGACY_LIVE_NOT_DISABLED": facts["legacy_live_disabled"],
        "KILL_SWITCH_NOT_ENGAGED": facts["kill_switch_engaged"],
        "GENERIC_LIVE_NOT_IN_ACTIVE_CHECKOUT": facts["generic_active_checkout_contains_candidate"],
        "GENERIC_LIVE_ACTIVE_SCHEDULE_ABSENT": facts["generic_active_schedule_present"],
        "GENERIC_STAGING_ABSENT": facts["generic_staging_present"],
        "PAPER_LIVE_ADAPTER_PARITY_NOT_PROVEN": facts["paper_live_adapter_parity_proven"],
        "OWNER_APPROVAL_NOT_RECORDED": facts["owner_approval_recorded"],
        "SUBMISSION_APPROVAL_NOT_RECORDED": facts["submission_approval_recorded"],
        "SCHEDULE_APPROVAL_NOT_RECORDED": facts["schedule_approval_recorded"],
        "ACTIVE_ACCOUNT_PIN_NOT_CONFIGURED": facts["active_account_pin_configured"],
        "ACTIVE_CAPITAL_CEILING_NOT_CONFIGURED": facts["active_capital_ceiling_configured"],
        "ACTIVE_MAX_ORDERS_NOT_CONFIGURED": facts["active_max_orders_configured"],
    }
    blockers = sorted(code for code, passed in requirements.items() if not passed)
    body = {
        "schema_version": GENERIC_LIVE_CANDIDATE_PREFLIGHT_SCHEMA,
        "preflight_id": "pending",
        "evaluated_at": _text(evaluated_at, label="evaluated_at"),
        "status": "BLOCKED" if blockers else "READY_FOR_SEPARATE_OWNER_ACTIVATION",
        "reason_codes": blockers or ["ALL_GENERIC_LIVE_CUTOVER_GATES_PROVEN"],
        "candidate_config_hash": candidate["content_hash"],
        "vm_inventory_hash": inventory["content_hash"],
        "live_observation_hash": observation["content_hash"],
        "staging_commit": staging_commit,
        "staging_evidence_hash": staging_evidence_hash,
        "paper_live_rehearsal_evidence_hash": rehearsal["content_hash"] if rehearsal else None,
        "paper_live_rehearsal_artifact_hash": rehearsal["source_artifact_hash"] if rehearsal else None,
        "lane_id": candidate["lane_id"],
        "lane_kind": "LIVE",
        "account_id_hash": candidate["account_id_hash"],
        "effective_capital_ceiling_usd": candidate["effective_capital_ceiling_usd"],
        "observed_live_equity_usd": candidate["observed_broker_equity_usd"],
        **facts,
        "candidate_execution_enabled": False,
        "candidate_submission_enabled": False,
        "candidate_schedule_enabled": False,
        "broker_write_performed": False,
        "active_config_mutated": False,
        "active_schedule_mutated": False,
        "kill_switch_mutated": False,
        "legacy_live_executor_enabled": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
    }
    seed = _hash(body)
    body["preflight_id"] = f"generic-live-candidate-preflight:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_generic_live_candidate_preflight(body)


def validate_generic_live_candidate_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _PREFLIGHT_FIELDS:
        raise GenericLiveCandidateError("generic Live candidate preflight fields are invalid")
    if payload.get("schema_version") != GENERIC_LIVE_CANDIDATE_PREFLIGHT_SCHEMA:
        raise GenericLiveCandidateError("unsupported generic Live candidate preflight")
    if payload.get("status") not in {"BLOCKED", "READY_FOR_SEPARATE_OWNER_ACTIVATION"}:
        raise GenericLiveCandidateError("candidate preflight status is invalid")
    if payload.get("lane_kind") != "LIVE":
        raise GenericLiveCandidateError("candidate preflight must bind LIVE")
    for field in (
        "candidate_config_hash", "vm_inventory_hash", "live_observation_hash",
        "staging_evidence_hash", "account_id_hash", "content_hash",
    ):
        _sha(payload.get(field), label=field)
    for field in ("paper_live_rehearsal_evidence_hash", "paper_live_rehearsal_artifact_hash"):
        value = payload.get(field)
        if value is not None:
            _sha(value, label=field)
    if payload.get("paper_live_adapter_parity_proven") is not (
        payload.get("paper_live_rehearsal_evidence_hash") is not None
        and payload.get("paper_live_rehearsal_artifact_hash") is not None
    ):
        raise GenericLiveCandidateError("PAPER/LIVE parity must be bound to rehearsal evidence and source artifact")
    _git_sha(payload.get("staging_commit"), label="staging_commit")
    reasons = payload.get("reason_codes")
    if not isinstance(reasons, list) or not reasons or reasons != sorted(set(reasons)):
        raise GenericLiveCandidateError("preflight reason_codes must be sorted and unique")
    fact_fields = set(_PREFLIGHT_FIELDS) - {
        "schema_version", "preflight_id", "evaluated_at", "status", "reason_codes",
        "candidate_config_hash", "vm_inventory_hash", "live_observation_hash",
        "staging_commit", "staging_evidence_hash", "paper_live_rehearsal_evidence_hash",
        "paper_live_rehearsal_artifact_hash", "lane_id", "lane_kind",
        "account_id_hash", "effective_capital_ceiling_usd", "observed_live_equity_usd",
        "content_hash",
    }
    for field in fact_fields:
        if type(payload.get(field)) is not bool:
            raise GenericLiveCandidateError(f"preflight {field} must be boolean")
    for field in (
        "candidate_execution_enabled", "candidate_submission_enabled",
        "candidate_schedule_enabled", "broker_write_performed", "active_config_mutated",
        "active_schedule_mutated", "kill_switch_mutated", "legacy_live_executor_enabled",
        "execution_authority", "activation_authority", "approval_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLiveCandidateError(f"preflight {field} must remain false")
    if payload["status"] == "READY_FOR_SEPARATE_OWNER_ACTIVATION" and payload["reason_codes"] != ["ALL_GENERIC_LIVE_CUTOVER_GATES_PROVEN"]:
        raise GenericLiveCandidateError("ready preflight reason code is invalid")
    if payload["status"] == "BLOCKED" and payload["reason_codes"] == ["ALL_GENERIC_LIVE_CUTOVER_GATES_PROVEN"]:
        raise GenericLiveCandidateError("blocked preflight requires precise blockers")
    if payload["content_hash"] != _hash(payload):
        raise GenericLiveCandidateError("generic Live candidate preflight content_hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "REDACTED_LIVE_ACCOUNT_OBSERVATION_SCHEMA", "GENERIC_LIVE_CANDIDATE_CONFIG_SCHEMA",
    "GENERIC_LIVE_CANDIDATE_PREFLIGHT_SCHEMA", "GenericLiveCandidateError",
    "validate_redacted_live_account_observation", "validate_vm_generic_live_preflight_evidence",
    "build_generic_live_candidate_config", "validate_generic_live_candidate_config",
    "build_generic_live_candidate_preflight", "validate_generic_live_candidate_preflight",
]
