"""Typed contracts for candidate assessment and CIO review artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from projects.alpha_lab.factory.canonical import (
    canonical_hash,
    format_datetime,
    parse_datetime,
    require_non_empty,
    require_sha256,
)
from projects.alpha_lab.factory.contracts import _require_aware
from projects.alpha_lab.factory.errors import ContractValidationError


_HYPOTHESIS_ID = re.compile(r"^HYP-\d{4}-\d{3}$")
_EXPERIMENT_ID = re.compile(r"^EXP-\d{4}-\d{4}$")
_REQUIREMENT_ID = re.compile(r"^[a-z][a-z0-9._-]{2,127}$")


class AccessMode(str, Enum):
    FREE = "FREE"
    EXISTING_LICENSE = "EXISTING_LICENSE"
    TRIAL = "TRIAL"
    PAID = "PAID"


class DataStatus(str, Enum):
    MISSING = "MISSING"
    REQUEST_DRAFTED = "REQUEST_DRAFTED"
    OWNER_APPROVED = "OWNER_APPROVED"
    ACQUIRING = "ACQUIRING"
    ACQUIRED_UNVERIFIED = "ACQUIRED_UNVERIFIED"
    CERTIFIED_READY = "CERTIFIED_READY"
    DECLINED = "DECLINED"


class ResearchVerdict(str, Enum):
    ITERATE = "ITERATE"
    REJECT = "REJECT"
    EVIDENCE_READY_FOR_OWNER_REVIEW = "EVIDENCE_READY_FOR_OWNER_REVIEW"
    PENDING = "PENDING"


class OwnerDecision(str, Enum):
    PENDING = "PENDING"
    PURSUE = "PURSUE"
    PARK = "PARK"
    KILL = "KILL"


class ShadowStatus(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    AWAITING_OWNER_APPROVAL = "AWAITING_OWNER_APPROVAL"
    APPROVED = "APPROVED"
    OBSERVING = "OBSERVING"
    COMPLETE = "COMPLETE"
    DECLINED = "DECLINED"


class QueueItemType(str, Enum):
    DATA_ACCESS_REVIEW = "DATA_ACCESS_REVIEW"
    RESEARCH_DECISION_REVIEW = "RESEARCH_DECISION_REVIEW"
    SHADOW_ACTIVATION_REVIEW = "SHADOW_ACTIVATION_REVIEW"
    SHADOW_CHECKPOINT_REVIEW = "SHADOW_CHECKPOINT_REVIEW"
    PAPER_PROMOTION_REVIEW = "PAPER_PROMOTION_REVIEW"


@dataclass(frozen=True)
class EvidenceReference:
    artifact: str
    sha256: str
    label: str

    def __post_init__(self) -> None:
        require_non_empty(self.artifact, "artifact")
        require_sha256(self.sha256, "sha256")
        require_non_empty(self.label, "label")

    def to_dict(self) -> Dict[str, Any]:
        return {"artifact": self.artifact, "sha256": self.sha256, "label": self.label}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceReference":
        return cls(
            artifact=value["artifact"],
            sha256=value["sha256"],
            label=value["label"],
        )


@dataclass(frozen=True)
class DataRequirement:
    requirement_id: str
    provider_id: str
    dataset_id: str
    purpose: str
    access_mode: AccessMode
    status: DataStatus
    required_fields: Tuple[str, ...]
    acceptance_criteria: Tuple[str, ...]
    estimated_one_time_cost_usd: Optional[float] = None
    estimated_monthly_cost_usd: Optional[float] = None
    provider_url: Optional[str] = None
    free_alternative: Optional[str] = None

    def __post_init__(self) -> None:
        if not _REQUIREMENT_ID.fullmatch(self.requirement_id):
            raise ContractValidationError("requirement_id is not path safe")
        require_non_empty(self.provider_id, "provider_id")
        require_non_empty(self.dataset_id, "dataset_id")
        require_non_empty(self.purpose, "purpose")
        if not isinstance(self.access_mode, AccessMode):
            raise ContractValidationError("access_mode must be an AccessMode")
        if not isinstance(self.status, DataStatus):
            raise ContractValidationError("status must be a DataStatus")
        if not self.required_fields or not all(str(item).strip() for item in self.required_fields):
            raise ContractValidationError("required_fields cannot be empty")
        if not self.acceptance_criteria or not all(
            str(item).strip() for item in self.acceptance_criteria
        ):
            raise ContractValidationError("acceptance_criteria cannot be empty")
        for name in ("estimated_one_time_cost_usd", "estimated_monthly_cost_usd"):
            amount = getattr(self, name)
            if amount is not None and (not isinstance(amount, (int, float)) or amount < 0):
                raise ContractValidationError("{} must be a non-negative number".format(name))
        if self.access_mode in {AccessMode.TRIAL, AccessMode.PAID} and not self.provider_url:
            raise ContractValidationError("paid or trial data requires provider_url")

    @property
    def ready(self) -> bool:
        return self.status is DataStatus.CERTIFIED_READY

    @property
    def needs_owner_review(self) -> bool:
        return self.access_mode in {AccessMode.TRIAL, AccessMode.PAID} and self.status in {
            DataStatus.MISSING,
            DataStatus.REQUEST_DRAFTED,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "provider_id": self.provider_id,
            "dataset_id": self.dataset_id,
            "purpose": self.purpose,
            "access_mode": self.access_mode.value,
            "status": self.status.value,
            "required_fields": self.required_fields,
            "acceptance_criteria": self.acceptance_criteria,
            "estimated_one_time_cost_usd": self.estimated_one_time_cost_usd,
            "estimated_monthly_cost_usd": self.estimated_monthly_cost_usd,
            "provider_url": self.provider_url,
            "free_alternative": self.free_alternative,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DataRequirement":
        return cls(
            requirement_id=value["requirement_id"],
            provider_id=value["provider_id"],
            dataset_id=value["dataset_id"],
            purpose=value["purpose"],
            access_mode=AccessMode(value["access_mode"]),
            status=DataStatus(value["status"]),
            required_fields=tuple(value["required_fields"]),
            acceptance_criteria=tuple(value["acceptance_criteria"]),
            estimated_one_time_cost_usd=value.get("estimated_one_time_cost_usd"),
            estimated_monthly_cost_usd=value.get("estimated_monthly_cost_usd"),
            provider_url=value.get("provider_url"),
            free_alternative=value.get("free_alternative"),
        )


@dataclass(frozen=True)
class CandidateSnapshot:
    hypothesis_id: str
    experiment_id: str
    title: str
    technique_family: str
    economic_mechanism: str
    classification: str
    captured_at: datetime
    research_verdict: ResearchVerdict
    research_gates: Mapping[str, bool]
    owner_research_decision: OwnerDecision
    shadow_status: ShadowStatus
    shadow_observation_days: int
    shadow_review_checkpoints: Tuple[int, ...]
    last_reviewed_shadow_checkpoint: int
    shadow_gates: Mapping[str, bool]
    data_requirements: Tuple[DataRequirement, ...]
    evidence: Tuple[EvidenceReference, ...]
    source_snapshot_hash: str
    schema_version: str = "caerus_alpha_lab_candidate_snapshot_v1"

    def __post_init__(self) -> None:
        if not _HYPOTHESIS_ID.fullmatch(self.hypothesis_id):
            raise ContractValidationError("hypothesis_id must match HYP-YYYY-NNN")
        if not _EXPERIMENT_ID.fullmatch(self.experiment_id):
            raise ContractValidationError("experiment_id must match EXP-YYYY-NNNN")
        for name in ("title", "technique_family", "economic_mechanism", "classification"):
            require_non_empty(getattr(self, name), name)
        _require_aware(self.captured_at, "captured_at")
        if not isinstance(self.research_verdict, ResearchVerdict):
            raise ContractValidationError("research_verdict is invalid")
        if not isinstance(self.owner_research_decision, OwnerDecision):
            raise ContractValidationError("owner_research_decision is invalid")
        if not isinstance(self.shadow_status, ShadowStatus):
            raise ContractValidationError("shadow_status is invalid")
        if self.shadow_observation_days < 0 or self.last_reviewed_shadow_checkpoint < 0:
            raise ContractValidationError("shadow day counts cannot be negative")
        if not self.shadow_review_checkpoints or any(
            not isinstance(item, int) or item < 1 for item in self.shadow_review_checkpoints
        ):
            raise ContractValidationError("shadow_review_checkpoints must be positive integers")
        if tuple(sorted(set(self.shadow_review_checkpoints))) != self.shadow_review_checkpoints:
            raise ContractValidationError("shadow_review_checkpoints must be unique and sorted")
        if self.last_reviewed_shadow_checkpoint not in (0,) + self.shadow_review_checkpoints:
            raise ContractValidationError("last reviewed checkpoint was not frozen")
        for name, gates in (("research_gates", self.research_gates), ("shadow_gates", self.shadow_gates)):
            if not isinstance(gates, Mapping) or not gates:
                raise ContractValidationError("{} cannot be empty".format(name))
            if not all(isinstance(key, str) and key.strip() for key in gates):
                raise ContractValidationError("{} names must be non-empty".format(name))
            if not all(isinstance(value, bool) for value in gates.values()):
                raise ContractValidationError("{} values must be boolean".format(name))
        requirement_ids = [item.requirement_id for item in self.data_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ContractValidationError("data requirement IDs cannot repeat")
        require_sha256(self.source_snapshot_hash, "source_snapshot_hash")

    @property
    def final_shadow_checkpoint(self) -> int:
        return self.shadow_review_checkpoints[-1]

    def unsigned_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "title": self.title,
            "technique_family": self.technique_family,
            "economic_mechanism": self.economic_mechanism,
            "classification": self.classification,
            "captured_at": format_datetime(self.captured_at),
            "research_verdict": self.research_verdict.value,
            "research_gates": dict(self.research_gates),
            "owner_research_decision": self.owner_research_decision.value,
            "shadow_status": self.shadow_status.value,
            "shadow_observation_days": self.shadow_observation_days,
            "shadow_review_checkpoints": self.shadow_review_checkpoints,
            "last_reviewed_shadow_checkpoint": self.last_reviewed_shadow_checkpoint,
            "shadow_gates": dict(self.shadow_gates),
            "data_requirements": [item.to_dict() for item in self.data_requirements],
            "evidence": [item.to_dict() for item in self.evidence],
        }

    def to_dict(self) -> Dict[str, Any]:
        payload = self.unsigned_dict()
        payload["source_snapshot_hash"] = self.source_snapshot_hash
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateSnapshot":
        unsigned = dict(value)
        supplied_hash = unsigned.pop("source_snapshot_hash")
        if canonical_hash(unsigned) != supplied_hash:
            raise ContractValidationError("candidate source_snapshot_hash mismatch")
        return cls(
            hypothesis_id=value["hypothesis_id"],
            experiment_id=value["experiment_id"],
            title=value["title"],
            technique_family=value["technique_family"],
            economic_mechanism=value["economic_mechanism"],
            classification=value["classification"],
            captured_at=parse_datetime(value["captured_at"]),
            research_verdict=ResearchVerdict(value["research_verdict"]),
            research_gates=dict(value["research_gates"]),
            owner_research_decision=OwnerDecision(value["owner_research_decision"]),
            shadow_status=ShadowStatus(value["shadow_status"]),
            shadow_observation_days=int(value["shadow_observation_days"]),
            shadow_review_checkpoints=tuple(value["shadow_review_checkpoints"]),
            last_reviewed_shadow_checkpoint=int(value["last_reviewed_shadow_checkpoint"]),
            shadow_gates=dict(value["shadow_gates"]),
            data_requirements=tuple(
                DataRequirement.from_dict(item) for item in value["data_requirements"]
            ),
            evidence=tuple(EvidenceReference.from_dict(item) for item in value["evidence"]),
            source_snapshot_hash=supplied_hash,
            schema_version=value.get("schema_version", "caerus_alpha_lab_candidate_snapshot_v1"),
        )


@dataclass(frozen=True)
class QueueItem:
    item_id: str
    item_type: QueueItemType
    hypothesis_id: str
    title: str
    priority: int
    summary: str
    decision_requested: str
    options: Tuple[str, ...]
    blockers: Tuple[str, ...]
    evidence: Tuple[EvidenceReference, ...]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in ("item_id", "title", "summary", "decision_requested"):
            require_non_empty(getattr(self, name), name)
        if not isinstance(self.item_type, QueueItemType):
            raise ContractValidationError("item_type is invalid")
        if not _HYPOTHESIS_ID.fullmatch(self.hypothesis_id):
            raise ContractValidationError("hypothesis_id must match HYP-YYYY-NNN")
        if self.priority not in {1, 2, 3, 4, 5}:
            raise ContractValidationError("priority must be from 1 through 5")
        if len(self.options) < 2:
            raise ContractValidationError("queue item must include at least two options")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type.value,
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "priority": self.priority,
            "summary": self.summary,
            "decision_requested": self.decision_requested,
            "options": self.options,
            "blockers": self.blockers,
            "evidence": [item.to_dict() for item in self.evidence],
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class CandidateAssessment:
    hypothesis_id: str
    state: str
    recommendation: str
    blockers: Tuple[str, ...]
    queue_items: Tuple[QueueItem, ...]
    assessed_at: datetime

    @property
    def assessment_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "caerus_alpha_lab_candidate_assessment_v1",
            "hypothesis_id": self.hypothesis_id,
            "state": self.state,
            "recommendation": self.recommendation,
            "blockers": self.blockers,
            "queue_items": [item.to_dict() for item in self.queue_items],
            "assessed_at": format_datetime(self.assessed_at),
            "trading_behavior_changed": False,
            "promotion_performed": False,
        }
