"""Deterministic lane-scoped portfolio allocation.

This module is deliberately non-authoritative: callers must supply both the
immutable sleeve-decision batch and an explicit, already-governed lane policy.
It never reads the active registry, selects a lifecycle state, or submits an
order.  The first supported method preserves the existing configured-risk-
budget economics while removing PAPER and strategy-name assumptions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import copy
from typing import Any, Mapping


LANE_ALLOCATION_SCHEMA = "caerus.lane_allocation.v1"
CONFIGURED_RISK_BUDGET = "configured_risk_budget_v1"
_SUPPORTED_DECISION_SCHEMAS = {
    "caerus.sleeve_decision_batch.v1",
    "caerus.sleeve_decision_batch.v2",
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SAFE_LANE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SAFE_SLEEVE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SAFE_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LaneAllocationError(RuntimeError):
    """Raised when a lane allocation cannot be proven from its inputs."""


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
        raise LaneAllocationError(f"lane allocation input is not canonical JSON: {exc}") from exc


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _finite_weight(value: Any, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LaneAllocationError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0 or result > 1.0 + 1e-12:
        raise LaneAllocationError(f"{label} must be in [0, 1]")
    return result


def _required_id(value: Any, *, label: str) -> str:
    result = str(value or "").strip()
    if not _SAFE_ID.fullmatch(result) or ".." in result:
        raise LaneAllocationError(f"invalid {label}: {value!r}")
    return result


def _timestamp(value: Any, *, label: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneAllocationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise LaneAllocationError(f"{label} must include a timezone")
    return raw


def _normalized_policy(
    lane_policy: Mapping[str, Any], *, deployment_version: str
) -> dict[str, Any]:
    if not isinstance(lane_policy, Mapping):
        raise LaneAllocationError("lane_policy must be an object")
    lane_id = str(lane_policy.get("lane_id") or "").strip().lower()
    if not _SAFE_LANE.fullmatch(lane_id):
        raise LaneAllocationError("lane_policy.lane_id is invalid")
    lane_kind = str(lane_policy.get("lane_kind") or "").strip().upper()
    if lane_kind not in {"SHADOW", "PAPER", "LIVE"}:
        raise LaneAllocationError("lane_policy.lane_kind is unsupported")
    if lane_policy.get("enabled") is not True:
        raise LaneAllocationError("lane allocation requires an enabled lane")
    if str(lane_policy.get("deployment_version") or deployment_version) != deployment_version:
        raise LaneAllocationError("lane policy deployment version mismatch")

    allocator = lane_policy.get("allocator_policy")
    if not isinstance(allocator, Mapping):
        raise LaneAllocationError("lane allocator_policy is required")
    allocator_id = _required_id(allocator.get("allocator_id"), label="allocator_id")
    allocator_version = str(allocator.get("allocator_version") or "").strip()
    method = str(allocator.get("method") or "").strip()
    if allocator_version != CONFIGURED_RISK_BUDGET or method != "configured_risk_budget":
        raise LaneAllocationError("only configured_risk_budget_v1 is currently supported")
    if str(allocator.get("unavailable_policy") or "") != "fail_closed":
        raise LaneAllocationError("lane allocator must fail closed on unavailable sleeves")
    target_cash = _finite_weight(
        allocator.get("target_cash_weight"), label="allocator_policy.target_cash_weight"
    )
    if target_cash >= 1.0:
        raise LaneAllocationError("target cash must leave positive investable capital")

    account_id_hash = str(lane_policy.get("account_id_hash") or "").strip().lower()
    if not _SHA256.fullmatch(account_id_hash):
        raise LaneAllocationError("lane_policy.account_id_hash must be a SHA-256 digest")
    broker_environment = str(lane_policy.get("broker_environment") or "").strip()
    if not broker_environment:
        raise LaneAllocationError("lane_policy.broker_environment is required")

    policy_objects: dict[str, dict[str, Any]] = {}
    policy_hashes: dict[str, str] = {}
    for name in (
        "risk_policy",
        "capital_policy",
        "execution_policy",
        "reconciliation_policy",
    ):
        value = lane_policy.get(name)
        if not isinstance(value, Mapping) or not value:
            raise LaneAllocationError(f"lane_policy.{name} must be a nonempty object")
        material = copy.deepcopy(dict(value))
        policy_hashes[name.removesuffix("_policy")] = content_hash(material)
        policy_objects[name] = material
    capital_basis = str(policy_objects["capital_policy"].get("capital_basis") or "").strip()
    if not capital_basis:
        raise LaneAllocationError("lane_policy.capital_policy.capital_basis is required")

    raw_sleeves = lane_policy.get("eligible_sleeves")
    if not isinstance(raw_sleeves, list) or not raw_sleeves:
        raise LaneAllocationError("lane eligible_sleeves must be a nonempty list")
    sleeves: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_sleeves:
        if not isinstance(raw, Mapping):
            raise LaneAllocationError("eligible sleeve rows must be objects")
        sleeve_id = str(raw.get("sleeve_id") or "").strip().lower()
        if not _SAFE_SLEEVE.fullmatch(sleeve_id) or sleeve_id in seen:
            raise LaneAllocationError(f"invalid or duplicate eligible sleeve: {sleeve_id!r}")
        seen.add(sleeve_id)
        allocation_eligible = raw.get("allocation_eligible")
        execution_eligible = raw.get("execution_eligible")
        observation_enabled = raw.get("observation_enabled")
        if not all(
            isinstance(value, bool)
            for value in (allocation_eligible, execution_eligible, observation_enabled)
        ):
            raise LaneAllocationError(
                f"{sleeve_id}: eligibility and observation flags must be booleans"
            )
        minimum = _finite_weight(raw.get("minimum_weight"), label=f"{sleeve_id}.minimum_weight")
        maximum = _finite_weight(raw.get("maximum_weight"), label=f"{sleeve_id}.maximum_weight")
        initial = _finite_weight(raw.get("initial_weight"), label=f"{sleeve_id}.initial_weight")
        if minimum > initial + 1e-12 or initial > maximum + 1e-12:
            raise LaneAllocationError(
                f"{sleeve_id}: initial weight must be within minimum/maximum bounds"
            )
        if not allocation_eligible and (minimum > 0.0 or initial > 0.0):
            raise LaneAllocationError(
                f"{sleeve_id}: non-allocatable sleeve cannot carry a positive weight"
            )
        if lane_kind in {"PAPER", "LIVE"} and allocation_eligible and not execution_eligible:
            raise LaneAllocationError(
                f"{sleeve_id}: executable lane allocation requires execution eligibility"
            )
        sleeves.append(
            {
                "sleeve_id": sleeve_id,
                "minimum_weight": minimum,
                "maximum_weight": maximum,
                "initial_weight": initial,
                "allocation_eligible": allocation_eligible,
                "execution_eligible": execution_eligible,
                "observation_enabled": observation_enabled,
            }
        )

    allocatable = [row for row in sleeves if row["allocation_eligible"]]
    if not allocatable:
        raise LaneAllocationError("lane has no allocation-eligible sleeves")
    if abs(sum(row["initial_weight"] for row in allocatable) - 1.0) > 1e-10:
        raise LaneAllocationError("configured lane sleeve weights must sum to one")
    if sum(row["minimum_weight"] for row in allocatable) > 1.0 + 1e-10:
        raise LaneAllocationError("lane minimum weights are infeasible")
    if sum(row["maximum_weight"] for row in allocatable) < 1.0 - 1e-10:
        raise LaneAllocationError("lane maximum weights are infeasible")

    raw_policy = dict(lane_policy)
    declared_hash = str(raw_policy.pop("content_hash", "")).lower()
    observed_hash = content_hash(raw_policy)
    if declared_hash and (not _SHA256.fullmatch(declared_hash) or declared_hash != observed_hash):
        raise LaneAllocationError("lane policy content hash mismatch")
    normalized_allocator = {
        "allocator_id": allocator_id,
        "allocator_version": allocator_version,
        "method": method,
        "unavailable_policy": "fail_closed",
        "target_cash_weight": target_cash,
    }
    return {
        "lane_id": lane_id,
        "lane_kind": lane_kind,
        "deployment_version": deployment_version,
        "performance_surface": str(lane_policy.get("performance_surface") or "").strip(),
        "account_id_hash": account_id_hash,
        "broker_environment": broker_environment,
        "capital_basis": capital_basis,
        "allocator_policy": normalized_allocator,
        "allocator_policy_hash": content_hash(normalized_allocator),
        "lane_policy_hash": observed_hash,
        "policy_objects": policy_objects,
        "policy_hashes": policy_hashes,
        "eligible_sleeves": sorted(sleeves, key=lambda row: row["sleeve_id"]),
    }


def _decision_index(decision_batch: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], str]:
    if not isinstance(decision_batch, Mapping):
        raise LaneAllocationError("decision_batch must be an object")
    batch_schema = decision_batch.get("schema_version")
    if batch_schema not in _SUPPORTED_DECISION_SCHEMAS:
        raise LaneAllocationError("unsupported sleeve decision batch schema")
    rows = decision_batch.get("decisions")
    if not isinstance(rows, list) or not rows:
        raise LaneAllocationError("decision batch decisions are required")
    index: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise LaneAllocationError("decision rows must be objects")
        sleeve_id = str(row.get("sleeve_id") or "").strip().lower()
        if not _SAFE_SLEEVE.fullmatch(sleeve_id) or sleeve_id in index:
            raise LaneAllocationError(f"blank, invalid, or duplicate decision sleeve: {sleeve_id!r}")
        decision_id = _required_id(row.get("decision_id"), label=f"{sleeve_id}.decision_id")
        declared = str(row.get("content_hash") or "").lower()
        unhashed = dict(row)
        unhashed.pop("content_hash", None)
        if not _SHA256.fullmatch(declared) or declared != content_hash(unhashed):
            raise LaneAllocationError(f"{sleeve_id}: decision content hash mismatch")
        if decision_id != str(row.get("decision_id")):
            raise LaneAllocationError(f"{sleeve_id}: decision identity is not canonical")
        if batch_schema == "caerus.sleeve_decision_batch.v2":
            from core.sleeve_decision import SleeveDecisionError, require_valid_sleeve_decision

            try:
                require_valid_sleeve_decision(row)
            except SleeveDecisionError as exc:
                raise LaneAllocationError(
                    f"{sleeve_id}: invalid standard sleeve decision: {exc}"
                ) from exc
        index[sleeve_id] = row
    batch_hash = str(decision_batch.get("content_hash") or "").lower()
    observed_batch_hash = content_hash(rows)
    if batch_hash and batch_hash != observed_batch_hash:
        raise LaneAllocationError("decision batch content hash mismatch")
    return index, batch_hash or observed_batch_hash


def _target_rows(decision: Mapping[str, Any], *, sleeve_id: str) -> list[dict[str, Any]]:
    raw_rows = decision.get("target_rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise LaneAllocationError(f"{sleeve_id}: recommendation has no target rows")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise LaneAllocationError(f"{sleeve_id}: target rows must be objects")
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in seen:
            raise LaneAllocationError(f"{sleeve_id}: invalid or duplicate target symbol {symbol!r}")
        seen.add(symbol)
        weight = _finite_weight(raw.get("target_weight"), label=f"{sleeve_id}.{symbol}.target_weight")
        if weight <= 0.0:
            raise LaneAllocationError(f"{sleeve_id}.{symbol}: target weight must be positive")
        rows.append({"symbol": symbol, "target_weight": weight})
    if abs(sum(row["target_weight"] for row in rows) - 1.0) > 1e-9:
        raise LaneAllocationError(f"{sleeve_id}: internal target weights must sum to one")
    return sorted(rows, key=lambda row: row["symbol"])


def allocate_lane(
    *,
    decision_batch: Mapping[str, Any],
    lane_policy: Mapping[str, Any],
    deployment_version: str,
    allocated_at: str | None = None,
) -> dict[str, Any]:
    """Allocate one enabled lane from explicit, immutable inputs.

    The function is strategy-agnostic and does not infer eligibility from a
    lifecycle label.  `initial_weight` is the configured weight used by the
    first approved allocator.  Later allocator versions may vary weights inside
    the same policy bounds without changing this contract.
    """

    deployment_version = _required_id(deployment_version, label="deployment_version")
    policy = _normalized_policy(lane_policy, deployment_version=deployment_version)
    decisions, decision_batch_hash = _decision_index(decision_batch)
    decision_session = _required_id(decision_batch.get("session_id"), label="session_id")
    session_hash = str(decision_batch.get("session_hash") or "").lower()
    if not _SHA256.fullmatch(session_hash):
        raise LaneAllocationError("decision batch session_hash is invalid")
    trade_date = str(decision_batch.get("trade_date") or "").strip()
    try:
        dt.date.fromisoformat(trade_date)
    except ValueError as exc:
        raise LaneAllocationError("decision batch trade_date is invalid") from exc

    allocatable = [
        row for row in policy["eligible_sleeves"] if row["allocation_eligible"]
    ]
    required_ids = {row["sleeve_id"] for row in allocatable}
    missing = sorted(required_ids - set(decisions))
    if missing:
        raise LaneAllocationError("lane decisions missing approved sleeves: " + ",".join(missing))

    investable = 1.0 - float(policy["allocator_policy"]["target_cash_weight"])
    allocation_rows: list[dict[str, Any]] = []
    symbol_contributions: dict[str, list[dict[str, Any]]] = {}
    for sleeve in allocatable:
        sleeve_id = sleeve["sleeve_id"]
        decision = decisions[sleeve_id]
        if str(decision.get("outcome") or "") != "RECOMMENDATION":
            raise LaneAllocationError(f"approved lane sleeve is unavailable: {sleeve_id}")
        weight = float(sleeve["initial_weight"])
        targets = _target_rows(decision, sleeve_id=sleeve_id)
        allocation_rows.append(
            {
                "sleeve_id": sleeve_id,
                "weight": weight,
                "minimum_weight": sleeve["minimum_weight"],
                "maximum_weight": sleeve["maximum_weight"],
                "account_target_weight": round(investable * weight, 12),
                "execution_eligible": sleeve["execution_eligible"],
                "decision_id": decision["decision_id"],
                "decision_hash": decision["content_hash"],
            }
        )
        for target in targets:
            contribution = round(
                investable * weight * float(target["target_weight"]), 12
            )
            if contribution <= 0.0:
                continue
            symbol_contributions.setdefault(target["symbol"], []).append(
                {
                    "sleeve_id": sleeve_id,
                    "target_weight": contribution,
                    "lane_allocation_weight": weight,
                    "sleeve_internal_weight": target["target_weight"],
                    "decision_id": decision["decision_id"],
                    "decision_hash": decision["content_hash"],
                }
            )

    targets: list[dict[str, Any]] = []
    for symbol in sorted(symbol_contributions):
        contributions = sorted(
            symbol_contributions[symbol], key=lambda row: row["sleeve_id"]
        )
        dominant = max(
            contributions,
            key=lambda row: (float(row["target_weight"]), row["sleeve_id"]),
        )["sleeve_id"]
        targets.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "sleeve": dominant,
                "target_weight": round(
                    sum(float(row["target_weight"]) for row in contributions), 12
                ),
                "sleeve_contributions": contributions,
            }
        )
    if not targets:
        raise LaneAllocationError("lane allocation produced no invested targets")
    gross = sum(float(row["target_weight"]) for row in targets)
    if abs(gross - investable) > 1e-9:
        raise LaneAllocationError(
            f"lane allocated gross {gross} does not equal investable weight {investable}"
        )

    effective_allocated_at = _timestamp(
        allocated_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        label="allocated_at",
    )
    body = {
        "schema_version": LANE_ALLOCATION_SCHEMA,
        "trade_date": trade_date,
        "session_id": decision_session,
        "session_hash": session_hash,
        "lane_id": policy["lane_id"],
        "lane_kind": policy["lane_kind"],
        "deployment_version": deployment_version,
        "performance_surface": policy["performance_surface"],
        "account_id_hash": policy["account_id_hash"],
        "broker_environment": policy["broker_environment"],
        "capital_basis": policy["capital_basis"],
        "allocator_id": policy["allocator_policy"]["allocator_id"],
        "allocator_version": policy["allocator_policy"]["allocator_version"],
        "method": policy["allocator_policy"]["method"],
        "unavailable_policy": "fail_closed",
        "allocator_policy_hash": policy["allocator_policy_hash"],
        "lane_policy_hash": policy["lane_policy_hash"],
        "policy_hashes": {
            "lane": policy["lane_policy_hash"],
            "allocator": policy["allocator_policy_hash"],
            **policy["policy_hashes"],
        },
        "eligible_sleeves": sorted(required_ids),
        "target_cash_weight": policy["allocator_policy"]["target_cash_weight"],
        "invested_target_weight": investable,
        "constraints": {
            "unavailable_policy": "fail_closed",
            "target_cash_weight": policy["allocator_policy"]["target_cash_weight"],
            "sleeve_weight_bounds": [
                {
                    "sleeve_id": row["sleeve_id"],
                    "minimum_weight": row["minimum_weight"],
                    "maximum_weight": row["maximum_weight"],
                }
                for row in sorted(allocatable, key=lambda item: item["sleeve_id"])
            ],
        },
        "sleeve_weights": sorted(allocation_rows, key=lambda row: row["sleeve_id"]),
        "decision_ids": sorted(
            str(decisions[sleeve_id]["decision_id"]) for sleeve_id in required_ids
        ),
        "decision_hashes": sorted(
            str(decisions[sleeve_id]["content_hash"]) for sleeve_id in required_ids
        ),
        "targets": targets,
        "decision_batch_hash": decision_batch_hash,
        "allocated_at": effective_allocated_at,
    }
    allocation_seed = content_hash(body)
    body["allocation_id"] = (
        f"lane-allocation:{policy['lane_id']}:{trade_date}:{allocation_seed[:24]}"
    )
    body["content_hash"] = content_hash(body)
    return body


def validate_lane_allocation(payload: Mapping[str, Any]) -> list[str]:
    """Return deterministic structural/hash failures for a serialized allocation."""

    failures: list[str] = []
    if not isinstance(payload, Mapping):
        return ["lane_allocation:not_object"]
    if payload.get("schema_version") != LANE_ALLOCATION_SCHEMA:
        failures.append("lane_allocation:schema")
    body = dict(payload)
    declared = str(body.pop("content_hash", ""))
    if declared != content_hash(body):
        failures.append("lane_allocation:content_hash")
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        failures.append("lane_allocation:targets")
    else:
        try:
            gross = sum(float(row.get("target_weight") or 0.0) for row in targets)
            expected = float(payload.get("invested_target_weight"))
            if abs(gross - expected) > 1e-9:
                failures.append("lane_allocation:gross")
        except (TypeError, ValueError):
            failures.append("lane_allocation:gross")
    if not _SAFE_LANE.fullmatch(str(payload.get("lane_id") or "")):
        failures.append("lane_allocation:lane_id")
    if not _SAFE_ID.fullmatch(str(payload.get("deployment_version") or "")):
        failures.append("lane_allocation:deployment_version")
    if not _SHA256.fullmatch(str(payload.get("account_id_hash") or "")):
        failures.append("lane_allocation:account_id_hash")
    policy_hashes = payload.get("policy_hashes")
    required_policy_hashes = {
        "lane", "allocator", "risk", "capital", "execution", "reconciliation"
    }
    if (
        not isinstance(policy_hashes, Mapping)
        or set(policy_hashes) != required_policy_hashes
        or any(not _SHA256.fullmatch(str(value or "")) for value in policy_hashes.values())
    ):
        failures.append("lane_allocation:policy_hashes")
    return sorted(set(failures))
