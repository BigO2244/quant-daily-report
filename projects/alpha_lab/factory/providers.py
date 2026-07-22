"""Fail-closed provider and dataset readiness gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from .canonical import canonical_hash, format_datetime, require_non_empty, require_sha256
from .contracts import _require_aware
from .errors import ContractValidationError, ProviderNotReadyError


class ProviderStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class ProviderRequirement:
    provider_id: str
    dataset_id: str
    required_fields: Tuple[str, ...]
    requires_historical_point_in_time: bool = True

    def __post_init__(self) -> None:
        require_non_empty(self.provider_id, "provider_id")
        require_non_empty(self.dataset_id, "dataset_id")
        if not self.required_fields or not all(item.strip() for item in self.required_fields):
            raise ContractValidationError("required_fields must contain non-empty fields")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ContractValidationError("required_fields cannot contain duplicates")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "dataset_id": self.dataset_id,
            "required_fields": self.required_fields,
            "requires_historical_point_in_time": self.requires_historical_point_in_time,
        }


@dataclass(frozen=True)
class ProviderReadiness:
    provider_id: str
    dataset_id: str
    status: ProviderStatus
    checked_at: datetime
    fields_available: Tuple[str, ...]
    historical_point_in_time_verified: bool
    evidence_hash: Optional[str]
    blockers: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty(self.provider_id, "provider_id")
        require_non_empty(self.dataset_id, "dataset_id")
        if not isinstance(self.status, ProviderStatus):
            raise ContractValidationError("status must be a ProviderStatus")
        _require_aware(self.checked_at, "checked_at")
        if len(set(self.fields_available)) != len(self.fields_available):
            raise ContractValidationError("fields_available cannot contain duplicates")
        if self.evidence_hash is not None:
            require_sha256(self.evidence_hash, "evidence_hash")
        if self.status is ProviderStatus.READY:
            if self.evidence_hash is None:
                raise ContractValidationError("READY provider status requires evidence_hash")
            if self.blockers:
                raise ContractValidationError("READY provider status cannot include blockers")
        elif not self.blockers:
            raise ContractValidationError("non-ready provider status requires blockers")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "dataset_id": self.dataset_id,
            "status": self.status.value,
            "checked_at": format_datetime(self.checked_at),
            "fields_available": self.fields_available,
            "historical_point_in_time_verified": self.historical_point_in_time_verified,
            "evidence_hash": self.evidence_hash,
            "blockers": self.blockers,
        }


@dataclass(frozen=True)
class ProviderGateResult:
    ready: bool
    blockers: Tuple[str, ...]
    requirement_hash: str
    readiness_hash: str

    def __post_init__(self) -> None:
        require_sha256(self.requirement_hash, "requirement_hash")
        require_sha256(self.readiness_hash, "readiness_hash")
        if self.ready and self.blockers:
            raise ContractValidationError("ready provider gate cannot include blockers")
        if not self.ready and not self.blockers:
            raise ContractValidationError("blocked provider gate requires blockers")

    @property
    def gate_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
            "blockers": self.blockers,
            "requirement_hash": self.requirement_hash,
            "readiness_hash": self.readiness_hash,
        }


def evaluate_provider_readiness(
    requirement: ProviderRequirement, readiness: ProviderReadiness
) -> ProviderGateResult:
    blockers = []
    if readiness.provider_id != requirement.provider_id:
        blockers.append("provider_id_mismatch")
    if readiness.dataset_id != requirement.dataset_id:
        blockers.append("dataset_id_mismatch")
    if readiness.status is not ProviderStatus.READY:
        blockers.append("provider_status_not_ready")
    missing_fields = sorted(set(requirement.required_fields) - set(readiness.fields_available))
    blockers.extend("missing_field:{}".format(field) for field in missing_fields)
    if (
        requirement.requires_historical_point_in_time
        and not readiness.historical_point_in_time_verified
    ):
        blockers.append("historical_point_in_time_not_verified")
    if readiness.evidence_hash is None:
        blockers.append("readiness_evidence_missing")
    blockers.extend(
        "provider_blocker:{}".format(blocker) for blocker in readiness.blockers
    )
    return ProviderGateResult(
        ready=not blockers,
        blockers=tuple(blockers),
        requirement_hash=canonical_hash(requirement.to_dict()),
        readiness_hash=canonical_hash(readiness.to_dict()),
    )


def require_provider_ready(
    requirement: ProviderRequirement, readiness: ProviderReadiness
) -> ProviderGateResult:
    result = evaluate_provider_readiness(requirement, readiness)
    if not result.ready:
        raise ProviderNotReadyError(
            "provider readiness gate failed: {}".format(", ".join(result.blockers))
        )
    return result
