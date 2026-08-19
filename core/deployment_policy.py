"""Immutable, non-authoritative lane deployment policy contracts.

This module validates and reads ``caerus.lane_deployment_policy.v1`` artifacts.
It deliberately does not select sleeves, activate a deployment, write policy,
or mutate runtime configuration.  A valid artifact is only a well-formed
candidate policy; activation remains a separate governed operation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


DEPLOYMENT_POLICY_SCHEMA = "caerus.lane_deployment_policy.v1"
VALID_DEPLOYMENT_STATUSES = frozenset(
    {"PENDING", "ACTIVE", "DISABLED", "SUPERSEDED", "ROLLED_BACK"}
)
VALID_LANE_KINDS = frozenset({"SHADOW", "PAPER", "LIVE"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "deployment_version",
        "status",
        "approved_by",
        "owner_decision_id",
        "approved_at",
        "effective_session",
        "prior_deployment_version",
        "rollback_deployment_version",
        "content_hash",
        "lanes",
    }
)
_LANE_FIELDS = frozenset(
    {
        "lane_id",
        "lane_kind",
        "enabled",
        "account_id_hash",
        "broker_environment",
        "performance_surface",
        "eligible_sleeves",
        "allocator_policy",
        "risk_policy",
        "capital_policy",
        "execution_policy",
        "reconciliation_policy",
    }
)
_SLEEVE_FIELDS = frozenset(
    {
        "sleeve_id",
        "minimum_weight",
        "maximum_weight",
        "initial_weight",
        "allocation_eligible",
        "execution_eligible",
        "observation_enabled",
    }
)


class DeploymentPolicyError(ValueError):
    """Raised when a lane deployment policy cannot be proven valid."""


def _reject_json_constant(value: str) -> None:
    raise DeploymentPolicyError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentPolicyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def canonical_json(payload: Any) -> str:
    """Return the canonical JSON representation used for contract hashes."""

    try:
        return json.dumps(
            _thaw_json(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeploymentPolicyError(f"artifact is not canonical JSON: {exc}") from exc


def artifact_content_hash(payload: Mapping[str, Any]) -> str:
    """Hash an artifact body, excluding its top-level ``content_hash`` field."""

    body = dict(_thaw_json(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def seal_deployment_policy_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy sealed with its deterministic content hash.

    Sealing does not validate or authorize the policy.
    """

    sealed = dict(_thaw_json(payload))
    sealed["content_hash"] = artifact_content_hash(sealed)
    return sealed


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise DeploymentPolicyError("JSON object keys must be strings")
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise DeploymentPolicyError("JSON object keys must be strings")
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_exact_fields(
    payload: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    present = set(payload)
    missing = sorted(expected - present)
    unknown = sorted(present - expected)
    if missing:
        raise DeploymentPolicyError(f"{label} missing required fields: {', '.join(missing)}")
    if unknown:
        raise DeploymentPolicyError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _required_string(value: Any, *, label: str, safe_id: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentPolicyError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if safe_id and (not _SAFE_ID.fullmatch(normalized) or ".." in normalized):
        raise DeploymentPolicyError(f"{label} is not a valid identifier")
    return normalized


def _required_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise DeploymentPolicyError(f"{label} must be a boolean")
    return value


def _sha256(value: Any, *, label: str) -> str:
    normalized = _required_string(value, label=label)
    if not _SHA256.fullmatch(normalized):
        raise DeploymentPolicyError(f"{label} must be a lowercase SHA-256 digest")
    return normalized


def _iso_date(value: Any, *, label: str) -> str:
    raw = _required_string(value, label=label)
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise DeploymentPolicyError(f"{label} must be an ISO date") from exc
    return raw


def _iso_timestamp(value: Any, *, label: str) -> str:
    raw = _required_string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeploymentPolicyError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeploymentPolicyError(f"{label} must include a timezone")
    return raw


def _weight(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise DeploymentPolicyError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DeploymentPolicyError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise DeploymentPolicyError(f"{label} must be finite and within [0, 1]")
    return result


def _policy_object(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise DeploymentPolicyError(f"{label} must be a non-empty JSON object")
    # Canonical serialization proves that nested values are finite JSON data.
    canonical_json(value)
    return _freeze_json(value)


@dataclass(frozen=True)
class EligibleSleeve:
    sleeve_id: str
    minimum_weight: float
    maximum_weight: float
    initial_weight: float
    allocation_eligible: bool
    execution_eligible: bool
    observation_enabled: bool

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, known_sleeve_ids: frozenset[str]
    ) -> "EligibleSleeve":
        if not isinstance(payload, Mapping):
            raise DeploymentPolicyError("eligible sleeve row must be a JSON object")
        _require_exact_fields(payload, _SLEEVE_FIELDS, label="eligible sleeve row")
        sleeve_id = _required_string(payload["sleeve_id"], label="sleeve_id", safe_id=True)
        if sleeve_id not in known_sleeve_ids:
            raise DeploymentPolicyError(f"unknown sleeve_id: {sleeve_id}")
        minimum = _weight(payload["minimum_weight"], label=f"{sleeve_id}.minimum_weight")
        maximum = _weight(payload["maximum_weight"], label=f"{sleeve_id}.maximum_weight")
        initial = _weight(payload["initial_weight"], label=f"{sleeve_id}.initial_weight")
        if minimum > maximum:
            raise DeploymentPolicyError(f"{sleeve_id}: minimum_weight exceeds maximum_weight")
        if initial < minimum or initial > maximum:
            raise DeploymentPolicyError(
                f"{sleeve_id}: initial_weight must be within minimum/maximum bounds"
            )
        allocation_eligible = _required_bool(
            payload["allocation_eligible"], label=f"{sleeve_id}.allocation_eligible"
        )
        execution_eligible = _required_bool(
            payload["execution_eligible"], label=f"{sleeve_id}.execution_eligible"
        )
        observation_enabled = _required_bool(
            payload["observation_enabled"], label=f"{sleeve_id}.observation_enabled"
        )
        if not allocation_eligible and (initial != 0.0 or minimum != 0.0):
            raise DeploymentPolicyError(
                f"{sleeve_id}: allocation-ineligible sleeve must have zero initial/minimum weight"
            )
        if execution_eligible and not allocation_eligible:
            raise DeploymentPolicyError(
                f"{sleeve_id}: execution eligibility requires allocation eligibility"
            )
        return cls(
            sleeve_id=sleeve_id,
            minimum_weight=minimum,
            maximum_weight=maximum,
            initial_weight=initial,
            allocation_eligible=allocation_eligible,
            execution_eligible=execution_eligible,
            observation_enabled=observation_enabled,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sleeve_id": self.sleeve_id,
            "minimum_weight": self.minimum_weight,
            "maximum_weight": self.maximum_weight,
            "initial_weight": self.initial_weight,
            "allocation_eligible": self.allocation_eligible,
            "execution_eligible": self.execution_eligible,
            "observation_enabled": self.observation_enabled,
        }


@dataclass(frozen=True)
class LaneDeployment:
    lane_id: str
    lane_kind: str
    enabled: bool
    account_id_hash: str
    broker_environment: str
    performance_surface: str
    eligible_sleeves: tuple[EligibleSleeve, ...]
    allocator_policy: Mapping[str, Any]
    risk_policy: Mapping[str, Any]
    capital_policy: Mapping[str, Any]
    execution_policy: Mapping[str, Any]
    reconciliation_policy: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, known_sleeve_ids: frozenset[str]
    ) -> "LaneDeployment":
        if not isinstance(payload, Mapping):
            raise DeploymentPolicyError("lane must be a JSON object")
        _require_exact_fields(payload, _LANE_FIELDS, label="lane")
        lane_id = _required_string(payload["lane_id"], label="lane_id", safe_id=True)
        lane_kind = _required_string(payload["lane_kind"], label=f"{lane_id}.lane_kind")
        if lane_kind not in VALID_LANE_KINDS:
            raise DeploymentPolicyError(f"{lane_id}: unsupported lane_kind {lane_kind!r}")
        enabled = _required_bool(payload["enabled"], label=f"{lane_id}.enabled")
        rows = payload["eligible_sleeves"]
        if not isinstance(rows, list) or not rows:
            raise DeploymentPolicyError(f"{lane_id}.eligible_sleeves must be a non-empty list")
        sleeves = tuple(
            EligibleSleeve.from_dict(row, known_sleeve_ids=known_sleeve_ids) for row in rows
        )
        sleeve_ids = [row.sleeve_id for row in sleeves]
        if len(sleeve_ids) != len(set(sleeve_ids)):
            raise DeploymentPolicyError(f"{lane_id}: duplicate eligible sleeve_id")
        if lane_kind == "SHADOW" and any(row.execution_eligible for row in sleeves):
            raise DeploymentPolicyError(f"{lane_id}: SHADOW sleeves cannot be execution eligible")
        allocatable = tuple(row for row in sleeves if row.allocation_eligible)
        if enabled and not allocatable:
            raise DeploymentPolicyError(f"{lane_id}: enabled lane has no allocation-eligible sleeve")
        if allocatable:
            total_initial = sum(row.initial_weight for row in allocatable)
            if not math.isclose(total_initial, 1.0, rel_tol=0.0, abs_tol=1e-9):
                raise DeploymentPolicyError(
                    f"{lane_id}: allocation-eligible initial weights must sum to 1.0"
                )
            if sum(row.minimum_weight for row in allocatable) > 1.0 + 1e-9:
                raise DeploymentPolicyError(f"{lane_id}: minimum weights are infeasible")
            if sum(row.maximum_weight for row in allocatable) < 1.0 - 1e-9:
                raise DeploymentPolicyError(f"{lane_id}: maximum weights are infeasible")
        return cls(
            lane_id=lane_id,
            lane_kind=lane_kind,
            enabled=enabled,
            account_id_hash=_sha256(
                payload["account_id_hash"], label=f"{lane_id}.account_id_hash"
            ),
            broker_environment=_required_string(
                payload["broker_environment"], label=f"{lane_id}.broker_environment"
            ),
            performance_surface=_required_string(
                payload["performance_surface"], label=f"{lane_id}.performance_surface"
            ),
            eligible_sleeves=sleeves,
            allocator_policy=_policy_object(
                payload["allocator_policy"], label=f"{lane_id}.allocator_policy"
            ),
            risk_policy=_policy_object(payload["risk_policy"], label=f"{lane_id}.risk_policy"),
            capital_policy=_policy_object(
                payload["capital_policy"], label=f"{lane_id}.capital_policy"
            ),
            execution_policy=_policy_object(
                payload["execution_policy"], label=f"{lane_id}.execution_policy"
            ),
            reconciliation_policy=_policy_object(
                payload["reconciliation_policy"], label=f"{lane_id}.reconciliation_policy"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "lane_kind": self.lane_kind,
            "enabled": self.enabled,
            "account_id_hash": self.account_id_hash,
            "broker_environment": self.broker_environment,
            "performance_surface": self.performance_surface,
            "eligible_sleeves": [row.to_dict() for row in self.eligible_sleeves],
            "allocator_policy": _thaw_json(self.allocator_policy),
            "risk_policy": _thaw_json(self.risk_policy),
            "capital_policy": _thaw_json(self.capital_policy),
            "execution_policy": _thaw_json(self.execution_policy),
            "reconciliation_policy": _thaw_json(self.reconciliation_policy),
        }


@dataclass(frozen=True)
class LaneDeploymentPolicy:
    deployment_version: str
    status: str
    approved_by: str
    owner_decision_id: str
    approved_at: str
    effective_session: str
    prior_deployment_version: str
    rollback_deployment_version: str
    lanes: tuple[LaneDeployment, ...]
    content_hash: str

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        known_sleeve_ids: Iterable[str],
        verify_hash: bool = True,
    ) -> "LaneDeploymentPolicy":
        if not isinstance(payload, Mapping):
            raise DeploymentPolicyError("deployment policy must be a JSON object")
        _require_exact_fields(payload, _TOP_LEVEL_FIELDS, label="deployment policy")
        if payload["schema_version"] != DEPLOYMENT_POLICY_SCHEMA:
            raise DeploymentPolicyError(
                f"unsupported deployment policy schema: {payload['schema_version']!r}"
            )
        known = frozenset(
            _required_string(item, label="known sleeve_id", safe_id=True)
            for item in known_sleeve_ids
        )
        if not known:
            raise DeploymentPolicyError("known_sleeve_ids must not be empty")
        raw_lanes = payload["lanes"]
        if not isinstance(raw_lanes, list) or not raw_lanes:
            raise DeploymentPolicyError("deployment policy lanes must be a non-empty list")
        lanes = tuple(
            LaneDeployment.from_dict(row, known_sleeve_ids=known) for row in raw_lanes
        )
        lane_ids = [lane.lane_id for lane in lanes]
        if len(lane_ids) != len(set(lane_ids)):
            raise DeploymentPolicyError("deployment policy contains duplicate lane_id values")
        deployment_version = _required_string(
            payload["deployment_version"], label="deployment_version", safe_id=True
        )
        prior = _required_string(
            payload["prior_deployment_version"],
            label="prior_deployment_version",
            safe_id=True,
        )
        rollback = _required_string(
            payload["rollback_deployment_version"],
            label="rollback_deployment_version",
            safe_id=True,
        )
        if deployment_version in {prior, rollback}:
            raise DeploymentPolicyError(
                "deployment_version must differ from prior and rollback deployment versions"
            )
        status = _required_string(payload["status"], label="status")
        if status not in VALID_DEPLOYMENT_STATUSES:
            raise DeploymentPolicyError(f"unsupported deployment status: {status!r}")
        approved_at = _iso_timestamp(payload["approved_at"], label="approved_at")
        effective_session = _iso_date(
            payload["effective_session"], label="effective_session"
        )
        approved_date = dt.datetime.fromisoformat(
            approved_at.replace("Z", "+00:00")
        ).date()
        if dt.date.fromisoformat(effective_session) < approved_date:
            raise DeploymentPolicyError("effective_session cannot precede approved_at")
        declared_hash = _sha256(payload["content_hash"], label="content_hash")
        expected_hash = artifact_content_hash(payload)
        if verify_hash and declared_hash != expected_hash:
            raise DeploymentPolicyError("deployment policy content_hash mismatch")
        return cls(
            deployment_version=deployment_version,
            status=status,
            approved_by=_required_string(payload["approved_by"], label="approved_by"),
            owner_decision_id=_required_string(
                payload["owner_decision_id"], label="owner_decision_id", safe_id=True
            ),
            approved_at=approved_at,
            effective_session=effective_session,
            prior_deployment_version=prior,
            rollback_deployment_version=rollback,
            lanes=lanes,
            content_hash=declared_hash,
        )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": DEPLOYMENT_POLICY_SCHEMA,
            "deployment_version": self.deployment_version,
            "status": self.status,
            "approved_by": self.approved_by,
            "owner_decision_id": self.owner_decision_id,
            "approved_at": self.approved_at,
            "effective_session": self.effective_session,
            "prior_deployment_version": self.prior_deployment_version,
            "rollback_deployment_version": self.rollback_deployment_version,
            "lanes": [lane.to_dict() for lane in self.lanes],
        }
        if include_hash:
            payload["content_hash"] = self.content_hash
        return payload


def parse_deployment_policy(
    payload: Mapping[str, Any],
    *,
    known_sleeve_ids: Iterable[str],
    verify_hash: bool = True,
) -> LaneDeploymentPolicy:
    return LaneDeploymentPolicy.from_dict(
        payload, known_sleeve_ids=known_sleeve_ids, verify_hash=verify_hash
    )


def read_deployment_policy(
    path: Path | str,
    *,
    known_sleeve_ids: Iterable[str],
    verify_hash: bool = True,
) -> LaneDeploymentPolicy:
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
        payload = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except DeploymentPolicyError:
        raise
    except Exception as exc:
        raise DeploymentPolicyError(f"cannot read deployment policy {source}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise DeploymentPolicyError(f"deployment policy {source} must contain a JSON object")
    return parse_deployment_policy(
        payload, known_sleeve_ids=known_sleeve_ids, verify_hash=verify_hash
    )


def require_single_active_deployment(
    policies: Iterable[LaneDeploymentPolicy],
) -> LaneDeploymentPolicy:
    """Select exactly one ACTIVE policy from an already validated collection."""

    rows = tuple(policies)
    versions = [row.deployment_version for row in rows]
    if len(versions) != len(set(versions)):
        raise DeploymentPolicyError("duplicate deployment_version values")
    active = tuple(row for row in rows if row.status == "ACTIVE")
    if len(active) != 1:
        raise DeploymentPolicyError(
            f"expected exactly one ACTIVE deployment version, found {len(active)}"
        )
    pending_sessions = [
        row.effective_session for row in rows if row.status == "PENDING"
    ]
    if len(pending_sessions) != len(set(pending_sessions)):
        raise DeploymentPolicyError("PENDING deployment versions overlap an effective session")
    return active[0]
