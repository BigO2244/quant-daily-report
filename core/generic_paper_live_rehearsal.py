"""Immutable proof of structural PAPER/LIVE generic-path parity.

This contract records deterministic no-write execution rehearsals.  It is not
broker-factual evidence and cannot authorize a cutover or submission.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json
from core.lane_execution_dry_run import (
    LaneExecutionDryRunError,
    validate_lane_execution_dry_run_result,
)
from core.scheduled_v2_factual_pipeline import (
    GENERIC_EXECUTION_ADAPTER,
    PIPELINE_RESULT_SCHEMA,
    PIPELINE_RUN_RECEIPT_SCHEMA,
)


GENERIC_PAPER_LIVE_REHEARSAL_SCHEMA = "caerus.generic_paper_live_no_write_rehearsal.v1"
_SHA = frozenset("0123456789abcdef")
_FIELDS = frozenset(
    {
        "schema_version", "created_at", "classification", "parity_status",
        "source_artifact_schema", "source_artifact_hash", "operational_receipt_hash",
        "shared_adapter_id", "deployment_version", "rehearsals", "broker_factual",
        "broker_call_performed", "broker_write_performed", "configuration_mutated",
        "schedule_mutated", "kill_switch_mutated", "execution_authority",
        "activation_authority", "approval_authority", "content_hash",
    }
)
_ROW_FIELDS = frozenset(
    {
        "lane_id", "lane_kind", "deployment_version", "execution_dry_run_hash",
        "status", "write_enabled", "broker_submission_allowed",
    }
)


class GenericPaperLiveRehearsalError(ValueError):
    """Raised when structural parity evidence is incomplete or overclaims facts."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _SHA for c in value):
        raise GenericPaperLiveRehearsalError(f"{label} must be a lowercase SHA-256 hash")
    return value


def _text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GenericPaperLiveRehearsalError(f"{label} must be a non-blank string")
    return value


def build_generic_paper_live_rehearsal(
    *, created_at: str, pipeline_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive parity only from a sealed scheduled pipeline receipt."""

    if not isinstance(pipeline_receipt, Mapping) or pipeline_receipt.get("schema_version") != PIPELINE_RUN_RECEIPT_SCHEMA:
        raise GenericPaperLiveRehearsalError("unsupported scheduled pipeline receipt")
    receipt = copy.deepcopy(dict(pipeline_receipt))
    receipt_hash = receipt.get("content_hash")
    _sha(receipt_hash, label="pipeline receipt content_hash")
    if receipt_hash != _hash(receipt):
        raise GenericPaperLiveRehearsalError("scheduled pipeline receipt content_hash mismatch")
    artifact = receipt.get("artifact")
    persistence = receipt.get("persistence")
    if not isinstance(artifact, Mapping) or not isinstance(persistence, Mapping):
        raise GenericPaperLiveRehearsalError("scheduled pipeline receipt sections are missing")
    artifact_hash = artifact.get("content_hash")
    _sha(artifact_hash, label="pipeline artifact content_hash")
    if artifact_hash != _hash(artifact):
        raise GenericPaperLiveRehearsalError("scheduled pipeline artifact content_hash mismatch")
    if persistence.get("artifact_hash") != artifact_hash:
        raise GenericPaperLiveRehearsalError("receipt persistence does not bind pipeline artifact")
    if (
        artifact.get("schema_version") != PIPELINE_RESULT_SCHEMA
        or artifact.get("status") != "DISABLED_NO_WRITE_REHEARSAL"
        or artifact.get("generic_execution_adapter") != GENERIC_EXECUTION_ADAPTER
        or artifact.get("expected_lane_kinds") != ["LIVE", "PAPER"]
        or artifact.get("paper_live_rehearsal") is not True
    ):
        raise GenericPaperLiveRehearsalError("pipeline is not the disabled generic PAPER/LIVE rehearsal")
    for field in (
        "schedule_enabled", "write_enabled", "write_performed", "broker_call_performed",
        "broker_submission_allowed", "execution_authority", "activation_authority",
        "registry_mutation_performed", "runtime_mutation_performed",
        "scheduler_mutation_performed", "dashboard_mutation_performed",
        "official_history_mutation_performed",
    ):
        if artifact.get(field) is not False:
            raise GenericPaperLiveRehearsalError(f"pipeline artifact {field} must remain false")
    lanes = artifact.get("factual_lanes")
    if not isinstance(lanes, list) or len(lanes) != 2:
        raise GenericPaperLiveRehearsalError("pipeline must contain exactly PAPER and LIVE lanes")
    deployment_versions: set[str] = set()
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise GenericPaperLiveRehearsalError("pipeline factual lane must be an object")
        if (
            lane.get("execution_adapter") != GENERIC_EXECUTION_ADAPTER
            or lane.get("execution_evidence_classification") != "STRUCTURAL_REHEARSAL"
        ):
            raise GenericPaperLiveRehearsalError("factual lane does not use the shared generic adapter")
        try:
            execution = validate_lane_execution_dry_run_result(lane.get("execution_rehearsal"))
        except LaneExecutionDryRunError as exc:
            raise GenericPaperLiveRehearsalError(f"nested execution rehearsal is invalid: {exc}") from exc
        if lane.get("execution_rehearsal_hash") != execution["content_hash"]:
            raise GenericPaperLiveRehearsalError("nested execution rehearsal hash mismatch")
        if execution["status"] != "VALIDATED_NO_WRITE":
            raise GenericPaperLiveRehearsalError("nested execution rehearsal is not VALIDATED_NO_WRITE")
        if execution["write_enabled"] is not False or execution["broker_submission_allowed"] is not False:
            raise GenericPaperLiveRehearsalError("nested execution rehearsal is not no-write/no-submit")
        if lane.get("lane_id") != execution["lane_id"] or lane.get("lane_kind") != execution["lane_kind"]:
            raise GenericPaperLiveRehearsalError("pipeline lane scope differs from execution rehearsal")
        deployment_versions.add(execution["deployment_version"])
        rows.append(
            {
                "lane_id": execution["lane_id"],
                "lane_kind": execution["lane_kind"],
                "deployment_version": execution["deployment_version"],
                "execution_dry_run_hash": execution["content_hash"],
                "status": execution["status"],
                "write_enabled": execution["write_enabled"],
                "broker_submission_allowed": execution["broker_submission_allowed"],
            }
        )
    rows.sort(key=lambda row: row["lane_kind"])
    if [row["lane_kind"] for row in rows] != ["LIVE", "PAPER"] or len(deployment_versions) != 1:
        raise GenericPaperLiveRehearsalError("pipeline lanes do not prove one-deployment PAPER/LIVE parity")
    deployment_version = next(iter(deployment_versions))
    body = {
        "schema_version": GENERIC_PAPER_LIVE_REHEARSAL_SCHEMA,
        "created_at": created_at,
        "classification": "STRUCTURAL_REHEARSAL_NOT_BROKER_FACTUAL",
        "parity_status": "PASS",
        "source_artifact_schema": artifact["schema_version"],
        "source_artifact_hash": artifact_hash,
        "operational_receipt_hash": receipt_hash,
        "shared_adapter_id": GENERIC_EXECUTION_ADAPTER,
        "deployment_version": deployment_version,
        "rehearsals": rows,
        "broker_factual": False,
        "broker_call_performed": False,
        "broker_write_performed": False,
        "configuration_mutated": False,
        "schedule_mutated": False,
        "kill_switch_mutated": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_generic_paper_live_rehearsal(body)


def validate_generic_paper_live_rehearsal(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise GenericPaperLiveRehearsalError("generic PAPER/LIVE rehearsal fields are invalid")
    if payload.get("schema_version") != GENERIC_PAPER_LIVE_REHEARSAL_SCHEMA:
        raise GenericPaperLiveRehearsalError("unsupported generic PAPER/LIVE rehearsal")
    if payload.get("classification") != "STRUCTURAL_REHEARSAL_NOT_BROKER_FACTUAL":
        raise GenericPaperLiveRehearsalError("rehearsal cannot claim broker-factual evidence")
    if payload.get("parity_status") != "PASS":
        raise GenericPaperLiveRehearsalError("rehearsal parity is not PASS")
    for field in (
        "created_at", "source_artifact_schema", "shared_adapter_id", "deployment_version"
    ):
        _text(payload.get(field), label=field)
    for field in ("source_artifact_hash", "operational_receipt_hash", "content_hash"):
        _sha(payload.get(field), label=field)
    rows = payload.get("rehearsals")
    if not isinstance(rows, list) or len(rows) != 2:
        raise GenericPaperLiveRehearsalError("exactly PAPER and LIVE rehearsals are required")
    kinds: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _ROW_FIELDS:
            raise GenericPaperLiveRehearsalError("rehearsal row fields are invalid")
        for field in ("lane_id", "lane_kind", "deployment_version", "status"):
            _text(row.get(field), label=f"rehearsal.{field}")
        if row["deployment_version"] != payload["deployment_version"]:
            raise GenericPaperLiveRehearsalError("rehearsal deployment versions differ")
        if row["status"] != "VALIDATED_NO_WRITE":
            raise GenericPaperLiveRehearsalError("execution rehearsal must be VALIDATED_NO_WRITE")
        _sha(row.get("execution_dry_run_hash"), label="execution_dry_run_hash")
        if row.get("write_enabled") is not False or row.get("broker_submission_allowed") is not False:
            raise GenericPaperLiveRehearsalError("execution rehearsal must remain no-write/no-submit")
        kinds.append(row["lane_kind"])
    if kinds != ["LIVE", "PAPER"]:
        raise GenericPaperLiveRehearsalError("rehearsals must be uniquely sorted LIVE/PAPER")
    for field in (
        "broker_factual", "broker_call_performed", "broker_write_performed",
        "configuration_mutated", "schedule_mutated", "kill_switch_mutated",
        "execution_authority", "activation_authority", "approval_authority",
    ):
        if payload.get(field) is not False:
            raise GenericPaperLiveRehearsalError(f"rehearsal {field} must remain false")
    if payload["content_hash"] != _hash(payload):
        raise GenericPaperLiveRehearsalError("generic PAPER/LIVE rehearsal content_hash mismatch")
    return json.loads(canonical_json(payload))


__all__ = [
    "GENERIC_PAPER_LIVE_REHEARSAL_SCHEMA", "GenericPaperLiveRehearsalError",
    "build_generic_paper_live_rehearsal", "validate_generic_paper_live_rehearsal",
]
