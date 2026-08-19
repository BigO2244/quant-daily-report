"""Canonical, non-executable lane target package.

The package is the Stage 6 Decision authority for one lane, session, and
deployment version.  It binds the generic lane allocation to the immutable
sleeve decisions while explicitly carrying no broker execution authority.
Independent Risk and exact-plan authorization remain downstream requirements.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from typing import Any, Mapping

from core.lane_allocator import (
    LANE_ALLOCATION_SCHEMA,
    canonical_json,
    content_hash,
    validate_lane_allocation,
)


LANE_TARGET_PACKAGE_SCHEMA = "caerus.lane_target_package.v1"
_SUPPORTED_DECISION_SCHEMAS = {
    "caerus.sleeve_decision_batch.v1",
    "caerus.sleeve_decision_batch.v2",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LaneTargetAuthorityError(RuntimeError):
    """Raised when one canonical lane target cannot be proven."""


def _timestamp(value: Any, *, label: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneTargetAuthorityError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise LaneTargetAuthorityError(f"{label} must include a timezone")
    return raw


def _decision_index(decision_batch: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], str]:
    if not isinstance(decision_batch, Mapping):
        raise LaneTargetAuthorityError("decision batch must be an object")
    if decision_batch.get("schema_version") not in _SUPPORTED_DECISION_SCHEMAS:
        raise LaneTargetAuthorityError("unsupported sleeve decision batch schema")
    rows = decision_batch.get("decisions")
    if not isinstance(rows, list) or not rows:
        raise LaneTargetAuthorityError("decision batch has no decisions")
    observed_batch_hash = content_hash(rows)
    declared_batch_hash = str(decision_batch.get("content_hash") or observed_batch_hash)
    if declared_batch_hash != observed_batch_hash:
        raise LaneTargetAuthorityError("decision batch content hash mismatch")
    index: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise LaneTargetAuthorityError("decision rows must be objects")
        sleeve_id = str(row.get("sleeve_id") or "").strip().lower()
        if not sleeve_id or sleeve_id in index:
            raise LaneTargetAuthorityError("decision sleeve identity is blank or duplicated")
        body = dict(row)
        declared = str(body.pop("content_hash", ""))
        if not _SHA256.fullmatch(declared) or declared != content_hash(body):
            raise LaneTargetAuthorityError(
                f"decision content hash mismatch: {sleeve_id}"
            )
        index[sleeve_id] = row
    return index, declared_batch_hash


def _prove_lineage(
    *,
    allocation: Mapping[str, Any],
    decision_batch: Mapping[str, Any],
) -> None:
    failures = validate_lane_allocation(allocation)
    if failures:
        raise LaneTargetAuthorityError(
            "lane allocation is invalid: " + ",".join(failures)
        )
    if allocation.get("schema_version") != LANE_ALLOCATION_SCHEMA:
        raise LaneTargetAuthorityError("unsupported lane allocation schema")
    if allocation.get("trade_date") != decision_batch.get("trade_date"):
        raise LaneTargetAuthorityError("allocation and decision trade dates differ")
    if allocation.get("session_id") != decision_batch.get("session_id"):
        raise LaneTargetAuthorityError("allocation and decision sessions differ")
    if allocation.get("session_hash") != decision_batch.get("session_hash"):
        raise LaneTargetAuthorityError("allocation and decision session hashes differ")
    decisions, batch_hash = _decision_index(decision_batch)
    if allocation.get("decision_batch_hash") != batch_hash:
        raise LaneTargetAuthorityError("allocation decision-batch lineage mismatch")

    eligible = set(allocation.get("eligible_sleeves") or [])
    weights = allocation.get("sleeve_weights")
    if not isinstance(weights, list) or {row.get("sleeve_id") for row in weights} != eligible:
        raise LaneTargetAuthorityError("allocation sleeve-weight coverage mismatch")
    for row in weights:
        sleeve_id = str(row.get("sleeve_id") or "")
        decision = decisions.get(sleeve_id)
        if (
            decision is None
            or row.get("decision_id") != decision.get("decision_id")
            or row.get("decision_hash") != decision.get("content_hash")
        ):
            raise LaneTargetAuthorityError(
                f"allocation decision lineage mismatch: {sleeve_id}"
            )
    for target in allocation.get("targets") or []:
        contributions = target.get("sleeve_contributions") if isinstance(target, Mapping) else None
        if not isinstance(contributions, list) or not contributions:
            raise LaneTargetAuthorityError("target lacks causal sleeve contributions")
        for contribution in contributions:
            sleeve_id = str(contribution.get("sleeve_id") or "")
            decision = decisions.get(sleeve_id)
            if (
                sleeve_id not in eligible
                or decision is None
                or contribution.get("decision_id") != decision.get("decision_id")
                or contribution.get("decision_hash") != decision.get("content_hash")
            ):
                raise LaneTargetAuthorityError(
                    f"target contribution decision lineage mismatch: {sleeve_id}"
                )


def _target_semantics(allocation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": allocation.get("trade_date"),
        "session_id": allocation.get("session_id"),
        "session_hash": allocation.get("session_hash"),
        "lane_id": allocation.get("lane_id"),
        "lane_kind": allocation.get("lane_kind"),
        "deployment_version": allocation.get("deployment_version"),
        "account_id_hash": allocation.get("account_id_hash"),
        "allocation_id": allocation.get("allocation_id"),
        "allocation_hash": allocation.get("content_hash"),
        "target_cash_weight": allocation.get("target_cash_weight"),
        "capital_basis": allocation.get("capital_basis"),
        "target_rows": copy.deepcopy(allocation.get("targets") or []),
    }


def build_lane_target_package(
    *,
    lane_allocation: Mapping[str, Any],
    decision_batch: Mapping[str, Any],
    sealed_at: str | None = None,
) -> dict[str, Any]:
    """Seal one lane allocation as a downstream-Risk-pending Decision target."""

    _prove_lineage(allocation=lane_allocation, decision_batch=decision_batch)
    effective_sealed_at = _timestamp(
        sealed_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        label="sealed_at",
    )
    semantics = _target_semantics(lane_allocation)
    target_hash = content_hash(semantics)
    package_seed = content_hash(
        {
            "target_hash": target_hash,
            "lane_policy_hash": lane_allocation.get("lane_policy_hash"),
            "allocator_policy_hash": lane_allocation.get("allocator_policy_hash"),
        }
    )
    body = {
        "schema_version": LANE_TARGET_PACKAGE_SCHEMA,
        "target_package_id": (
            f"lane-target:{lane_allocation.get('lane_id')}:"
            f"{lane_allocation.get('trade_date')}:{package_seed[:24]}"
        ),
        "trade_date": lane_allocation.get("trade_date"),
        "sealed_at": effective_sealed_at,
        "authority": "DECISION",
        "execution_authority": False,
        "risk_status": "PENDING",
        "session_id": lane_allocation.get("session_id"),
        "session_hash": lane_allocation.get("session_hash"),
        "lane_id": lane_allocation.get("lane_id"),
        "lane_kind": lane_allocation.get("lane_kind"),
        "deployment_version": lane_allocation.get("deployment_version"),
        "performance_surface": lane_allocation.get("performance_surface"),
        "account_id_hash": lane_allocation.get("account_id_hash"),
        "broker_environment": lane_allocation.get("broker_environment"),
        "allocation_id": lane_allocation.get("allocation_id"),
        "allocation_hash": lane_allocation.get("content_hash"),
        "lane_policy_hash": lane_allocation.get("lane_policy_hash"),
        "allocator_policy_hash": lane_allocation.get("allocator_policy_hash"),
        "policy_hashes": copy.deepcopy(lane_allocation.get("policy_hashes") or {}),
        "decision_batch_hash": lane_allocation.get("decision_batch_hash"),
        "eligible_sleeves": copy.deepcopy(lane_allocation.get("eligible_sleeves") or []),
        "sleeve_weights": copy.deepcopy(lane_allocation.get("sleeve_weights") or []),
        "target_cash_weight": lane_allocation.get("target_cash_weight"),
        "capital_basis": lane_allocation.get("capital_basis"),
        "constraints": copy.deepcopy(lane_allocation.get("constraints") or {}),
        "decision_ids": copy.deepcopy(lane_allocation.get("decision_ids") or []),
        "decision_hashes": copy.deepcopy(lane_allocation.get("decision_hashes") or []),
        "target_rows": copy.deepcopy(lane_allocation.get("targets") or []),
        "target_hash": target_hash,
        "source_hashes": {
            "decision_batch": lane_allocation.get("decision_batch_hash"),
            "lane_allocation": lane_allocation.get("content_hash"),
            "lane_policy": lane_allocation.get("lane_policy_hash"),
            "allocator_policy": lane_allocation.get("allocator_policy_hash"),
        },
    }
    body["content_hash"] = content_hash(body)
    return body


def validate_lane_target_package(payload: Mapping[str, Any]) -> list[str]:
    """Return deterministic failures for a serialized lane target package."""

    failures: list[str] = []
    if not isinstance(payload, Mapping):
        return ["lane_target:not_object"]
    if payload.get("schema_version") != LANE_TARGET_PACKAGE_SCHEMA:
        failures.append("lane_target:schema")
    if payload.get("authority") != "DECISION":
        failures.append("lane_target:authority")
    if payload.get("execution_authority") is not False:
        failures.append("lane_target:execution_authority")
    if payload.get("risk_status") != "PENDING":
        failures.append("lane_target:risk_status")
    body = dict(payload)
    declared = str(body.pop("content_hash", ""))
    if declared != content_hash(body):
        failures.append("lane_target:content_hash")
    semantics = {
        "trade_date": payload.get("trade_date"),
        "session_id": payload.get("session_id"),
        "session_hash": payload.get("session_hash"),
        "lane_id": payload.get("lane_id"),
        "lane_kind": payload.get("lane_kind"),
        "deployment_version": payload.get("deployment_version"),
        "account_id_hash": payload.get("account_id_hash"),
        "allocation_id": payload.get("allocation_id"),
        "allocation_hash": payload.get("allocation_hash"),
        "target_cash_weight": payload.get("target_cash_weight"),
        "capital_basis": payload.get("capital_basis"),
        "target_rows": payload.get("target_rows") or [],
    }
    if payload.get("target_hash") != content_hash(semantics):
        failures.append("lane_target:target_hash")
    if not _SHA256.fullmatch(str(payload.get("account_id_hash") or "")):
        failures.append("lane_target:account_id_hash")
    policy_hashes = payload.get("policy_hashes")
    required_policy_hashes = {
        "lane", "allocator", "risk", "capital", "execution", "reconciliation"
    }
    if (
        not isinstance(policy_hashes, Mapping)
        or set(policy_hashes) != required_policy_hashes
        or any(not _SHA256.fullmatch(str(value or "")) for value in policy_hashes.values())
    ):
        failures.append("lane_target:policy_hashes")
    sources = payload.get("source_hashes")
    expected_sources = {
        "decision_batch": payload.get("decision_batch_hash"),
        "lane_allocation": payload.get("allocation_hash"),
        "lane_policy": payload.get("lane_policy_hash"),
        "allocator_policy": payload.get("allocator_policy_hash"),
    }
    if sources != expected_sources or any(
        not _SHA256.fullmatch(str(value or "")) for value in expected_sources.values()
    ):
        failures.append("lane_target:source_hashes")
    return sorted(set(failures))


__all__ = [
    "LANE_TARGET_PACKAGE_SCHEMA",
    "LaneTargetAuthorityError",
    "build_lane_target_package",
    "canonical_json",
    "validate_lane_target_package",
]
