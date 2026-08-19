"""Off-by-default scheduler boundary for the generic v4 lane path.

This is not a clock or an executor.  An external scheduler may invoke it with
explicit artifacts.  Even when enabled it can only call the submission-disabled
generic OMS rehearsal and can never activate PAPER, LIVE, or a deployment.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.lane_environment_adapter import (
    validate_generic_live_cutover_preflight,
    validate_lane_environment_binding,
)
from core.lane_execution_dry_run import (
    run_lane_execution_dry_run,
    validate_lane_execution_safety_evidence,
)


GENERIC_LANE_SCHEDULER_RESULT_SCHEMA = "caerus.generic_lane_scheduler_dry_run.v1"


class GenericLaneSchedulerError(ValueError):
    """Raised when the generic scheduler boundary cannot prove safety."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def run_generic_lane_scheduler_dry_run(
    *,
    exact_plan: Mapping[str, Any],
    environment_binding: Mapping[str, Any],
    safety_evidence: Mapping[str, Any],
    live_cutover_preflight: Mapping[str, Any] | None = None,
    scheduler_enabled: bool = False,
) -> dict[str, Any]:
    """Validate or rehearse the generic path; broker submission is impossible."""

    if type(scheduler_enabled) is not bool:
        raise GenericLaneSchedulerError("scheduler_enabled must be a literal boolean")
    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise GenericLaneSchedulerError("exact plan is invalid: " + ",".join(failures))
    binding = validate_lane_environment_binding(environment_binding, exact_plan=exact_plan)
    safety = validate_lane_execution_safety_evidence(safety_evidence, exact_plan=exact_plan)
    blockers: list[str] = []
    execution_result: dict[str, Any] | None = None
    preflight_hash: str | None = None
    if not scheduler_enabled:
        status = "DISABLED_NO_ACTION"
        blockers = ["ADVISORY_SCHEDULER_NOT_ENABLED"]
    elif exact_plan["lane_kind"] == "PAPER":
        status = "BLOCKED"
        blockers = ["GENERIC_PAPER_NOT_YET_CUT_OVER"]
    else:
        if live_cutover_preflight is None:
            raise GenericLaneSchedulerError("LIVE requires an explicit generic cutover preflight")
        preflight = validate_generic_live_cutover_preflight(live_cutover_preflight)
        preflight_hash = preflight["content_hash"]
        if preflight["status"] != "READY_FOR_SEPARATE_ACTIVATION":
            status = "BLOCKED"
            blockers = ["GENERIC_LIVE_CUTOVER_PREFLIGHT_NOT_READY"]
        elif (
            preflight["live_plan_hash"] != exact_plan["content_hash"]
            or preflight["live_binding_hash"] != binding["content_hash"]
        ):
            raise GenericLaneSchedulerError("Live preflight scope differs from plan/binding")
        else:
            execution_result = run_lane_execution_dry_run(
                exact_plan=exact_plan,
                safety_evidence=safety,
                write_enabled=False,
            )
            status = (
                "VALIDATED_NO_SUBMIT"
                if execution_result["status"] == "VALIDATED_NO_WRITE"
                else "BLOCKED"
            )
            blockers = [] if status == "VALIDATED_NO_SUBMIT" else ["GENERIC_EXECUTION_REHEARSAL_BLOCKED"]
    body = {
        "schema_version": GENERIC_LANE_SCHEDULER_RESULT_SCHEMA,
        "scheduler_result_id": "pending",
        "status": status,
        "reason_codes": blockers or ["GENERIC_V4_NO_SUBMIT_REHEARSAL_COMPLETE"],
        "scheduler_enabled": scheduler_enabled,
        "lane_id": exact_plan["lane_id"],
        "lane_kind": exact_plan["lane_kind"],
        "deployment_version": exact_plan["deployment_version"],
        "account_id_hash": exact_plan["account_id_hash"],
        "plan_hash": exact_plan["content_hash"],
        "environment_binding_hash": binding["content_hash"],
        "safety_evidence_hash": safety["content_hash"],
        "live_cutover_preflight_hash": preflight_hash,
        "execution_rehearsal_hash": execution_result["content_hash"] if execution_result else None,
        "paper_runtime_status": "LEGACY_UNCHANGED_NOT_YET_CUT_OVER",
        "legacy_live_executor_reachable": False,
        "broker_call_performed": False,
        "broker_submission_allowed": False,
        "configuration_mutated": False,
        "schedule_mutated": False,
        "execution_authority": False,
        "activation_authority": False,
    }
    seed = _hash(body)
    body["scheduler_result_id"] = f"generic-scheduler:{exact_plan['lane_id']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_generic_lane_scheduler_result(body)


def validate_generic_lane_scheduler_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "scheduler_result_id", "status", "reason_codes",
        "scheduler_enabled", "lane_id", "lane_kind", "deployment_version",
        "account_id_hash", "plan_hash", "environment_binding_hash",
        "safety_evidence_hash", "live_cutover_preflight_hash",
        "execution_rehearsal_hash", "paper_runtime_status",
        "legacy_live_executor_reachable", "broker_call_performed",
        "broker_submission_allowed", "configuration_mutated", "schedule_mutated",
        "execution_authority", "activation_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise GenericLaneSchedulerError("scheduler result fields are invalid")
    if payload["schema_version"] != GENERIC_LANE_SCHEDULER_RESULT_SCHEMA:
        raise GenericLaneSchedulerError("unsupported scheduler result schema")
    if payload["status"] not in {"DISABLED_NO_ACTION", "BLOCKED", "VALIDATED_NO_SUBMIT"}:
        raise GenericLaneSchedulerError("scheduler status is invalid")
    if type(payload["scheduler_enabled"]) is not bool:
        raise GenericLaneSchedulerError("scheduler_enabled must be boolean")
    if payload["paper_runtime_status"] != "LEGACY_UNCHANGED_NOT_YET_CUT_OVER":
        raise GenericLaneSchedulerError("scheduler cannot claim PAPER cutover")
    for field in (
        "legacy_live_executor_reachable", "broker_call_performed", "broker_submission_allowed",
        "configuration_mutated", "schedule_mutated", "execution_authority", "activation_authority",
    ):
        if payload[field] is not False:
            raise GenericLaneSchedulerError(f"scheduler safety flag must remain false: {field}")
    if payload["status"] == "VALIDATED_NO_SUBMIT" and not payload["execution_rehearsal_hash"]:
        raise GenericLaneSchedulerError("validated scheduler result requires execution rehearsal")
    if payload["content_hash"] != _hash(payload):
        raise GenericLaneSchedulerError("scheduler result content_hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "GENERIC_LANE_SCHEDULER_RESULT_SCHEMA", "GenericLaneSchedulerError",
    "run_generic_lane_scheduler_dry_run", "validate_generic_lane_scheduler_result",
]
