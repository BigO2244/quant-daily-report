"""Typed, immutable contracts for Alpha Lab observations and experiments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

from .canonical import (
    canonical_hash,
    format_datetime,
    require_non_empty,
    require_sha256,
)
from .errors import ContractValidationError


_HYPOTHESIS_ID = re.compile(r"^HYP-\d{4}-\d{3}$")
_EXPERIMENT_ID = re.compile(r"^EXP-\d{4}-\d{4}$")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError("{} must be timezone-aware".format(field_name))


def _freeze_json(value: Any) -> Any:
    """Deep-freeze a JSON-like value so frozen observations are actually immutable."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


class HypothesisClassification(str, Enum):
    ALPHA_CANDIDATE = "ALPHA_CANDIDATE"
    FACTOR_HARVEST_CANDIDATE = "FACTOR_HARVEST_CANDIDATE"
    DIVERSIFIER_CANDIDATE = "DIVERSIFIER_CANDIDATE"
    PROTECTION_CANDIDATE = "PROTECTION_CANDIDATE"
    EXECUTION_EDGE_CANDIDATE = "EXECUTION_EDGE_CANDIDATE"


class RunState(str, Enum):
    FROZEN = "FROZEN"
    RUNNING = "RUNNING"
    BLOCKED_DATA = "BLOCKED_DATA"
    REVIEW = "REVIEW"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Observation:
    """A source observation with explicit event and availability timestamps."""

    source_id: str
    security_id: str
    observed_at: datetime
    available_at: datetime
    payload: Mapping[str, Any]
    payload_hash: str
    schema_version: str = "caerus_alpha_lab_observation_v1"

    def __post_init__(self) -> None:
        require_non_empty(self.source_id, "source_id")
        require_non_empty(self.security_id, "security_id")
        require_non_empty(self.schema_version, "schema_version")
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.available_at, "available_at")
        if self.available_at < self.observed_at:
            raise ContractValidationError("available_at cannot precede observed_at")
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError("payload must be a mapping")
        frozen_payload = _freeze_json(self.payload)
        object.__setattr__(self, "payload", frozen_payload)
        require_sha256(self.payload_hash, "payload_hash")
        if canonical_hash(frozen_payload) != self.payload_hash:
            raise ContractValidationError("payload_hash does not match canonical payload")

    @classmethod
    def create(
        cls,
        *,
        source_id: str,
        security_id: str,
        observed_at: datetime,
        available_at: datetime,
        payload: Mapping[str, Any]
    ) -> "Observation":
        return cls(
            source_id=source_id,
            security_id=security_id,
            observed_at=observed_at,
            available_at=available_at,
            payload=payload,
            payload_hash=canonical_hash(payload),
        )

    def require_consumable_at(self, decision_timestamp: datetime) -> None:
        """Fail closed unless this observation existed by the model decision time."""

        _require_aware(decision_timestamp, "decision_timestamp")
        if self.available_at > decision_timestamp:
            raise ContractValidationError(
                "observation is unavailable at the requested decision timestamp"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "security_id": self.security_id,
            "observed_at": format_datetime(self.observed_at),
            "available_at": format_datetime(self.available_at),
            "payload": self.payload,
            "payload_hash": self.payload_hash,
        }


@dataclass(frozen=True)
class ExperimentDesign:
    primary_metric: str
    benchmark: str
    risk_model: str
    holding_horizon: str
    cost_model: str
    challenge_period: str
    maximum_variants: int
    pass_criteria: Tuple[str, ...]
    kill_criteria: Tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "primary_metric",
            "benchmark",
            "risk_model",
            "holding_horizon",
            "cost_model",
            "challenge_period",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        if not isinstance(self.maximum_variants, int) or self.maximum_variants < 1:
            raise ContractValidationError("maximum_variants must be a positive integer")
        if not self.pass_criteria or not all(item.strip() for item in self.pass_criteria):
            raise ContractValidationError("pass_criteria must contain non-empty criteria")
        if not self.kill_criteria or not all(item.strip() for item in self.kill_criteria):
            raise ContractValidationError("kill_criteria must contain non-empty criteria")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_metric": self.primary_metric,
            "benchmark": self.benchmark,
            "risk_model": self.risk_model,
            "holding_horizon": self.holding_horizon,
            "cost_model": self.cost_model,
            "challenge_period": self.challenge_period,
            "maximum_variants": self.maximum_variants,
            "pass_criteria": self.pass_criteria,
            "kill_criteria": self.kill_criteria,
        }


@dataclass(frozen=True)
class HypothesisManifest:
    hypothesis_id: str
    title: str
    claim: str
    classification: HypothesisClassification
    frozen_at: datetime
    data_contract_ids: Tuple[str, ...]
    design: ExperimentDesign
    schema_version: str = "caerus_alpha_lab_hypothesis_manifest_v1"

    def __post_init__(self) -> None:
        if not isinstance(self.hypothesis_id, str) or not _HYPOTHESIS_ID.fullmatch(
            self.hypothesis_id
        ):
            raise ContractValidationError("hypothesis_id must match HYP-YYYY-NNN")
        require_non_empty(self.title, "title")
        require_non_empty(self.claim, "claim")
        require_non_empty(self.schema_version, "schema_version")
        if not isinstance(self.classification, HypothesisClassification):
            raise ContractValidationError("classification must be a HypothesisClassification")
        _require_aware(self.frozen_at, "frozen_at")
        if not self.data_contract_ids or not all(item.strip() for item in self.data_contract_ids):
            raise ContractValidationError("data_contract_ids must contain non-empty identifiers")
        if len(set(self.data_contract_ids)) != len(self.data_contract_ids):
            raise ContractValidationError("data_contract_ids cannot contain duplicates")
        if not isinstance(self.design, ExperimentDesign):
            raise ContractValidationError("design must be an ExperimentDesign")

    @property
    def manifest_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "claim": self.claim,
            "classification": self.classification.value,
            "frozen_at": format_datetime(self.frozen_at),
            "data_contract_ids": self.data_contract_ids,
            "design": self.design.to_dict(),
        }


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    experiment_id: str
    hypothesis_id: str
    state: RunState
    created_at: datetime
    hypothesis_hash: str
    code_hash: str
    data_snapshot_hash: str
    provider_gate_hash: str
    schema_version: str = "caerus_alpha_lab_run_manifest_v1"

    def __post_init__(self) -> None:
        require_non_empty(self.run_id, "run_id")
        if not _EXPERIMENT_ID.fullmatch(self.experiment_id):
            raise ContractValidationError("experiment_id must match EXP-YYYY-NNNN")
        if not _HYPOTHESIS_ID.fullmatch(self.hypothesis_id):
            raise ContractValidationError("hypothesis_id must match HYP-YYYY-NNN")
        if not isinstance(self.state, RunState):
            raise ContractValidationError("state must be a RunState")
        _require_aware(self.created_at, "created_at")
        for field_name in (
            "hypothesis_hash",
            "code_hash",
            "data_snapshot_hash",
            "provider_gate_hash",
        ):
            require_sha256(getattr(self, field_name), field_name)

    @property
    def manifest_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "state": self.state.value,
            "created_at": format_datetime(self.created_at),
            "hypothesis_hash": self.hypothesis_hash,
            "code_hash": self.code_hash,
            "data_snapshot_hash": self.data_snapshot_hash,
            "provider_gate_hash": self.provider_gate_hash,
        }
