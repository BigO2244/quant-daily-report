"""Generic PAPER/LIVE environment bindings and advisory Live cutover proof.

This module contains no broker SDK, credential reader, executor, or active
configuration writer.  It binds explicit environment references to the same
v4 plan contract for PAPER and LIVE and can produce a non-authoritative cutover
candidate after safety, OMS, reconciliation, and accounting rehearsals pass.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.lane_accounting_dry_run import validate_lane_accounting_dry_run_result
from core.lane_execution_dry_run import (
    validate_lane_execution_dry_run_result,
    validate_lane_execution_safety_evidence,
)
from core.lane_reconciliation import validate_lane_reconciliation
from core.owner_decision import parse_owner_decision


LANE_ENVIRONMENT_BINDING_SCHEMA = "caerus.lane_environment_binding.v1"
LIVE_CUTOVER_INVENTORY_SCHEMA = "caerus.live_cutover_read_only_inventory.v1"
GENERIC_LIVE_CUTOVER_PREFLIGHT_SCHEMA = "caerus.generic_live_cutover_preflight.v1"
GENERIC_ADAPTER_CONTRACT = "CAERUS_GENERIC_LANE_V4"

REQUIRED_GATE_REFERENCES = frozenset(
    {
        "kill_switch", "owner_approval", "submission_approval", "account_pin",
        "deployment_sha", "open_orders", "leverage", "shorting", "capital_ceiling",
    }
)
REQUIRED_OWNER_PREFLIGHTS = frozenset(
    {
        "GENERIC_V4_PLAN", "SAME_PAPER_LIVE_ADAPTER", "KILL_SWITCH_ENGAGED",
        "ACCOUNT_PIN_MATCH", "DEPLOYMENT_SHA_MATCH", "OPEN_ORDERS_CLEAR",
        "LEVERAGE_DISABLED", "SHORTING_DISABLED", "CAPITAL_WITHIN_CEILING",
        "OMS_NO_WRITE_REHEARSAL", "RECONCILIATION_PASS_REHEARSAL",
        "ACCOUNTING_NO_WRITE_REHEARSAL", "ROLLBACK_DEFINED",
    }
)
LEGACY_REMOVAL_CANDIDATES = (
    {
        "reference": "scripts/cron_live_pilot_execute.sh#live_capital_disabled_by_owner_policy",
        "action": "REMOVE_STRUCTURAL_EARLY_EXIT_ONLY_IN_SEPARATE_OWNER_ACTIVATION",
    },
    {
        "reference": "scripts/live_pilot_execute.py#live_capital_disabled_by_owner_policy",
        "action": "DO_NOT_REMOVE_IN_LEGACY_EXECUTOR; RETIRE_EXECUTOR_AS_A_UNIT",
    },
    {
        "reference": "scripts/live_pilot_execute.py",
        "action": "REMOVE_AFTER_GENERIC_LIVE_OBSERVATION",
    },
    {
        "reference": "scripts/cron_live_pilot_execute.sh",
        "action": "REPLACE_WITH_THIN_GENERIC_ENVIRONMENT_WRAPPER",
    },
    {
        "reference": "scripts/run_monday_live_pilot.sh",
        "action": "REMOVE_AFTER_GENERIC_OWNER_WORKFLOW_EXISTS",
    },
    {
        "reference": "core/live_pilot_guardrails.py",
        "action": "RETIRE_ONLY_AFTER_GENERIC_GATES_HAVE_PARITY",
    },
    {
        "reference": "config/research/strategy_registry.json#/sleeve_control_plane/paper_allocation_policy/governance/live_enabled",
        "action": "SUPERSEDE_WITH_OWNER_APPROVED_LANE_DEPLOYMENT_POLICY; DO_NOT_FLIP_IN_PLACE",
    },
)
CURRENT_LIVE_CONFIG_REFERENCES = {
    "environment_file": "$HOME/.caerus/live_pilot.env",
    "kill_switch": "CAERUS_LIVE_PILOT_KILL_SWITCH",
    "owner_approval": "CAERUS_LIVE_PILOT_APPROVED",
    "submission_approval": "CAERUS_LIVE_PILOT_SUBMIT_APPROVED",
    "scheduled_approval": "CAERUS_LIVE_PILOT_CRON_APPROVED",
    "schedule_enabled": "CAERUS_LIVE_PILOT_SCHEDULE_ENABLED",
    "account_pin_legacy": "CAERUS_LIVE_PILOT_ACCOUNT_ID",
    "account_pin": "CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH",
    "capital_ceiling": "CAERUS_LIVE_PILOT_CAPITAL_CAP",
    "maximum_orders": "CAERUS_LIVE_PILOT_MAX_ORDERS",
}

_SHA = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_PREFLIGHT_GATES = frozenset(
    {
        "same_generic_path", "owner_authorization", "safety", "oms_rehearsal",
        "reconciliation_rehearsal", "accounting_rehearsal", "read_only_inventory",
    }
)
_EXTERNAL_ACTIONS = [
    "OWNER_ACTIVATE_GENERIC_LIVE_BINDING",
    "OPERATOR_MIGRATE_CONFIG_REFERENCES_WITH_KILL_SWITCH_ENGAGED",
    "OPERATOR_VERIFY_GENERIC_LIVE_READ_ONLY_OBSERVATION",
    "OWNER_AUTHORIZE_LEGACY_REMOVAL_AFTER_OBSERVATION",
]
_BINDING_FIELDS = frozenset(
    {
        "schema_version", "binding_id", "bound_at", "adapter_contract", "adapter_id",
        "adapter_version", "endpoint_class", "lane_id", "lane_kind",
        "deployment_version", "account_id_hash", "broker_environment", "plan_id",
        "plan_hash", "credential_reference_hash", "configuration_reference_hash",
        "gate_references", "capabilities", "read_only_preflight", "submission_enabled",
        "runtime_cutover_status", "legacy_live_executor_imported", "execution_authority", "activation_authority",
        "configuration_mutated", "content_hash",
    }
)
_PREFLIGHT_FIELDS = frozenset(
    {
        "schema_version", "preflight_id", "evaluated_at", "status", "reason_codes",
        "paper_binding_id", "paper_binding_hash", "live_binding_id", "live_binding_hash",
        "adapter_contract", "adapter_id", "adapter_version", "live_lane_id",
        "live_deployment_version", "live_account_id_hash", "live_plan_id", "live_plan_hash",
        "owner_decision_id", "owner_decision_hash", "owner_authorization_present",
        "owner_capital_ceiling", "effective_session", "rollback_deployment_version",
        "safety_evidence_hash", "execution_rehearsal_hash", "reconciliation_hash",
        "accounting_rehearsal_hash", "gate_results", "current_config_references",
        "read_only_inventory", "paper_runtime_status", "legacy_live_executor_status",
        "legacy_removal_candidates", "required_external_actions", "broker_call_performed",
        "broker_write_performed", "configuration_mutated", "execution_authority",
        "activation_authority", "approval_authority", "content_hash",
    }
)
_INVENTORY_FIELDS = frozenset(
    {
        "schema_version", "inventory_id", "observed_at", "repository_root_hash",
        "strategy_registry_path", "strategy_registry_hash", "registry_live_enabled",
        "cron_wrapper_path", "cron_wrapper_hash", "cron_structural_disable_present",
        "legacy_executor_path", "legacy_executor_hash", "executor_structural_disable_present",
        "legacy_guardrails_path", "legacy_guardrails_hash", "live_env_reference",
        "live_env_present", "live_env_hash", "live_gate_values", "legacy_executor_disabled",
        "paper_runtime_status", "read_only", "secrets_persisted", "configuration_mutated",
        "execution_authority", "activation_authority", "content_hash",
    }
)
_INVENTORIED_ENV_KEYS = (
    "CAERUS_LIVE_PILOT_APPROVED", "CAERUS_LIVE_PILOT_CAPITAL_CAP",
    "CAERUS_LIVE_PILOT_CRON_APPROVED", "CAERUS_LIVE_PILOT_KILL_SWITCH",
    "CAERUS_LIVE_PILOT_MAX_ORDERS", "CAERUS_LIVE_PILOT_SCHEDULE_ENABLED",
    "CAERUS_LIVE_PILOT_SUBMIT_APPROVED",
)


class LaneEnvironmentAdapterError(ValueError):
    """Raised when an environment binding or cutover proof fails closed."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _strict(payload: Mapping[str, Any], fields: frozenset[str], *, label: str) -> None:
    if set(payload) != fields:
        raise LaneEnvironmentAdapterError(
            f"{label} fields differ: missing={sorted(fields-set(payload))}, extra={sorted(set(payload)-fields)}"
        )


def _string(value: Any, *, label: str, safe: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneEnvironmentAdapterError(f"{label} must be a non-blank string")
    if safe and (not _SAFE.fullmatch(value) or ".." in value):
        raise LaneEnvironmentAdapterError(f"{label} is unsafe")
    return value


def _sha(value: Any, *, label: str) -> str:
    raw = _string(value, label=label)
    if not _SHA.fullmatch(raw):
        raise LaneEnvironmentAdapterError(f"{label} must be a lowercase SHA-256 hash")
    return raw


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    raw = _string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneEnvironmentAdapterError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise LaneEnvironmentAdapterError(f"{label} must include a timezone")
    return raw, parsed


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LaneEnvironmentAdapterError(f"cannot read inventory source {path}: {exc}") from exc


def build_live_cutover_read_only_inventory(
    *, repository_root: Path | str, live_env_path: Path | str, observed_at: str
) -> dict[str, Any]:
    """Read only the named gate sources; never persist credentials or other env values."""

    root = Path(repository_root).resolve()
    registry_path = root / "config/research/strategy_registry.json"
    cron_path = root / "scripts/cron_live_pilot_execute.sh"
    executor_path = root / "scripts/live_pilot_execute.py"
    guardrails_path = root / "core/live_pilot_guardrails.py"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        live_enabled = registry["sleeve_control_plane"]["paper_allocation_policy"]["governance"]["live_enabled"]
        cron_text = cron_path.read_text(encoding="utf-8")
        executor_text = executor_path.read_text(encoding="utf-8")
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise LaneEnvironmentAdapterError(f"cannot inventory structural Live gates: {exc}") from exc
    if type(live_enabled) is not bool:
        raise LaneEnvironmentAdapterError("registry live_enabled must be boolean")
    env_path = Path(live_env_path)
    env_values = {key: None for key in _INVENTORIED_ENV_KEYS}
    env_hash: str | None = None
    if env_path.is_file():
        env_hash = _file_hash(env_path)
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                candidate = line.strip()
                if not candidate or candidate.startswith("#") or "=" not in candidate:
                    continue
                key, value = candidate.split("=", 1)
                if key.strip() in env_values:
                    env_values[key.strip()] = value.strip().strip("\"'")
        except OSError as exc:
            raise LaneEnvironmentAdapterError(f"cannot read whitelisted Live gates: {exc}") from exc
    cron_disabled = "live_capital_disabled_by_owner_policy" in cron_text
    executor_disabled = "live_capital_disabled_by_owner_policy" in executor_text
    body = {
        "schema_version": LIVE_CUTOVER_INVENTORY_SCHEMA,
        "inventory_id": "pending",
        "observed_at": _timestamp(observed_at, label="observed_at")[0],
        "repository_root_hash": hashlib.sha256(str(root).encode()).hexdigest(),
        "strategy_registry_path": "config/research/strategy_registry.json",
        "strategy_registry_hash": _file_hash(registry_path),
        "registry_live_enabled": live_enabled,
        "cron_wrapper_path": "scripts/cron_live_pilot_execute.sh",
        "cron_wrapper_hash": _file_hash(cron_path),
        "cron_structural_disable_present": cron_disabled,
        "legacy_executor_path": "scripts/live_pilot_execute.py",
        "legacy_executor_hash": _file_hash(executor_path),
        "executor_structural_disable_present": executor_disabled,
        "legacy_guardrails_path": "core/live_pilot_guardrails.py",
        "legacy_guardrails_hash": _file_hash(guardrails_path),
        "live_env_reference": str(env_path),
        "live_env_present": env_path.is_file(),
        "live_env_hash": env_hash,
        "live_gate_values": env_values,
        "legacy_executor_disabled": bool(not live_enabled and cron_disabled and executor_disabled),
        "paper_runtime_status": "LEGACY_UNCHANGED_NOT_YET_CUT_OVER",
        "read_only": True,
        "secrets_persisted": False,
        "configuration_mutated": False,
        "execution_authority": False,
        "activation_authority": False,
    }
    seed = _hash(body)
    body["inventory_id"] = f"live-cutover-inventory:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_live_cutover_read_only_inventory(body)


def validate_live_cutover_read_only_inventory(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneEnvironmentAdapterError("Live cutover inventory must be an object")
    _strict(payload, _INVENTORY_FIELDS, label="Live cutover inventory")
    if payload["schema_version"] != LIVE_CUTOVER_INVENTORY_SCHEMA:
        raise LaneEnvironmentAdapterError("unsupported Live cutover inventory schema")
    _string(payload["inventory_id"], label="inventory_id", safe=True)
    _timestamp(payload["observed_at"], label="observed_at")
    for field in (
        "repository_root_hash", "strategy_registry_hash", "cron_wrapper_hash",
        "legacy_executor_hash", "legacy_guardrails_hash",
    ):
        _sha(payload[field], label=field)
    if payload["live_env_hash"] is not None:
        _sha(payload["live_env_hash"], label="live_env_hash")
    if not isinstance(payload["live_gate_values"], Mapping) or tuple(sorted(payload["live_gate_values"])) != tuple(sorted(_INVENTORIED_ENV_KEYS)):
        raise LaneEnvironmentAdapterError("inventoried Live gate values are incomplete")
    if any(value is not None and not isinstance(value, str) for value in payload["live_gate_values"].values()):
        raise LaneEnvironmentAdapterError("inventoried Live gate values must be strings or null")
    for field in (
        "registry_live_enabled", "cron_structural_disable_present",
        "executor_structural_disable_present", "live_env_present", "legacy_executor_disabled",
    ):
        if type(payload[field]) is not bool:
            raise LaneEnvironmentAdapterError(f"{field} must be boolean")
    expected_disabled = bool(
        payload["registry_live_enabled"] is False
        and payload["cron_structural_disable_present"] is True
        and payload["executor_structural_disable_present"] is True
    )
    if payload["legacy_executor_disabled"] is not expected_disabled:
        raise LaneEnvironmentAdapterError("legacy executor disabled proof mismatch")
    if payload["paper_runtime_status"] != "LEGACY_UNCHANGED_NOT_YET_CUT_OVER":
        raise LaneEnvironmentAdapterError("PAPER runtime status is invalid")
    expected_flags = {
        "read_only": True, "secrets_persisted": False, "configuration_mutated": False,
        "execution_authority": False, "activation_authority": False,
    }
    if any(payload[field] is not value for field, value in expected_flags.items()):
        raise LaneEnvironmentAdapterError("inventory authority flags are invalid")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneEnvironmentAdapterError("inventory content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_lane_environment_binding(
    *, exact_plan: Mapping[str, Any], adapter_descriptor: Mapping[str, Any], bound_at: str
) -> dict[str, Any]:
    """Bind one explicit environment descriptor to one advisory v4 plan."""

    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise LaneEnvironmentAdapterError("exact plan is invalid: " + ",".join(failures))
    expected_descriptor = {
        "adapter_id", "adapter_version", "endpoint_class", "broker_environment",
        "credential_reference_hash", "configuration_reference_hash", "gate_references",
    }
    if not isinstance(adapter_descriptor, Mapping) or set(adapter_descriptor) != expected_descriptor:
        raise LaneEnvironmentAdapterError("adapter descriptor fields are invalid")
    endpoint_class = adapter_descriptor["endpoint_class"]
    if endpoint_class != exact_plan["lane_kind"] or endpoint_class not in {"PAPER", "LIVE"}:
        raise LaneEnvironmentAdapterError("endpoint class must exactly match plan lane kind")
    if adapter_descriptor["broker_environment"] != exact_plan["broker_environment"]:
        raise LaneEnvironmentAdapterError("adapter broker environment differs from exact plan")
    gates = adapter_descriptor["gate_references"]
    if not isinstance(gates, Mapping) or set(gates) != REQUIRED_GATE_REFERENCES:
        raise LaneEnvironmentAdapterError("adapter gate references are incomplete")
    for key, value in gates.items():
        _string(value, label=f"gate reference {key}")
    body = {
        "schema_version": LANE_ENVIRONMENT_BINDING_SCHEMA,
        "binding_id": "pending",
        "bound_at": _timestamp(bound_at, label="bound_at")[0],
        "adapter_contract": GENERIC_ADAPTER_CONTRACT,
        "adapter_id": _string(adapter_descriptor["adapter_id"], label="adapter_id", safe=True),
        "adapter_version": _string(adapter_descriptor["adapter_version"], label="adapter_version", safe=True),
        "endpoint_class": endpoint_class,
        "lane_id": exact_plan["lane_id"],
        "lane_kind": exact_plan["lane_kind"],
        "deployment_version": exact_plan["deployment_version"],
        "account_id_hash": exact_plan["account_id_hash"],
        "broker_environment": exact_plan["broker_environment"],
        "plan_id": exact_plan["plan_id"],
        "plan_hash": exact_plan["content_hash"],
        "credential_reference_hash": _sha(adapter_descriptor["credential_reference_hash"], label="credential_reference_hash"),
        "configuration_reference_hash": _sha(adapter_descriptor["configuration_reference_hash"], label="configuration_reference_hash"),
        "gate_references": dict(sorted(gates.items())),
        "capabilities": [
            "ACCOUNTING_GATE", "EXACT_PLAN_V4", "OMS_ADVISORY_GATE",
            "READ_ONLY_BROKER_EVIDENCE", "RECONCILIATION_GATE", "SAFETY_GATE",
        ],
        "read_only_preflight": True,
        "submission_enabled": False,
        "runtime_cutover_status": "NOT_YET_CUT_OVER",
        "legacy_live_executor_imported": False,
        "execution_authority": False,
        "activation_authority": False,
        "configuration_mutated": False,
    }
    seed = _hash(body)
    body["binding_id"] = f"lane-environment:{body['lane_id']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lane_environment_binding(body, exact_plan=exact_plan)


def validate_lane_environment_binding(
    payload: Mapping[str, Any], *, exact_plan: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneEnvironmentAdapterError("environment binding must be an object")
    _strict(payload, _BINDING_FIELDS, label="environment binding")
    if payload["schema_version"] != LANE_ENVIRONMENT_BINDING_SCHEMA:
        raise LaneEnvironmentAdapterError("unsupported environment binding schema")
    if payload["adapter_contract"] != GENERIC_ADAPTER_CONTRACT:
        raise LaneEnvironmentAdapterError("binding is not on the generic v4 adapter contract")
    for field in ("binding_id", "adapter_id", "adapter_version", "lane_id", "deployment_version", "broker_environment", "plan_id"):
        _string(payload[field], label=field, safe=True)
    _timestamp(payload["bound_at"], label="bound_at")
    if payload["endpoint_class"] != payload["lane_kind"] or payload["lane_kind"] not in {"PAPER", "LIVE"}:
        raise LaneEnvironmentAdapterError("binding endpoint/lane kind mismatch")
    for field in ("account_id_hash", "plan_hash", "credential_reference_hash", "configuration_reference_hash"):
        _sha(payload[field], label=field)
    if not isinstance(payload["gate_references"], Mapping) or set(payload["gate_references"]) != REQUIRED_GATE_REFERENCES:
        raise LaneEnvironmentAdapterError("binding gate references are incomplete")
    for key, value in payload["gate_references"].items():
        _string(value, label=f"gate reference {key}")
    if payload["capabilities"] != [
        "ACCOUNTING_GATE", "EXACT_PLAN_V4", "OMS_ADVISORY_GATE",
        "READ_ONLY_BROKER_EVIDENCE", "RECONCILIATION_GATE", "SAFETY_GATE",
    ]:
        raise LaneEnvironmentAdapterError("binding capabilities are invalid")
    expected_flags = {
        "read_only_preflight": True, "submission_enabled": False,
        "legacy_live_executor_imported": False, "execution_authority": False,
        "activation_authority": False, "configuration_mutated": False,
    }
    if payload["runtime_cutover_status"] != "NOT_YET_CUT_OVER":
        raise LaneEnvironmentAdapterError("environment binding cannot claim runtime cutover")
    if any(payload[field] is not value for field, value in expected_flags.items()):
        raise LaneEnvironmentAdapterError("environment binding authority flags are invalid")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneEnvironmentAdapterError("environment binding content_hash mismatch")
    identity_body = dict(payload)
    identity_body["binding_id"] = "pending"
    identity_body.pop("content_hash", None)
    expected_binding_id = f"lane-environment:{payload['lane_id']}:{_hash(identity_body)[:24]}"
    if payload["binding_id"] != expected_binding_id:
        raise LaneEnvironmentAdapterError("environment binding identity mismatch")
    if exact_plan is not None:
        failures = validate_lane_exact_execution_plan(exact_plan)
        if failures:
            raise LaneEnvironmentAdapterError("exact plan is invalid: " + ",".join(failures))
        for field in ("lane_id", "lane_kind", "deployment_version", "account_id_hash", "broker_environment", "plan_id"):
            if payload[field] != exact_plan[field]:
                raise LaneEnvironmentAdapterError(f"environment binding differs from plan: {field}")
        if payload["plan_hash"] != exact_plan["content_hash"]:
            raise LaneEnvironmentAdapterError("environment binding differs from plan: plan_hash")
    return copy.deepcopy(dict(payload))


def build_generic_live_cutover_preflight(
    *, paper_exact_plan: Mapping[str, Any], paper_binding: Mapping[str, Any],
    live_exact_plan: Mapping[str, Any], live_binding: Mapping[str, Any],
    safety_evidence: Mapping[str, Any], execution_rehearsal: Mapping[str, Any],
    reconciliation: Mapping[str, Any], accounting_rehearsal: Mapping[str, Any],
    owner_decision: Mapping[str, Any], read_only_inventory: Mapping[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    """Build a read-only removal/cutover candidate; never activate or execute."""

    paper = validate_lane_environment_binding(paper_binding, exact_plan=paper_exact_plan)
    live = validate_lane_environment_binding(live_binding, exact_plan=live_exact_plan)
    if paper["lane_kind"] != "PAPER" or live["lane_kind"] != "LIVE":
        raise LaneEnvironmentAdapterError("same-path proof requires PAPER and LIVE bindings")
    for field in ("adapter_contract", "adapter_id", "adapter_version", "capabilities"):
        if paper[field] != live[field]:
            raise LaneEnvironmentAdapterError(f"PAPER and LIVE generic paths differ: {field}")
    safety = validate_lane_execution_safety_evidence(safety_evidence, exact_plan=live_exact_plan)
    execution = validate_lane_execution_dry_run_result(execution_rehearsal)
    recon = validate_lane_reconciliation(reconciliation, exact_plan=live_exact_plan)
    accounting = validate_lane_accounting_dry_run_result(accounting_rehearsal)
    decision = parse_owner_decision(owner_decision)
    inventory = validate_live_cutover_read_only_inventory(read_only_inventory)
    evaluated_raw, evaluated = _timestamp(evaluated_at, label="evaluated_at")

    blockers: list[str] = []
    if inventory["legacy_executor_disabled"] is not True:
        blockers.append("LEGACY_LIVE_EXECUTOR_NOT_PROVEN_DISABLED")
    if inventory["live_env_present"] is not True:
        blockers.append("LIVE_ENV_REFERENCE_ABSENT")
    kill_value = str(inventory["live_gate_values"]["CAERUS_LIVE_PILOT_KILL_SWITCH"] or "").lower()
    if kill_value not in {"1", "true", "yes", "y", "on"}:
        blockers.append("INVENTORIED_KILL_SWITCH_NOT_ENGAGED")
    if safety["kill_switch_state"] != "ENGAGED":
        blockers.append("KILL_SWITCH_NOT_ENGAGED_DURING_PREFLIGHT")
    for field, expected in {
        "account_pin_status": "MATCH", "deployment_sha_status": "MATCH",
        "open_order_status": "CLEAR", "leverage_status": "DISABLED",
        "shorting_status": "DISABLED", "capital_ceiling_status": "WITHIN_LIMIT",
        "credential_mode": "READ_ONLY_OR_NONE",
    }.items():
        if safety[field] != expected:
            blockers.append(f"SAFETY_{field.upper()}_{safety[field]}")
    if execution["status"] != "VALIDATED_NO_WRITE" or execution["write_enabled"] is not False:
        blockers.append("OMS_REHEARSAL_NOT_VALIDATED_NO_WRITE")
    if execution["plan_hash"] != live_exact_plan["content_hash"] or execution["safety_evidence_hash"] != safety["content_hash"]:
        raise LaneEnvironmentAdapterError("execution rehearsal scope differs from Live plan/safety")
    if recon["status"] != "PASS" or recon["accounting_ready"] is not True:
        blockers.append("RECONCILIATION_REHEARSAL_NOT_PASS")
    if accounting["status"] != "VALIDATED_NO_WRITE" or accounting["write_enabled"] is not False:
        blockers.append("ACCOUNTING_REHEARSAL_NOT_VALIDATED_NO_WRITE")
    for field, expected in {
        "plan_hash": live_exact_plan["content_hash"],
        "reconciliation_hash": recon["content_hash"],
        "lane_id": live_exact_plan["lane_id"],
        "deployment_version": live_exact_plan["deployment_version"],
        "account_id_hash": live_exact_plan["account_id_hash"],
    }.items():
        if accounting[field] != expected:
            raise LaneEnvironmentAdapterError(f"accounting rehearsal scope differs: {field}")
    if not decision.approved:
        blockers.append("OWNER_DECISION_NOT_APPROVE")
    _, expires = _timestamp(decision.expires_at, label="owner decision expires_at")
    if evaluated > expires:
        blockers.append("OWNER_DECISION_EXPIRED")
    if decision.effective_session != live_exact_plan["trade_date"]:
        blockers.append("OWNER_EFFECTIVE_SESSION_MISMATCH")
    if decision.capital_ceiling is None or decision.capital_ceiling + 1e-8 < float(live_exact_plan["deployable_capital"]):
        blockers.append("OWNER_CAPITAL_CEILING_EXCEEDED")
    if not REQUIRED_OWNER_PREFLIGHTS.issubset(set(decision.preflight_requirements)):
        blockers.append("OWNER_PREFLIGHT_REQUIREMENTS_INCOMPLETE")
    expected_patch = {
        "generic_lane_cutover": {
            "lane_id": live["lane_id"], "lane_kind": "LIVE",
            "deployment_version": live["deployment_version"],
            "adapter_binding_hash": live["content_hash"],
            "legacy_live_executor_import_allowed": False,
        }
    }
    if decision.to_dict()["approved_policy_patch"] != expected_patch:
        blockers.append("OWNER_APPROVED_PATCH_DOES_NOT_BIND_LIVE_ADAPTER")

    gate_results = {
        "same_generic_path": "PASS",
        "read_only_inventory": "PASS" if not any(code in blockers for code in (
            "LEGACY_LIVE_EXECUTOR_NOT_PROVEN_DISABLED", "LIVE_ENV_REFERENCE_ABSENT",
            "INVENTORIED_KILL_SWITCH_NOT_ENGAGED",
        )) else "BLOCK",
        "owner_authorization": "PASS" if not any(code.startswith("OWNER_") for code in blockers) else "BLOCK",
        "safety": "PASS" if not any(code.startswith(("SAFETY_", "KILL_SWITCH_")) for code in blockers) else "BLOCK",
        "oms_rehearsal": "PASS" if "OMS_REHEARSAL_NOT_VALIDATED_NO_WRITE" not in blockers else "BLOCK",
        "reconciliation_rehearsal": "PASS" if "RECONCILIATION_REHEARSAL_NOT_PASS" not in blockers else "BLOCK",
        "accounting_rehearsal": "PASS" if "ACCOUNTING_REHEARSAL_NOT_VALIDATED_NO_WRITE" not in blockers else "BLOCK",
    }
    status = "READY_FOR_SEPARATE_ACTIVATION" if not blockers else "BLOCKED"
    body = {
        "schema_version": GENERIC_LIVE_CUTOVER_PREFLIGHT_SCHEMA,
        "preflight_id": "pending", "evaluated_at": evaluated_raw, "status": status,
        "reason_codes": sorted(blockers) if blockers else ["GENERIC_LIVE_CUTOVER_PREFLIGHT_COMPLETE"],
        "paper_binding_id": paper["binding_id"], "paper_binding_hash": paper["content_hash"],
        "live_binding_id": live["binding_id"], "live_binding_hash": live["content_hash"],
        "adapter_contract": live["adapter_contract"], "adapter_id": live["adapter_id"],
        "adapter_version": live["adapter_version"], "live_lane_id": live["lane_id"],
        "live_deployment_version": live["deployment_version"],
        "live_account_id_hash": live["account_id_hash"], "live_plan_id": live["plan_id"],
        "live_plan_hash": live["plan_hash"], "owner_decision_id": decision.owner_decision_id,
        "owner_decision_hash": decision.content_hash, "owner_authorization_present": decision.approved,
        "owner_capital_ceiling": decision.capital_ceiling, "effective_session": decision.effective_session,
        "rollback_deployment_version": decision.rollback_deployment_version,
        "safety_evidence_hash": safety["content_hash"],
        "execution_rehearsal_hash": execution["content_hash"],
        "reconciliation_hash": recon["content_hash"],
        "accounting_rehearsal_hash": accounting["content_hash"], "gate_results": gate_results,
        "current_config_references": copy.deepcopy(CURRENT_LIVE_CONFIG_REFERENCES),
        "read_only_inventory": inventory,
        "paper_runtime_status": "LEGACY_UNCHANGED_NOT_YET_CUT_OVER",
        "legacy_live_executor_status": "DISABLED_UNCHANGED",
        "legacy_removal_candidates": copy.deepcopy(list(LEGACY_REMOVAL_CANDIDATES)),
        "required_external_actions": copy.deepcopy(_EXTERNAL_ACTIONS),
        "broker_call_performed": False, "broker_write_performed": False,
        "configuration_mutated": False, "execution_authority": False,
        "activation_authority": False, "approval_authority": False,
    }
    seed = _hash(body)
    body["preflight_id"] = f"generic-live-cutover:{live['lane_id']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_generic_live_cutover_preflight(body)


def validate_generic_live_cutover_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneEnvironmentAdapterError("cutover preflight must be an object")
    _strict(payload, _PREFLIGHT_FIELDS, label="cutover preflight")
    if payload["schema_version"] != GENERIC_LIVE_CUTOVER_PREFLIGHT_SCHEMA:
        raise LaneEnvironmentAdapterError("unsupported cutover preflight schema")
    if payload["status"] not in {"READY_FOR_SEPARATE_ACTIVATION", "BLOCKED"}:
        raise LaneEnvironmentAdapterError("cutover preflight status is invalid")
    if payload["adapter_contract"] != GENERIC_ADAPTER_CONTRACT:
        raise LaneEnvironmentAdapterError("cutover is not bound to generic v4 adapter")
    for field in ("preflight_id", "paper_binding_id", "live_binding_id", "adapter_id", "adapter_version", "live_lane_id", "live_deployment_version", "live_plan_id", "owner_decision_id", "rollback_deployment_version"):
        _string(payload[field], label=field, safe=True)
    _timestamp(payload["evaluated_at"], label="evaluated_at")
    for field in ("paper_binding_hash", "live_binding_hash", "live_account_id_hash", "live_plan_hash", "owner_decision_hash", "safety_evidence_hash", "execution_rehearsal_hash", "reconciliation_hash", "accounting_rehearsal_hash"):
        _sha(payload[field], label=field)
    if not isinstance(payload["reason_codes"], list) or not payload["reason_codes"]:
        raise LaneEnvironmentAdapterError("cutover reason_codes are required")
    if not isinstance(payload["gate_results"], Mapping) or set(payload["gate_results"]) != _PREFLIGHT_GATES or any(value not in {"PASS", "BLOCK"} for value in payload["gate_results"].values()):
        raise LaneEnvironmentAdapterError("cutover gate results are invalid")
    if (payload["status"] == "READY_FOR_SEPARATE_ACTIVATION") != all(value == "PASS" for value in payload["gate_results"].values()):
        raise LaneEnvironmentAdapterError("cutover status differs from gates")
    expected_false = ("broker_call_performed", "broker_write_performed", "configuration_mutated", "execution_authority", "activation_authority", "approval_authority")
    if any(payload[field] is not False for field in expected_false):
        raise LaneEnvironmentAdapterError("cutover preflight cannot mutate or grant authority")
    if payload["legacy_removal_candidates"] != list(LEGACY_REMOVAL_CANDIDATES):
        raise LaneEnvironmentAdapterError("legacy removal candidates differ")
    if payload["current_config_references"] != CURRENT_LIVE_CONFIG_REFERENCES:
        raise LaneEnvironmentAdapterError("current Live config references differ")
    validate_live_cutover_read_only_inventory(payload["read_only_inventory"])
    if payload["paper_runtime_status"] != "LEGACY_UNCHANGED_NOT_YET_CUT_OVER":
        raise LaneEnvironmentAdapterError("cutover cannot claim generic PAPER authority")
    if payload["legacy_live_executor_status"] != "DISABLED_UNCHANGED":
        raise LaneEnvironmentAdapterError("cutover must preserve legacy Live executor disabled")
    if payload["required_external_actions"] != _EXTERNAL_ACTIONS:
        raise LaneEnvironmentAdapterError("cutover external actions differ")
    if type(payload["owner_authorization_present"]) is not bool:
        raise LaneEnvironmentAdapterError("owner_authorization_present must be boolean")
    if isinstance(payload["owner_capital_ceiling"], bool) or not isinstance(
        payload["owner_capital_ceiling"], (int, float)
    ):
        raise LaneEnvironmentAdapterError("owner_capital_ceiling must be numeric")
    identity_body = dict(payload)
    identity_body["preflight_id"] = "pending"
    identity_body.pop("content_hash", None)
    expected_preflight_id = (
        f"generic-live-cutover:{payload['live_lane_id']}:{_hash(identity_body)[:24]}"
    )
    if payload["preflight_id"] != expected_preflight_id:
        raise LaneEnvironmentAdapterError("cutover preflight identity mismatch")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneEnvironmentAdapterError("cutover preflight content_hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "CURRENT_LIVE_CONFIG_REFERENCES", "GENERIC_ADAPTER_CONTRACT",
    "GENERIC_LIVE_CUTOVER_PREFLIGHT_SCHEMA", "LANE_ENVIRONMENT_BINDING_SCHEMA",
    "LIVE_CUTOVER_INVENTORY_SCHEMA",
    "LEGACY_REMOVAL_CANDIDATES", "REQUIRED_GATE_REFERENCES",
    "REQUIRED_OWNER_PREFLIGHTS", "LaneEnvironmentAdapterError",
    "build_generic_live_cutover_preflight", "build_lane_environment_binding",
    "build_live_cutover_read_only_inventory", "validate_generic_live_cutover_preflight",
    "validate_lane_environment_binding", "validate_live_cutover_read_only_inventory",
]
