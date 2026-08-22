"""Typed global research ledger for families, trials, inference, and holdouts.

The append-only JSONL event chain is the sole machine authority. Run packets,
evaluator bundles, and evidence cards remain immutable source artifacts that
the ledger references by path and hash. Markdown and Atlas are projections.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .canonical import (
    canonical_hash,
    format_datetime,
    parse_datetime,
    require_non_empty,
    require_sha256,
)
from .contracts import _require_aware
from .errors import ContractValidationError, EventStoreIntegrityError
from .store import AppendOnlyJSONLEventStore, EventRecord


_FAMILY_ID = re.compile(r"^FAM-\d{4}-\d{3}$")
_HYPOTHESIS_ID = re.compile(r"^HYP-\d{4}-\d{3}$")
_EXPERIMENT_ID = re.compile(r"^EXP-\d{4}-\d{4}$")
_ATTEMPT_ID = re.compile(r"^ATTEMPT-[0-9a-f]{16}$")
_TRIAL_ID = re.compile(r"^FAM-\d{4}-\d{3}-T\d{3}$")
_WAVE_ID = re.compile(r"^WAVE-\d{4}-\d{3}$")
_CHALLENGE_ID = re.compile(r"^CHALLENGE-\d{4}-\d{3}$")
_ACCESS_ID = re.compile(r"^ACCESS-[0-9a-f]{16}$")
_REVIEW_ID = re.compile(r"^REVIEW-[0-9a-f]{16}$")


class ResearchRunClass(str, Enum):
    DATA_GATE = "DATA_GATE"
    COLLECTION = "COLLECTION"
    TRANSFORM = "TRANSFORM"
    MODEL_TRIAL = "MODEL_TRIAL"
    ROBUSTNESS = "ROBUSTNESS"
    CHALLENGE_READ = "CHALLENGE_READ"


class ResearchPhase(str, Enum):
    DATA = "DATA"
    DISCOVERY = "DISCOVERY"
    VALIDATION = "VALIDATION"
    CHALLENGE = "CHALLENGE"


class InferenceTrack(str, Enum):
    EXPLORATORY = "EXPLORATORY"
    CONFIRMATORY = "CONFIRMATORY"


class ExpectedDirection(str, Enum):
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"


class MultipleTestingMethod(str, Enum):
    HOLM_BONFERRONI = "HOLM_BONFERRONI"
    BENJAMINI_YEKUTIELI = "BENJAMINI_YEKUTIELI"
    BENJAMINI_HOCHBERG = "BENJAMINI_HOCHBERG"
    ROMANO_WOLF = "ROMANO_WOLF"


class TrialOutcome(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


def _require_pattern(value: str, pattern: re.Pattern[str], field_name: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ContractValidationError("{} has invalid format".format(field_name))


def _require_probability(value: float, field_name: str) -> None:
    if not isinstance(value, (float, int)) or not 0.0 <= float(value) <= 1.0:
        raise ContractValidationError("{} must be a probability".format(field_name))


def _require_unique(values: Sequence[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ContractValidationError("{} cannot contain duplicates".format(field_name))


def _date_period(value: str) -> Tuple[date, date]:
    try:
        start_text, end_text = value.split("/", 1)
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
    except (AttributeError, ValueError) as exc:
        raise ContractValidationError("challenge_period must be YYYY-MM-DD/YYYY-MM-DD") from exc
    if end < start:
        raise ContractValidationError("challenge_period end cannot precede start")
    return start, end


def _periods_overlap(left: str, right: str) -> bool:
    left_start, left_end = _date_period(left)
    right_start, right_end = _date_period(right)
    return left_start <= right_end and right_start <= left_end


def _holdout_reuse_reason(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> Optional[str]:
    left_hashes = set(left["expected_input_sha256_by_trial"].values())
    right_hashes = set(right["expected_input_sha256_by_trial"].values())
    if left_hashes.intersection(right_hashes):
        return "challenge input hash is already frozen into another epoch"
    if (
        left["panel_manifest_sha256"] == right["panel_manifest_sha256"]
        and _periods_overlap(left["challenge_period"], right["challenge_period"])
    ):
        return "challenge panel has an overlapping period in another epoch"
    return None


@dataclass(frozen=True)
class ResearchWave:
    wave_id: str
    track: InferenceTrack
    family_ids: Tuple[str, ...]
    method: MultipleTestingMethod
    alpha_or_q: float
    registered_at: datetime
    policy_artifact: str
    policy_sha256: str
    owner_ratified: bool
    dependence_contract_sha256: Optional[str] = None
    legacy_policy: bool = False
    schema_version: str = "caerus_alpha_lab_research_wave_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.wave_id, _WAVE_ID, "wave_id")
        if not isinstance(self.track, InferenceTrack):
            raise ContractValidationError("track must be an InferenceTrack")
        if not isinstance(self.method, MultipleTestingMethod):
            raise ContractValidationError("method must be a MultipleTestingMethod")
        if not self.family_ids:
            raise ContractValidationError("family_ids cannot be empty")
        _require_unique(self.family_ids, "family_ids")
        for family_id in self.family_ids:
            _require_pattern(family_id, _FAMILY_ID, "family_id")
        _require_probability(self.alpha_or_q, "alpha_or_q")
        if float(self.alpha_or_q) in {0.0, 1.0}:
            raise ContractValidationError("alpha_or_q must be strictly between zero and one")
        _require_aware(self.registered_at, "registered_at")
        require_non_empty(self.policy_artifact, "policy_artifact")
        require_sha256(self.policy_sha256, "policy_sha256")
        if self.method is MultipleTestingMethod.ROMANO_WOLF:
            raise ContractValidationError("ROMANO_WOLF is a within-family method")
        if not self.legacy_policy:
            if self.track is InferenceTrack.EXPLORATORY and (
                self.method
                not in {
                    MultipleTestingMethod.BENJAMINI_YEKUTIELI,
                    MultipleTestingMethod.BENJAMINI_HOCHBERG,
                }
                or float(self.alpha_or_q) > 0.10
            ):
                raise ContractValidationError(
                    "new exploratory waves require BY/BH with q no greater than 0.10"
                )
            if self.track is InferenceTrack.CONFIRMATORY and (
                self.method is not MultipleTestingMethod.HOLM_BONFERRONI
                or float(self.alpha_or_q) > 0.05
            ):
                raise ContractValidationError(
                    "confirmatory waves require Holm with alpha no greater than 0.05"
                )
        if (
            self.method is MultipleTestingMethod.BENJAMINI_HOCHBERG
            and self.dependence_contract_sha256 is None
        ):
            raise ContractValidationError("BH requires a frozen dependence contract")
        if self.dependence_contract_sha256 is not None:
            require_sha256(self.dependence_contract_sha256, "dependence_contract_sha256")
        if not isinstance(self.owner_ratified, bool) or not isinstance(
            self.legacy_policy, bool
        ):
            raise ContractValidationError("wave policy flags must be boolean")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "wave_id": self.wave_id,
            "track": self.track.value,
            "family_ids": list(self.family_ids),
            "method": self.method.value,
            "alpha_or_q": float(self.alpha_or_q),
            "registered_at": format_datetime(self.registered_at),
            "policy_artifact": self.policy_artifact,
            "policy_sha256": self.policy_sha256,
            "owner_ratified": self.owner_ratified,
            "dependence_contract_sha256": self.dependence_contract_sha256,
            "legacy_policy": self.legacy_policy,
        }


@dataclass(frozen=True)
class HypothesisFamily:
    family_id: str
    wave_id: str
    challenge_epoch_id: str
    name: str
    economic_mechanism: str
    family_scope_hash: str
    primary_metric: str
    benchmark: str
    expected_direction: ExpectedDirection
    null_value: float
    economic_hurdle: float
    primary_variant_id: str
    maximum_trial_units: int
    selection_trial_budget: int
    within_family_method: MultipleTestingMethod
    family_alpha: float
    registered_at: datetime
    source_artifact: str
    source_sha256: str
    owner_ratified: bool
    parent_family_ids: Tuple[str, ...] = ()
    schema_version: str = "caerus_alpha_lab_hypothesis_family_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.family_id, _FAMILY_ID, "family_id")
        _require_pattern(self.wave_id, _WAVE_ID, "wave_id")
        _require_pattern(self.challenge_epoch_id, _CHALLENGE_ID, "challenge_epoch_id")
        _require_unique(self.parent_family_ids, "parent_family_ids")
        for parent in self.parent_family_ids:
            _require_pattern(parent, _FAMILY_ID, "parent_family_id")
            if parent == self.family_id:
                raise ContractValidationError("family cannot parent itself")
        for name in (
            "name",
            "economic_mechanism",
            "primary_metric",
            "benchmark",
            "source_artifact",
            "schema_version",
        ):
            require_non_empty(getattr(self, name), name)
        require_sha256(self.family_scope_hash, "family_scope_hash")
        require_sha256(self.source_sha256, "source_sha256")
        if not isinstance(self.expected_direction, ExpectedDirection):
            raise ContractValidationError("expected_direction must be an ExpectedDirection")
        if not isinstance(self.null_value, (float, int)) or not isinstance(
            self.economic_hurdle, (float, int)
        ):
            raise ContractValidationError("null_value and economic_hurdle must be numeric")
        if float(self.economic_hurdle) < 0.0:
            raise ContractValidationError("economic_hurdle cannot be negative")
        require_non_empty(self.primary_variant_id, "primary_variant_id")
        _require_aware(self.registered_at, "registered_at")
        if not isinstance(self.maximum_trial_units, int) or self.maximum_trial_units < 1:
            raise ContractValidationError("maximum_trial_units must be positive")
        if not isinstance(self.selection_trial_budget, int) or self.selection_trial_budget < 0:
            raise ContractValidationError("selection_trial_budget cannot be negative")
        if self.within_family_method not in {
            MultipleTestingMethod.ROMANO_WOLF,
            MultipleTestingMethod.HOLM_BONFERRONI,
        }:
            raise ContractValidationError("within-family inference must control FWER")
        _require_probability(self.family_alpha, "family_alpha")
        if float(self.family_alpha) in {0.0, 1.0}:
            raise ContractValidationError("family_alpha must be strictly between zero and one")
        if float(self.family_alpha) > 0.10:
            raise ContractValidationError("family_alpha cannot exceed 0.10")
        if not isinstance(self.owner_ratified, bool):
            raise ContractValidationError("owner_ratified must be boolean")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "family_id": self.family_id,
            "wave_id": self.wave_id,
            "challenge_epoch_id": self.challenge_epoch_id,
            "name": self.name,
            "economic_mechanism": self.economic_mechanism,
            "family_scope_hash": self.family_scope_hash,
            "primary_metric": self.primary_metric,
            "benchmark": self.benchmark,
            "expected_direction": self.expected_direction.value,
            "null_value": float(self.null_value),
            "economic_hurdle": float(self.economic_hurdle),
            "primary_variant_id": self.primary_variant_id,
            "maximum_trial_units": self.maximum_trial_units,
            "selection_trial_budget": self.selection_trial_budget,
            "within_family_method": self.within_family_method.value,
            "family_alpha": float(self.family_alpha),
            "registered_at": format_datetime(self.registered_at),
            "source_artifact": self.source_artifact,
            "source_sha256": self.source_sha256,
            "owner_ratified": self.owner_ratified,
            "parent_family_ids": list(self.parent_family_ids),
        }


@dataclass(frozen=True)
class ResearchExperiment:
    experiment_id: str
    family_id: str
    hypothesis_id: str
    parent_experiment_ids: Tuple[str, ...]
    generated_after_results: bool
    generation_reason: str
    frozen_primary_metric: str
    registered_at: datetime
    source_artifact: str
    source_sha256: str
    owner_ratified: bool
    schema_version: str = "caerus_alpha_lab_research_experiment_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.experiment_id, _EXPERIMENT_ID, "experiment_id")
        _require_pattern(self.family_id, _FAMILY_ID, "family_id")
        _require_pattern(self.hypothesis_id, _HYPOTHESIS_ID, "hypothesis_id")
        _require_unique(self.parent_experiment_ids, "parent_experiment_ids")
        for parent in self.parent_experiment_ids:
            _require_pattern(parent, _EXPERIMENT_ID, "parent_experiment_id")
            if parent == self.experiment_id:
                raise ContractValidationError("experiment cannot parent itself")
        if self.generation_reason not in {
            "INITIAL",
            "PRE_RESULT_REFINEMENT",
            "POST_RESULT_ITERATION",
            "LEGACY_IMPORT",
        }:
            raise ContractValidationError("generation_reason is invalid")
        if self.generated_after_results and not self.parent_experiment_ids:
            raise ContractValidationError(
                "post-result generation requires parent experiment lineage"
            )
        require_non_empty(self.frozen_primary_metric, "frozen_primary_metric")
        require_non_empty(self.source_artifact, "source_artifact")
        require_sha256(self.source_sha256, "source_sha256")
        _require_aware(self.registered_at, "registered_at")
        if not isinstance(self.generated_after_results, bool) or not isinstance(
            self.owner_ratified, bool
        ):
            raise ContractValidationError("experiment lineage flags must be boolean")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "family_id": self.family_id,
            "hypothesis_id": self.hypothesis_id,
            "parent_experiment_ids": list(self.parent_experiment_ids),
            "generated_after_results": self.generated_after_results,
            "generation_reason": self.generation_reason,
            "frozen_primary_metric": self.frozen_primary_metric,
            "registered_at": format_datetime(self.registered_at),
            "source_artifact": self.source_artifact,
            "source_sha256": self.source_sha256,
            "owner_ratified": self.owner_ratified,
        }


@dataclass(frozen=True)
class ResearchRun:
    attempt_id: str
    family_id: str
    hypothesis_id: str
    experiment_id: str
    run_id: str
    run_class: ResearchRunClass
    phase: ResearchPhase
    occurred_at: datetime
    source_artifact: str
    source_sha256: str
    statistical_trial_id: Optional[str] = None
    parent_trial_id: Optional[str] = None
    primary_metric: Optional[str] = None
    variant_id: Optional[str] = None
    variant_definition_hash: Optional[str] = None
    consumes_trial_budget: bool = False
    preregistered: bool = False
    outcome_data_accessed: bool = False
    challenge_accessed: bool = False
    legacy_accounting_quality: str = "COMPLETE"
    source_chain_head_hash: Optional[str] = None
    attempt_outcome: Optional[str] = None
    code_sha256: Optional[str] = None
    data_snapshot_sha256: Optional[str] = None
    evaluator_spec_sha256: Optional[str] = None
    effective_sample_floor: Optional[int] = None
    selection_trial_units: int = 0
    prespecified_non_selective: bool = False
    schema_version: str = "caerus_alpha_lab_research_attempt_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.attempt_id, _ATTEMPT_ID, "attempt_id")
        _require_pattern(self.family_id, _FAMILY_ID, "family_id")
        _require_pattern(self.hypothesis_id, _HYPOTHESIS_ID, "hypothesis_id")
        _require_pattern(self.experiment_id, _EXPERIMENT_ID, "experiment_id")
        if self.statistical_trial_id is not None:
            _require_pattern(self.statistical_trial_id, _TRIAL_ID, "statistical_trial_id")
            if not self.statistical_trial_id.startswith(self.family_id + "-T"):
                raise ContractValidationError("trial ID must be allocated within its family")
        if self.parent_trial_id is not None:
            _require_pattern(self.parent_trial_id, _TRIAL_ID, "parent_trial_id")
        for name in ("run_id", "source_artifact", "legacy_accounting_quality", "schema_version"):
            require_non_empty(getattr(self, name), name)
        require_sha256(self.source_sha256, "source_sha256")
        if self.source_chain_head_hash is not None:
            require_sha256(self.source_chain_head_hash, "source_chain_head_hash")
        if self.attempt_outcome is not None:
            require_non_empty(self.attempt_outcome, "attempt_outcome")
        if not isinstance(self.selection_trial_units, int) or self.selection_trial_units < 0:
            raise ContractValidationError("selection_trial_units cannot be negative")
        for name in (
            "consumes_trial_budget",
            "preregistered",
            "outcome_data_accessed",
            "challenge_accessed",
            "prespecified_non_selective",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ContractValidationError("{} must be boolean".format(name))
        if self.legacy_accounting_quality not in {
            "COMPLETE",
            "SOURCE_NATIVE",
            "SOURCE_NATIVE_8_CELL_GRID",
            "AGGREGATE_ONLY",
        }:
            raise ContractValidationError("legacy_accounting_quality is invalid")
        _require_aware(self.occurred_at, "occurred_at")
        if not isinstance(self.run_class, ResearchRunClass):
            raise ContractValidationError("run_class must be a ResearchRunClass")
        if not isinstance(self.phase, ResearchPhase):
            raise ContractValidationError("phase must be a ResearchPhase")
        data_classes = {
            ResearchRunClass.DATA_GATE,
            ResearchRunClass.COLLECTION,
            ResearchRunClass.TRANSFORM,
        }
        if self.run_class in data_classes:
            if (
                self.phase is not ResearchPhase.DATA
                or self.statistical_trial_id is not None
                or self.consumes_trial_budget
                or self.outcome_data_accessed
                or self.challenge_accessed
                or self.selection_trial_units
            ):
                raise ContractValidationError("data/provenance attempts cannot consume trials")
        elif self.run_class is ResearchRunClass.MODEL_TRIAL:
            if self.phase not in {ResearchPhase.DISCOVERY, ResearchPhase.VALIDATION}:
                raise ContractValidationError("model trial requires discovery or validation")
            if (
                not self.statistical_trial_id
                or not self.variant_id
                or not self.variant_definition_hash
                or not self.primary_metric
                or not self.consumes_trial_budget
                or self.challenge_accessed
            ):
                raise ContractValidationError("model trial requires one frozen statistical variant")
            require_sha256(self.variant_definition_hash, "variant_definition_hash")
            for name in (
                "code_sha256",
                "data_snapshot_sha256",
                "evaluator_spec_sha256",
            ):
                if getattr(self, name) is None:
                    raise ContractValidationError("model trial requires {}".format(name))
                require_sha256(getattr(self, name), name)
            if (
                not isinstance(self.effective_sample_floor, int)
                or self.effective_sample_floor < 1
            ):
                raise ContractValidationError(
                    "model trial requires a positive effective_sample_floor"
                )
            if self.legacy_accounting_quality == "COMPLETE" and (
                not self.preregistered or self.outcome_data_accessed
            ):
                raise ContractValidationError(
                    "new model trials must be registered before outcome access"
                )
        elif self.run_class is ResearchRunClass.ROBUSTNESS:
            if (
                self.phase not in {ResearchPhase.DISCOVERY, ResearchPhase.VALIDATION}
                or not self.parent_trial_id
                or self.statistical_trial_id is not None
                or self.consumes_trial_budget
                or self.challenge_accessed
                or not self.prespecified_non_selective
                or self.selection_trial_units
            ):
                raise ContractValidationError("robustness must be a non-budget child of a trial")
        elif self.run_class is ResearchRunClass.CHALLENGE_READ:
            if (
                self.phase is not ResearchPhase.CHALLENGE
                or not self.statistical_trial_id
                or not self.variant_id
                or not self.variant_definition_hash
                or not self.primary_metric
                or self.consumes_trial_budget
                or self.outcome_data_accessed
                or self.challenge_accessed
            ):
                raise ContractValidationError(
                    "challenge trial must be registered before the epoch is opened"
                )
            require_sha256(self.variant_definition_hash, "variant_definition_hash")
            for name in (
                "code_sha256",
                "data_snapshot_sha256",
                "evaluator_spec_sha256",
            ):
                if getattr(self, name) is None:
                    raise ContractValidationError("challenge trial requires {}".format(name))
                require_sha256(getattr(self, name), name)
            if (
                not isinstance(self.effective_sample_floor, int)
                or self.effective_sample_floor < 1
            ):
                raise ContractValidationError(
                    "challenge trial requires a positive effective_sample_floor"
                )
            if not self.preregistered:
                raise ContractValidationError("challenge trial must be preregistered")

    @property
    def statistical_trial_delta(self) -> int:
        return 1 if self.run_class is ResearchRunClass.MODEL_TRIAL else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "family_id": self.family_id,
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "run_class": self.run_class.value,
            "phase": self.phase.value,
            "occurred_at": format_datetime(self.occurred_at),
            "source_artifact": self.source_artifact,
            "source_sha256": self.source_sha256,
            "statistical_trial_id": self.statistical_trial_id,
            "parent_trial_id": self.parent_trial_id,
            "primary_metric": self.primary_metric,
            "variant_id": self.variant_id,
            "variant_definition_hash": self.variant_definition_hash,
            "consumes_trial_budget": self.consumes_trial_budget,
            "statistical_trial_delta": self.statistical_trial_delta,
            "preregistered": self.preregistered,
            "outcome_data_accessed": self.outcome_data_accessed,
            "challenge_accessed": self.challenge_accessed,
            "legacy_accounting_quality": self.legacy_accounting_quality,
            "source_chain_head_hash": self.source_chain_head_hash,
            "attempt_outcome": self.attempt_outcome,
            "code_sha256": self.code_sha256,
            "data_snapshot_sha256": self.data_snapshot_sha256,
            "evaluator_spec_sha256": self.evaluator_spec_sha256,
            "effective_sample_floor": self.effective_sample_floor,
            "selection_trial_units": self.selection_trial_units,
            "prespecified_non_selective": self.prespecified_non_selective,
        }


@dataclass(frozen=True)
class TrialResult:
    statistical_trial_id: str
    outcome: TrialOutcome
    recorded_at: datetime
    primary_metric: str
    primary_metric_value: Optional[float]
    p_value: Optional[float]
    inference_eligible: bool
    ineligibility_reasons: Tuple[str, ...]
    stress_scenario_pass: bool
    capacity_and_concentration_pass: bool
    effective_sample_size: int
    minimum_effective_sample: int
    source_artifact: str
    source_sha256: str
    schema_version: str = "caerus_alpha_lab_trial_result_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.statistical_trial_id, _TRIAL_ID, "statistical_trial_id")
        if not isinstance(self.outcome, TrialOutcome):
            raise ContractValidationError("outcome must be a TrialOutcome")
        _require_aware(self.recorded_at, "recorded_at")
        for name in ("primary_metric", "source_artifact", "schema_version"):
            require_non_empty(getattr(self, name), name)
        require_sha256(self.source_sha256, "source_sha256")
        _require_unique(self.ineligibility_reasons, "ineligibility_reasons")
        if self.p_value is not None:
            _require_probability(self.p_value, "p_value")
        if self.primary_metric_value is not None and not isinstance(
            self.primary_metric_value, (float, int)
        ):
            raise ContractValidationError("primary_metric_value must be numeric")
        if not isinstance(self.inference_eligible, bool):
            raise ContractValidationError("inference_eligible must be boolean")
        if self.inference_eligible and self.p_value is None:
            raise ContractValidationError("inference-eligible result requires a p-value")
        if self.inference_eligible and self.ineligibility_reasons:
            raise ContractValidationError("eligible result cannot have ineligibility reasons")
        if not self.inference_eligible and not self.ineligibility_reasons:
            raise ContractValidationError("ineligible result requires a reason")
        if self.outcome is TrialOutcome.POSITIVE and not self.inference_eligible:
            raise ContractValidationError("positive evidence must be inference-eligible")
        if not isinstance(self.stress_scenario_pass, bool) or not isinstance(
            self.capacity_and_concentration_pass, bool
        ):
            raise ContractValidationError("result diagnostic gates must be boolean")
        if (
            not isinstance(self.effective_sample_size, int)
            or self.effective_sample_size < 0
            or not isinstance(self.minimum_effective_sample, int)
            or self.minimum_effective_sample < 1
        ):
            raise ContractValidationError("result effective sample fields are invalid")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "statistical_trial_id": self.statistical_trial_id,
            "outcome": self.outcome.value,
            "recorded_at": format_datetime(self.recorded_at),
            "primary_metric": self.primary_metric,
            "primary_metric_value": self.primary_metric_value,
            "p_value": self.p_value,
            "inference_eligible": self.inference_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
            "stress_scenario_pass": self.stress_scenario_pass,
            "capacity_and_concentration_pass": self.capacity_and_concentration_pass,
            "effective_sample_size": self.effective_sample_size,
            "minimum_effective_sample": self.minimum_effective_sample,
            "source_artifact": self.source_artifact,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class FamilyInference:
    family_id: str
    wave_id: str
    track: InferenceTrack
    method: MultipleTestingMethod
    included_trial_ids: Tuple[str, ...]
    family_omnibus_p_value: float
    adjusted_p_values: Mapping[str, float]
    primary_variant_pass: bool
    economic_hurdle_pass: bool
    stress_scenario_pass: bool
    capacity_and_concentration_pass: bool
    effective_sample_pass: bool
    recorded_at: datetime
    evaluated_ledger_head_hash: str
    source_artifact: str
    source_sha256: str
    schema_version: str = "caerus_alpha_lab_family_inference_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.family_id, _FAMILY_ID, "family_id")
        _require_pattern(self.wave_id, _WAVE_ID, "wave_id")
        if not isinstance(self.track, InferenceTrack):
            raise ContractValidationError("track must be an InferenceTrack")
        if self.method not in {
            MultipleTestingMethod.ROMANO_WOLF,
            MultipleTestingMethod.HOLM_BONFERRONI,
        }:
            raise ContractValidationError("family inference must control FWER")
        if not self.included_trial_ids:
            raise ContractValidationError("included_trial_ids cannot be empty")
        _require_unique(self.included_trial_ids, "included_trial_ids")
        for trial_id in self.included_trial_ids:
            _require_pattern(trial_id, _TRIAL_ID, "trial_id")
        _require_probability(self.family_omnibus_p_value, "family_omnibus_p_value")
        if set(self.adjusted_p_values) != set(self.included_trial_ids):
            raise ContractValidationError("adjusted p-values must cover all included trials")
        for value in self.adjusted_p_values.values():
            _require_probability(value, "adjusted_p_value")
        _require_aware(self.recorded_at, "recorded_at")
        require_sha256(self.evaluated_ledger_head_hash, "evaluated_ledger_head_hash")
        require_non_empty(self.source_artifact, "source_artifact")
        require_sha256(self.source_sha256, "source_sha256")
        for name in (
            "primary_variant_pass",
            "economic_hurdle_pass",
            "stress_scenario_pass",
            "capacity_and_concentration_pass",
            "effective_sample_pass",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ContractValidationError("{} must be boolean".format(name))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "family_id": self.family_id,
            "wave_id": self.wave_id,
            "track": self.track.value,
            "method": self.method.value,
            "included_trial_ids": list(self.included_trial_ids),
            "family_omnibus_p_value": float(self.family_omnibus_p_value),
            "adjusted_p_values": {
                key: float(value) for key, value in sorted(self.adjusted_p_values.items())
            },
            "primary_variant_pass": self.primary_variant_pass,
            "economic_hurdle_pass": self.economic_hurdle_pass,
            "stress_scenario_pass": self.stress_scenario_pass,
            "capacity_and_concentration_pass": self.capacity_and_concentration_pass,
            "effective_sample_pass": self.effective_sample_pass,
            "recorded_at": format_datetime(self.recorded_at),
            "evaluated_ledger_head_hash": self.evaluated_ledger_head_hash,
            "source_artifact": self.source_artifact,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class ChallengeEpoch:
    challenge_epoch_id: str
    family_ids: Tuple[str, ...]
    trial_ids: Tuple[str, ...]
    expected_input_sha256_by_trial: Mapping[str, str]
    challenge_period: str
    panel_manifest_sha256: str
    alpha: float
    authorized_by: str
    authorization_artifact: str
    authorization_sha256: str
    authorized_at: datetime
    schema_version: str = "caerus_alpha_lab_challenge_epoch_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.challenge_epoch_id, _CHALLENGE_ID, "challenge_epoch_id")
        if not self.family_ids or len(self.family_ids) != len(self.trial_ids):
            raise ContractValidationError("challenge requires one trial per family")
        _require_unique(self.family_ids, "family_ids")
        _require_unique(self.trial_ids, "trial_ids")
        for family_id in self.family_ids:
            _require_pattern(family_id, _FAMILY_ID, "family_id")
        for trial_id in self.trial_ids:
            _require_pattern(trial_id, _TRIAL_ID, "trial_id")
        if set(self.expected_input_sha256_by_trial) != set(self.trial_ids):
            raise ContractValidationError("input hashes must cover the entrant set")
        for value in self.expected_input_sha256_by_trial.values():
            require_sha256(value, "expected_input_sha256")
        require_non_empty(self.challenge_period, "challenge_period")
        _date_period(self.challenge_period)
        require_sha256(self.panel_manifest_sha256, "panel_manifest_sha256")
        _require_probability(self.alpha, "alpha")
        if float(self.alpha) in {0.0, 1.0}:
            raise ContractValidationError("alpha must be strictly between zero and one")
        if float(self.alpha) > 0.05:
            raise ContractValidationError("challenge alpha cannot exceed 0.05")
        require_non_empty(self.authorized_by, "authorized_by")
        require_non_empty(self.authorization_artifact, "authorization_artifact")
        require_sha256(self.authorization_sha256, "authorization_sha256")
        _require_aware(self.authorized_at, "authorized_at")

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "challenge_epoch_id": self.challenge_epoch_id,
            "family_ids": list(self.family_ids),
            "trial_ids": list(self.trial_ids),
            "expected_input_sha256_by_trial": {
                key: value
                for key, value in sorted(self.expected_input_sha256_by_trial.items())
            },
            "challenge_period": self.challenge_period,
            "panel_manifest_sha256": self.panel_manifest_sha256,
            "confirmatory_method": MultipleTestingMethod.HOLM_BONFERRONI.value,
            "alpha": float(self.alpha),
            "authorized_by": self.authorized_by,
            "authorization_artifact": self.authorization_artifact,
            "authorization_sha256": self.authorization_sha256,
            "authorized_at": format_datetime(self.authorized_at),
            "maximum_consumptions": 1,
        }
        result["holdout_fingerprint"] = canonical_hash(
            {
                "panel_manifest_sha256": self.panel_manifest_sha256,
                "challenge_period": self.challenge_period,
                "input_sha256s": sorted(self.expected_input_sha256_by_trial.values()),
            }
        )
        return result


@dataclass(frozen=True)
class HoldoutAccess:
    access_id: str
    challenge_epoch_id: str
    trial_ids: Tuple[str, ...]
    input_sha256_by_trial: Mapping[str, str]
    accessed_at: datetime
    consumer: str
    purpose: str
    schema_version: str = "caerus_alpha_lab_challenge_access_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.access_id, _ACCESS_ID, "access_id")
        _require_pattern(self.challenge_epoch_id, _CHALLENGE_ID, "challenge_epoch_id")
        if not self.trial_ids:
            raise ContractValidationError("trial_ids cannot be empty")
        _require_unique(self.trial_ids, "trial_ids")
        if set(self.input_sha256_by_trial) != set(self.trial_ids):
            raise ContractValidationError("input hashes must cover every challenge trial")
        for trial_id in self.trial_ids:
            _require_pattern(trial_id, _TRIAL_ID, "trial_id")
            require_sha256(self.input_sha256_by_trial[trial_id], "expected_input_sha256")
        _require_aware(self.accessed_at, "accessed_at")
        require_non_empty(self.consumer, "consumer")
        require_non_empty(self.purpose, "purpose")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "access_id": self.access_id,
            "challenge_epoch_id": self.challenge_epoch_id,
            "trial_ids": list(self.trial_ids),
            "input_sha256_by_trial": {
                key: value for key, value in sorted(self.input_sha256_by_trial.items())
            },
            "accessed_at": format_datetime(self.accessed_at),
            "consumer": self.consumer,
            "purpose": self.purpose,
            "single_use": True,
        }


@dataclass(frozen=True)
class IndependentResearchReview:
    review_id: str
    family_id: str
    reviewer: str
    independent_of_research_authors: bool
    reviewed_ledger_head_hash: str
    point_in_time_integrity: bool
    deterministic_replay: bool
    benchmark_and_factor_model_pass: bool
    artifact_integrity_pass: bool
    reviewed_at: datetime
    source_artifact: str
    source_sha256: str
    schema_version: str = "caerus_alpha_lab_independent_research_review_v1"

    def __post_init__(self) -> None:
        _require_pattern(self.review_id, _REVIEW_ID, "review_id")
        _require_pattern(self.family_id, _FAMILY_ID, "family_id")
        require_non_empty(self.reviewer, "reviewer")
        if self.independent_of_research_authors is not True:
            raise ContractValidationError("research review must be independent")
        require_sha256(self.reviewed_ledger_head_hash, "reviewed_ledger_head_hash")
        for name in (
            "point_in_time_integrity",
            "deterministic_replay",
            "benchmark_and_factor_model_pass",
            "artifact_integrity_pass",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ContractValidationError("{} must be boolean".format(name))
        _require_aware(self.reviewed_at, "reviewed_at")
        require_non_empty(self.source_artifact, "source_artifact")
        require_sha256(self.source_sha256, "source_sha256")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "review_id": self.review_id,
            "family_id": self.family_id,
            "reviewer": self.reviewer,
            "independent_of_research_authors": self.independent_of_research_authors,
            "reviewed_ledger_head_hash": self.reviewed_ledger_head_hash,
            "point_in_time_integrity": self.point_in_time_integrity,
            "deterministic_replay": self.deterministic_replay,
            "benchmark_and_factor_model_pass": self.benchmark_and_factor_model_pass,
            "artifact_integrity_pass": self.artifact_integrity_pass,
            "reviewed_at": format_datetime(self.reviewed_at),
            "source_artifact": self.source_artifact,
            "source_sha256": self.source_sha256,
        }


def _step_up(
    labels_and_p_values: Sequence[Tuple[str, float]], *, multiplier: float, threshold: float
) -> List[Dict[str, Any]]:
    _require_probability(threshold, "threshold")
    for label, value in labels_and_p_values:
        require_non_empty(label, "p_value_label")
        _require_probability(value, "p_value")
    count = len(labels_and_p_values)
    if count == 0:
        return []
    ranked = sorted(enumerate(labels_and_p_values), key=lambda item: (item[1][1], item[1][0]))
    adjusted_ranked = [1.0] * count
    running = 1.0
    for index in range(count - 1, -1, -1):
        _, (_, p_value) = ranked[index]
        running = min(running, float(p_value) * count * multiplier / (index + 1), 1.0)
        adjusted_ranked[index] = running
    by_original: Dict[int, Dict[str, Any]] = {}
    for index, (original, (label, p_value)) in enumerate(ranked):
        adjusted = adjusted_ranked[index]
        by_original[original] = {
            "label": label,
            "p_value": float(p_value),
            "adjusted_p_value": adjusted,
            "reject": adjusted <= float(threshold),
        }
    return [by_original[index] for index in range(count)]


def benjamini_hochberg(
    labels_and_p_values: Sequence[Tuple[str, float]], *, q: float
) -> List[Dict[str, Any]]:
    return _step_up(labels_and_p_values, multiplier=1.0, threshold=q)


def benjamini_yekutieli(
    labels_and_p_values: Sequence[Tuple[str, float]], *, q: float
) -> List[Dict[str, Any]]:
    count = len(labels_and_p_values)
    harmonic = sum(1.0 / index for index in range(1, count + 1)) if count else 1.0
    return _step_up(labels_and_p_values, multiplier=harmonic, threshold=q)


def holm_bonferroni(
    labels_and_p_values: Sequence[Tuple[str, float]], *, alpha: float
) -> List[Dict[str, Any]]:
    _require_probability(alpha, "alpha")
    for label, value in labels_and_p_values:
        require_non_empty(label, "p_value_label")
        _require_probability(value, "p_value")
    count = len(labels_and_p_values)
    ranked = sorted(enumerate(labels_and_p_values), key=lambda item: (item[1][1], item[1][0]))
    adjusted_ranked: List[float] = []
    running = 0.0
    for index, (_, (_, p_value)) in enumerate(ranked):
        running = max(running, min(1.0, float(p_value) * (count - index)))
        adjusted_ranked.append(running)
    by_original: Dict[int, Dict[str, Any]] = {}
    for index, (original, (label, p_value)) in enumerate(ranked):
        adjusted = adjusted_ranked[index]
        by_original[original] = {
            "label": label,
            "p_value": float(p_value),
            "adjusted_p_value": adjusted,
            "reject": adjusted <= float(alpha),
        }
    return [by_original[index] for index in range(count)]


def apply_wave_correction(
    labels_and_p_values: Sequence[Tuple[str, float]],
    *,
    method: MultipleTestingMethod,
    alpha_or_q: float,
) -> List[Dict[str, Any]]:
    if method is MultipleTestingMethod.HOLM_BONFERRONI:
        return holm_bonferroni(labels_and_p_values, alpha=alpha_or_q)
    if method is MultipleTestingMethod.BENJAMINI_YEKUTIELI:
        return benjamini_yekutieli(labels_and_p_values, q=alpha_or_q)
    if method is MultipleTestingMethod.BENJAMINI_HOCHBERG:
        return benjamini_hochberg(labels_and_p_values, q=alpha_or_q)
    raise ContractValidationError("unsupported wave correction method")


def _family_inference_is_internally_valid(
    family: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
    inference: Mapping[str, Any],
) -> bool:
    if (
        inference.get("verified_internal") is not True
        or family.get("within_family_method")
        != MultipleTestingMethod.HOLM_BONFERRONI.value
        or inference.get("method") != MultipleTestingMethod.HOLM_BONFERRONI.value
    ):
        return False
    trial_ids = [
        item["statistical_trial_id"]
        for item in runs
        if item["family_id"] == family["family_id"]
        and item["run_class"] == ResearchRunClass.MODEL_TRIAL.value
    ]
    if not trial_ids or set(inference.get("included_trial_ids", [])) != set(trial_ids):
        return False
    if any(
        trial_id not in results
        or results[trial_id].get("p_value") is None
        or results[trial_id].get("inference_eligible") is not True
        for trial_id in trial_ids
    ):
        return False
    tests = holm_bonferroni(
        [(trial_id, float(results[trial_id]["p_value"])) for trial_id in trial_ids],
        alpha=float(family["family_alpha"]),
    )
    adjusted = {item["label"]: item["adjusted_p_value"] for item in tests}
    omnibus = min(
        1.0,
        len(trial_ids) * min(float(results[item]["p_value"]) for item in trial_ids),
    )
    run_by_trial = {
        item["statistical_trial_id"]: item
        for item in runs
        if item.get("statistical_trial_id")
    }
    primary_ids = [
        trial_id
        for trial_id in trial_ids
        if run_by_trial[trial_id].get("variant_id") == family["primary_variant_id"]
    ]
    if len(primary_ids) != 1:
        return False
    primary_id = primary_ids[0]
    primary = results[primary_id]
    metric_value = primary.get("primary_metric_value")
    economic_pass = metric_value is not None and (
        float(metric_value)
        >= float(family["null_value"]) + float(family["economic_hurdle"])
        if family["expected_direction"] == ExpectedDirection.GREATER_THAN.value
        else float(metric_value)
        <= float(family["null_value"]) - float(family["economic_hurdle"])
    )
    effective_pass = int(primary["effective_sample_size"]) >= int(
        primary["minimum_effective_sample"]
    )
    primary_pass = bool(
        primary["outcome"] == TrialOutcome.POSITIVE.value
        and adjusted[primary_id] <= float(family["family_alpha"])
        and economic_pass
        and primary["stress_scenario_pass"]
        and primary["capacity_and_concentration_pass"]
        and effective_pass
    )
    expected = {
        "family_omnibus_p_value": omnibus,
        "adjusted_p_values": adjusted,
        "primary_variant_pass": primary_pass,
        "economic_hurdle_pass": bool(economic_pass),
        "stress_scenario_pass": bool(primary["stress_scenario_pass"]),
        "capacity_and_concentration_pass": bool(
            primary["capacity_and_concentration_pass"]
        ),
        "effective_sample_pass": effective_pass,
    }
    supplied = {
        "family_omnibus_p_value": float(inference["family_omnibus_p_value"]),
        "adjusted_p_values": {
            key: float(value)
            for key, value in inference["adjusted_p_values"].items()
        },
        "primary_variant_pass": inference["primary_variant_pass"],
        "economic_hurdle_pass": inference["economic_hurdle_pass"],
        "stress_scenario_pass": inference["stress_scenario_pass"],
        "capacity_and_concentration_pass": inference[
            "capacity_and_concentration_pass"
        ],
        "effective_sample_pass": inference["effective_sample_pass"],
    }
    return canonical_hash(expected) == canonical_hash(supplied)


class GlobalResearchLedger:
    WAVE_EVENT = "research_wave_registered"
    FAMILY_EVENT = "hypothesis_family_registered"
    EXPERIMENT_EVENT = "research_experiment_registered"
    RUN_EVENT = "attempt_linked"
    RESULT_EVENT = "statistical_trial_closed"
    FAMILY_INFERENCE_EVENT = "family_inference_recorded"
    CHALLENGE_EPOCH_EVENT = "challenge_epoch_registered"
    HOLDOUT_EVENT = "challenge_access_started"
    REVIEW_EVENT = "independent_research_review_recorded"
    _KNOWN_EVENTS = frozenset(
        {
            WAVE_EVENT,
            FAMILY_EVENT,
            EXPERIMENT_EVENT,
            RUN_EVENT,
            RESULT_EVENT,
            FAMILY_INFERENCE_EVENT,
            CHALLENGE_EPOCH_EVENT,
            HOLDOUT_EVENT,
            REVIEW_EVENT,
        }
    )

    def __init__(self, path: Path, *, research_root: Path) -> None:
        self.store = AppendOnlyJSONLEventStore(path, research_root=research_root)

    @staticmethod
    def _payloads(records: Iterable[EventRecord], event_type: str) -> List[Mapping[str, Any]]:
        return [record.payload for record in records if record.event_type == event_type]

    def _verify_review_artifact(self, review: Mapping[str, Any]) -> None:
        source = Path(str(review["source_artifact"])).expanduser()
        candidate = source if source.is_absolute() else self.store.research_root / source
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.store.research_root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise EventStoreIntegrityError(
                "independent review artifact must exist inside research_root"
            ) from exc
        if not resolved.is_file():
            raise EventStoreIntegrityError(
                "independent review artifact must be a regular file"
            )
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != review["source_sha256"]:
            raise EventStoreIntegrityError(
                "independent review artifact hash does not match source_sha256"
            )

    def register_wave(self, wave: ResearchWave, *, recorded_at: datetime) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            waves = self._payloads(records, self.WAVE_EVENT)
            if any(item["wave_id"] == wave.wave_id for item in waves):
                raise EventStoreIntegrityError("duplicate wave_id")
            existing = {family for item in waves for family in item["family_ids"]}
            if existing.intersection(wave.family_ids):
                raise EventStoreIntegrityError("family generation already belongs to a wave")

        return self.store.append(
            event_id="wave:{}".format(wave.wave_id),
            event_type=self.WAVE_EVENT,
            occurred_at=wave.registered_at,
            recorded_at=recorded_at,
            payload=wave.to_dict(),
            validate_existing=validate,
        )

    def register_family(self, family: HypothesisFamily, *, recorded_at: datetime) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            families = self._payloads(records, self.FAMILY_EVENT)
            if any(item["family_id"] == family.family_id for item in families):
                raise EventStoreIntegrityError("duplicate family_id")
            wave = next(
                (
                    item
                    for item in self._payloads(records, self.WAVE_EVENT)
                    if item["wave_id"] == family.wave_id
                ),
                None,
            )
            if wave is None or family.family_id not in wave["family_ids"]:
                raise EventStoreIntegrityError("family is not frozen into the registered wave")
            known = {item["family_id"] for item in families}
            if set(family.parent_family_ids) - known:
                raise EventStoreIntegrityError("parent family is not registered")
            frozen_epoch = next(
                (
                    item
                    for item in self._payloads(records, self.CHALLENGE_EPOCH_EVENT)
                    if item["challenge_epoch_id"] == family.challenge_epoch_id
                ),
                None,
            )
            if frozen_epoch is not None and family.family_id not in set(
                frozen_epoch["family_ids"]
            ):
                raise EventStoreIntegrityError(
                    "cannot add a family to an already frozen challenge epoch"
                )

        return self.store.append(
            event_id="family:{}".format(family.family_id),
            event_type=self.FAMILY_EVENT,
            occurred_at=family.registered_at,
            recorded_at=recorded_at,
            payload=family.to_dict(),
            validate_existing=validate,
        )

    def register_experiment(
        self, experiment: ResearchExperiment, *, recorded_at: datetime
    ) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            families = {
                item["family_id"]: item
                for item in self._payloads(records, self.FAMILY_EVENT)
            }
            family = families.get(experiment.family_id)
            if family is None:
                raise EventStoreIntegrityError("experiment family is not registered")
            if experiment.frozen_primary_metric != family["primary_metric"]:
                raise EventStoreIntegrityError(
                    "experiment changed the frozen family primary metric"
                )
            experiments = self._payloads(records, self.EXPERIMENT_EVENT)
            if any(
                item["experiment_id"] == experiment.experiment_id
                for item in experiments
            ):
                raise EventStoreIntegrityError("duplicate experiment_id")
            known = {item["experiment_id"] for item in experiments}
            if set(experiment.parent_experiment_ids) - known:
                raise EventStoreIntegrityError("parent experiment is not registered")
            if experiment.generated_after_results:
                parent_ids = set(experiment.parent_experiment_ids)
                parent_trial_ids = {
                    item.get("statistical_trial_id")
                    for item in self._payloads(records, self.RUN_EVENT)
                    if item["experiment_id"] in parent_ids
                    and item.get("statistical_trial_id")
                }
                result_ids = {
                    item["statistical_trial_id"]
                    for item in self._payloads(records, self.RESULT_EVENT)
                }
                if not parent_trial_ids.intersection(result_ids):
                    raise EventStoreIntegrityError(
                        "post-result experiment lacks a closed parent generation"
                    )

        return self.store.append(
            event_id="experiment:{}".format(experiment.experiment_id),
            event_type=self.EXPERIMENT_EVENT,
            occurred_at=experiment.registered_at,
            recorded_at=recorded_at,
            payload=experiment.to_dict(),
            validate_existing=validate,
        )

    def register_run(self, run: ResearchRun, *, recorded_at: datetime) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            families = {
                item["family_id"]: item for item in self._payloads(records, self.FAMILY_EVENT)
            }
            family = families.get(run.family_id)
            if family is None:
                raise EventStoreIntegrityError("attempt family is not registered")
            experiment = next(
                (
                    item
                    for item in self._payloads(records, self.EXPERIMENT_EVENT)
                    if item["experiment_id"] == run.experiment_id
                ),
                None,
            )
            if (
                experiment is None
                or experiment["family_id"] != run.family_id
                or experiment["hypothesis_id"] != run.hypothesis_id
            ):
                raise EventStoreIntegrityError("attempt experiment lineage mismatch")
            runs = self._payloads(records, self.RUN_EVENT)
            if any(item["attempt_id"] == run.attempt_id for item in runs):
                raise EventStoreIntegrityError("duplicate attempt_id")
            if run.statistical_trial_id and any(
                item.get("statistical_trial_id") == run.statistical_trial_id for item in runs
            ):
                raise EventStoreIntegrityError("statistical trial is already registered")
            if run.parent_trial_id and not any(
                item.get("statistical_trial_id") == run.parent_trial_id for item in runs
            ):
                raise EventStoreIntegrityError("parent trial is not registered")
            if run.primary_metric and run.primary_metric != family["primary_metric"]:
                raise EventStoreIntegrityError("attempt changed the frozen primary metric")
            consumed = sum(
                int(item.get("statistical_trial_delta", 0))
                for item in runs
                if item["family_id"] == run.family_id
            )
            if consumed + run.statistical_trial_delta > int(family["maximum_trial_units"]):
                raise EventStoreIntegrityError("family statistical trial budget exceeded")
            selection_used = sum(
                int(item.get("selection_trial_units", 0))
                for item in runs
                if item["family_id"] == run.family_id
            )
            if selection_used + run.selection_trial_units > int(
                family["selection_trial_budget"]
            ):
                raise EventStoreIntegrityError("family selection trial budget exceeded")

        return self.store.append(
            event_id="attempt:{}".format(run.attempt_id),
            event_type=self.RUN_EVENT,
            occurred_at=run.occurred_at,
            recorded_at=recorded_at,
            payload=run.to_dict(),
            validate_existing=validate,
        )

    def record_result(self, result: TrialResult, *, recorded_at: datetime) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            run = next(
                (
                    item
                    for item in self._payloads(records, self.RUN_EVENT)
                    if item.get("statistical_trial_id") == result.statistical_trial_id
                ),
                None,
            )
            if run is None:
                raise EventStoreIntegrityError("result trial is not registered")
            if result.recorded_at < parse_datetime(run["occurred_at"]):
                raise EventStoreIntegrityError(
                    "result chronology precedes the registered trial"
                )
            if result.primary_metric != run.get("primary_metric"):
                raise EventStoreIntegrityError("result changed the frozen primary metric")
            if result.minimum_effective_sample != run.get("effective_sample_floor"):
                raise EventStoreIntegrityError(
                    "result changed the frozen effective sample floor"
                )
            family = next(
                item
                for item in self._payloads(records, self.FAMILY_EVENT)
                if item["family_id"] == run["family_id"]
            )
            if result.outcome is TrialOutcome.POSITIVE:
                if result.primary_metric_value is None:
                    raise EventStoreIntegrityError(
                        "positive result requires a primary metric value"
                    )
                threshold = float(family["null_value"])
                hurdle = float(family["economic_hurdle"])
                metric_pass = (
                    float(result.primary_metric_value) >= threshold + hurdle
                    if family["expected_direction"] == ExpectedDirection.GREATER_THAN.value
                    else float(result.primary_metric_value) <= threshold - hurdle
                )
                if not metric_pass:
                    raise EventStoreIntegrityError(
                        "positive result does not clear the frozen economic hurdle"
                    )
            if run["run_class"] == ResearchRunClass.CHALLENGE_READ.value:
                access = next(
                    (
                        item
                        for item in self._payloads(records, self.HOLDOUT_EVENT)
                        if item["challenge_epoch_id"] == family["challenge_epoch_id"]
                    ),
                    None,
                )
                if access is None:
                    raise EventStoreIntegrityError(
                        "challenge result cannot precede challenge access"
                    )
                if result.recorded_at < parse_datetime(access["accessed_at"]):
                    raise EventStoreIntegrityError(
                        "challenge result chronology precedes access"
                    )
            if any(
                item["statistical_trial_id"] == result.statistical_trial_id
                for item in self._payloads(records, self.RESULT_EVENT)
            ):
                raise EventStoreIntegrityError("trial result is already recorded")

        return self.store.append(
            event_id="result:{}".format(result.statistical_trial_id),
            event_type=self.RESULT_EVENT,
            occurred_at=result.recorded_at,
            recorded_at=recorded_at,
            payload=result.to_dict(),
            validate_existing=validate,
        )

    def record_family_inference(
        self, inference: FamilyInference, *, recorded_at: datetime
    ) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            if not records or records[-1].event_hash != inference.evaluated_ledger_head_hash:
                raise EventStoreIntegrityError("inference is not bound to the current ledger head")
            family = next(
                (
                    item
                    for item in self._payloads(records, self.FAMILY_EVENT)
                    if item["family_id"] == inference.family_id
                ),
                None,
            )
            if family is None or family["wave_id"] != inference.wave_id:
                raise EventStoreIntegrityError("inference family/wave is not registered")
            if family["within_family_method"] != inference.method.value:
                raise EventStoreIntegrityError("inference changed the frozen family method")
            if inference.method is not MultipleTestingMethod.HOLM_BONFERRONI:
                raise EventStoreIntegrityError(
                    "verified family inference engine is not implemented for {}".format(
                        inference.method.value
                    )
                )
            runs = self._payloads(records, self.RUN_EVENT)
            expected = {
                item["statistical_trial_id"]
                for item in runs
                if item["family_id"] == inference.family_id
                and item["run_class"] == ResearchRunClass.MODEL_TRIAL.value
            }
            if set(inference.included_trial_ids) != expected:
                raise EventStoreIntegrityError("family inference omitted registered trials")
            results = {
                item["statistical_trial_id"]: item
                for item in self._payloads(records, self.RESULT_EVENT)
            }
            if any(
                trial_id not in results
                or results[trial_id].get("p_value") is None
                or not results[trial_id]["inference_eligible"]
                for trial_id in expected
            ):
                raise EventStoreIntegrityError("family inference requires complete eligible results")
            if any(
                inference.recorded_at < parse_datetime(results[trial_id]["recorded_at"])
                for trial_id in expected
            ):
                raise EventStoreIntegrityError(
                    "family inference cannot precede its trial results"
                )
            ordered = list(inference.included_trial_ids)
            computed_tests = holm_bonferroni(
                [(trial_id, float(results[trial_id]["p_value"])) for trial_id in ordered],
                alpha=float(family["family_alpha"]),
            )
            computed_adjusted = {
                item["label"]: float(item["adjusted_p_value"])
                for item in computed_tests
            }
            computed_omnibus = min(
                1.0,
                len(ordered)
                * min(float(results[trial_id]["p_value"]) for trial_id in ordered),
            )
            runs_by_trial = {
                item["statistical_trial_id"]: item
                for item in runs
                if item.get("statistical_trial_id")
            }
            primary_ids = [
                trial_id
                for trial_id in ordered
                if runs_by_trial[trial_id].get("variant_id")
                == family["primary_variant_id"]
            ]
            if len(primary_ids) != 1:
                raise EventStoreIntegrityError(
                    "family inference requires exactly one frozen primary variant"
                )
            primary = results[primary_ids[0]]
            metric_value = primary.get("primary_metric_value")
            economic_pass = metric_value is not None and (
                float(metric_value)
                >= float(family["null_value"]) + float(family["economic_hurdle"])
                if family["expected_direction"] == ExpectedDirection.GREATER_THAN.value
                else float(metric_value)
                <= float(family["null_value"]) - float(family["economic_hurdle"])
            )
            effective_pass = int(primary["effective_sample_size"]) >= int(
                primary["minimum_effective_sample"]
            )
            primary_pass = bool(
                primary["outcome"] == TrialOutcome.POSITIVE.value
                and computed_adjusted[primary_ids[0]] <= float(family["family_alpha"])
                and economic_pass
                and primary["stress_scenario_pass"]
                and primary["capacity_and_concentration_pass"]
                and effective_pass
            )
            expected_fields = {
                "family_omnibus_p_value": computed_omnibus,
                "adjusted_p_values": computed_adjusted,
                "primary_variant_pass": primary_pass,
                "economic_hurdle_pass": bool(economic_pass),
                "stress_scenario_pass": bool(primary["stress_scenario_pass"]),
                "capacity_and_concentration_pass": bool(
                    primary["capacity_and_concentration_pass"]
                ),
                "effective_sample_pass": effective_pass,
            }
            supplied_fields = {
                "family_omnibus_p_value": float(inference.family_omnibus_p_value),
                "adjusted_p_values": {
                    key: float(value)
                    for key, value in inference.adjusted_p_values.items()
                },
                "primary_variant_pass": inference.primary_variant_pass,
                "economic_hurdle_pass": inference.economic_hurdle_pass,
                "stress_scenario_pass": inference.stress_scenario_pass,
                "capacity_and_concentration_pass": inference.capacity_and_concentration_pass,
                "effective_sample_pass": inference.effective_sample_pass,
            }
            if canonical_hash(expected_fields) != canonical_hash(supplied_fields):
                raise EventStoreIntegrityError(
                    "family inference differs from internally verified statistics"
                )
            if any(
                item["family_id"] == inference.family_id
                and item["track"] == inference.track.value
                for item in self._payloads(records, self.FAMILY_INFERENCE_EVENT)
            ):
                raise EventStoreIntegrityError("family inference is already recorded")

        payload = inference.to_dict()
        payload["verified_internal"] = True
        return self.store.append(
            event_id="family-inference:{}:{}".format(
                inference.family_id, inference.track.value.lower()
            ),
            event_type=self.FAMILY_INFERENCE_EVENT,
            occurred_at=inference.recorded_at,
            recorded_at=recorded_at,
            payload=payload,
            validate_existing=validate,
        )

    def register_challenge_epoch(
        self, epoch: ChallengeEpoch, *, recorded_at: datetime
    ) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            existing_epochs = self._payloads(records, self.CHALLENGE_EPOCH_EVENT)
            if any(
                item["challenge_epoch_id"] == epoch.challenge_epoch_id
                for item in existing_epochs
            ):
                raise EventStoreIntegrityError("challenge epoch is already registered")
            epoch_payload = epoch.to_dict()
            for existing in existing_epochs:
                conflict = _holdout_reuse_reason(existing, epoch_payload)
                if conflict is not None:
                    raise EventStoreIntegrityError(conflict)
            families = {
                item["family_id"]: item for item in self._payloads(records, self.FAMILY_EVENT)
            }
            runs = {
                item.get("statistical_trial_id"): item
                for item in self._payloads(records, self.RUN_EVENT)
                if item.get("statistical_trial_id")
            }
            for family_id, trial_id in zip(epoch.family_ids, epoch.trial_ids):
                run = runs.get(trial_id)
                if (
                    family_id not in families
                    or families[family_id]["challenge_epoch_id"] != epoch.challenge_epoch_id
                    or run is None
                    or run["family_id"] != family_id
                    or run["run_class"] != ResearchRunClass.CHALLENGE_READ.value
                    or run["variant_id"] != families[family_id]["primary_variant_id"]
                    or run["data_snapshot_sha256"]
                    != epoch.expected_input_sha256_by_trial[trial_id]
                ):
                    raise EventStoreIntegrityError("challenge entrant binding mismatch")
            inference_index = {
                (item["family_id"], item["track"]): item
                for item in self._payloads(records, self.FAMILY_INFERENCE_EVENT)
            }
            wave_index = {
                item["wave_id"]: item
                for item in self._payloads(records, self.WAVE_EVENT)
            }
            for family_id in epoch.family_ids:
                family = families[family_id]
                wave = wave_index[family["wave_id"]]
                inference = inference_index.get((family_id, wave["track"]))
                if inference is None or inference.get("verified_internal") is not True:
                    raise EventStoreIntegrityError(
                        "challenge requires verified family inference"
                    )
                if epoch.authorized_at < parse_datetime(inference["recorded_at"]):
                    raise EventStoreIntegrityError(
                        "challenge epoch cannot precede verified family inference"
                    )
                if not all(
                    inference.get(name) is True
                    for name in (
                        "primary_variant_pass",
                        "economic_hurdle_pass",
                        "stress_scenario_pass",
                        "capacity_and_concentration_pass",
                        "effective_sample_pass",
                    )
                ):
                    raise EventStoreIntegrityError(
                        "challenge entrant did not pass verified family gates"
                    )
                values = []
                for wave_family_id in wave["family_ids"]:
                    item = inference_index.get((wave_family_id, wave["track"]))
                    if item is None or item.get("verified_internal") is not True:
                        raise EventStoreIntegrityError(
                            "challenge requires complete verified wave inference"
                        )
                    values.append(
                        (wave_family_id, float(item["family_omnibus_p_value"]))
                    )
                decisions = {
                    item["label"]: item["reject"]
                    for item in apply_wave_correction(
                        values,
                        method=MultipleTestingMethod(wave["method"]),
                        alpha_or_q=float(wave["alpha_or_q"]),
                    )
                }
                if not decisions.get(family_id, False):
                    raise EventStoreIntegrityError(
                        "challenge entrant did not pass frozen wave correction"
                    )

        return self.store.append(
            event_id="challenge-epoch:{}".format(epoch.challenge_epoch_id),
            event_type=self.CHALLENGE_EPOCH_EVENT,
            occurred_at=epoch.authorized_at,
            recorded_at=recorded_at,
            payload=epoch.to_dict(),
            validate_existing=validate,
        )

    def record_holdout_access(
        self, access: HoldoutAccess, *, recorded_at: datetime
    ) -> EventRecord:
        """Consume the whole challenge epoch before any outcome-bearing input read."""

        def validate(records: List[EventRecord]) -> None:
            epoch = next(
                (
                    item
                    for item in self._payloads(records, self.CHALLENGE_EPOCH_EVENT)
                    if item["challenge_epoch_id"] == access.challenge_epoch_id
                ),
                None,
            )
            if epoch is None:
                raise EventStoreIntegrityError("challenge epoch is not registered")
            if access.accessed_at < parse_datetime(epoch["authorized_at"]):
                raise EventStoreIntegrityError(
                    "challenge access cannot precede epoch authorization"
                )
            if access.accessed_at > recorded_at:
                raise EventStoreIntegrityError(
                    "challenge access cannot follow event recording"
                )
            if (
                set(access.trial_ids) != set(epoch["trial_ids"])
                or dict(access.input_sha256_by_trial)
                != dict(epoch["expected_input_sha256_by_trial"])
            ):
                raise EventStoreIntegrityError("challenge access differs from the frozen entrant set")
            if any(
                item["challenge_epoch_id"] == access.challenge_epoch_id
                for item in self._payloads(records, self.HOLDOUT_EVENT)
            ):
                raise EventStoreIntegrityError("single-use challenge epoch was already accessed")

        return self.store.append(
            event_id="challenge-access:{}".format(access.access_id),
            event_type=self.HOLDOUT_EVENT,
            occurred_at=access.accessed_at,
            recorded_at=recorded_at,
            payload=access.to_dict(),
            validate_existing=validate,
        )

    def record_independent_review(
        self, review: IndependentResearchReview, *, recorded_at: datetime
    ) -> EventRecord:
        def validate(records: List[EventRecord]) -> None:
            if not records or records[-1].event_hash != review.reviewed_ledger_head_hash:
                raise EventStoreIntegrityError(
                    "independent review is not bound to the current ledger head"
                )
            family = next(
                (
                    item
                    for item in self._payloads(records, self.FAMILY_EVENT)
                    if item["family_id"] == review.family_id
                ),
                None,
            )
            if family is None:
                raise EventStoreIntegrityError("review family is not registered")
            if any(
                item["family_id"] == review.family_id
                for item in self._payloads(records, self.REVIEW_EVENT)
            ):
                raise EventStoreIntegrityError("family review is already recorded")
            challenge_trial_ids = {
                item["statistical_trial_id"]
                for item in self._payloads(records, self.RUN_EVENT)
                if item["family_id"] == review.family_id
                and item["run_class"] == ResearchRunClass.CHALLENGE_READ.value
            }
            closed_ids = {
                item["statistical_trial_id"]
                for item in self._payloads(records, self.RESULT_EVENT)
            }
            if not challenge_trial_ids or challenge_trial_ids - closed_ids:
                raise EventStoreIntegrityError(
                    "independent review requires closed challenge evidence"
                )
            if review.reviewed_at < max(
                parse_datetime(item["recorded_at"])
                for item in self._payloads(records, self.RESULT_EVENT)
                if item["statistical_trial_id"] in challenge_trial_ids
            ):
                raise EventStoreIntegrityError(
                    "independent review cannot precede challenge results"
                )
            self._verify_review_artifact(review.to_dict())

        return self.store.append(
            event_id="review:{}".format(review.review_id),
            event_type=self.REVIEW_EVENT,
            occurred_at=review.reviewed_at,
            recorded_at=recorded_at,
            payload=review.to_dict(),
            validate_existing=validate,
        )

    def require_registered_trials(
        self, trial_ids: Sequence[str], *, run_class: Optional[ResearchRunClass] = None
    ) -> Tuple[Mapping[str, Any], ...]:
        records = self.store.read_all()
        self._validate_semantics(records)
        runs = {
            item.get("statistical_trial_id"): item
            for item in self._payloads(records, self.RUN_EVENT)
            if item.get("statistical_trial_id")
        }
        missing = sorted(set(trial_ids) - set(runs))
        if missing:
            raise EventStoreIntegrityError(
                "unregistered statistical trials: {}".format(",".join(missing))
            )
        selected = tuple(runs[item] for item in trial_ids)
        if run_class and any(item["run_class"] != run_class.value for item in selected):
            raise EventStoreIntegrityError("registered trial has the wrong run class")
        return selected

    def _validate_semantics(self, records: Sequence[EventRecord]) -> None:
        unknown = sorted({item.event_type for item in records} - self._KNOWN_EVENTS)
        if unknown:
            raise EventStoreIntegrityError(
                "unknown global-ledger event types: {}".format(",".join(unknown))
            )
        event_identity_fields = {
            self.WAVE_EVENT: ("wave", "wave_id", "registered_at"),
            self.FAMILY_EVENT: ("family", "family_id", "registered_at"),
            self.EXPERIMENT_EVENT: (
                "experiment",
                "experiment_id",
                "registered_at",
            ),
            self.RUN_EVENT: ("attempt", "attempt_id", "occurred_at"),
            self.RESULT_EVENT: (
                "result",
                "statistical_trial_id",
                "recorded_at",
            ),
            self.CHALLENGE_EPOCH_EVENT: (
                "challenge-epoch",
                "challenge_epoch_id",
                "authorized_at",
            ),
            self.HOLDOUT_EVENT: ("challenge-access", "access_id", "accessed_at"),
            self.REVIEW_EVENT: ("review", "review_id", "reviewed_at"),
        }
        for record in records:
            if record.event_type == self.FAMILY_INFERENCE_EVENT:
                expected_event_id = "family-inference:{}:{}".format(
                    record.payload.get("family_id"),
                    str(record.payload.get("track", "")).lower(),
                )
                occurred_field = "recorded_at"
            else:
                prefix, identity_field, occurred_field = event_identity_fields[
                    record.event_type
                ]
                expected_event_id = "{}:{}".format(
                    prefix, record.payload.get(identity_field)
                )
            try:
                payload_occurred_at = parse_datetime(
                    str(record.payload[occurred_field])
                )
            except (KeyError, TypeError, ValueError, ContractValidationError) as exc:
                raise EventStoreIntegrityError(
                    "semantic replay found an invalid event chronology field"
                ) from exc
            if (
                record.event_id != expected_event_id
                or record.occurred_at != payload_occurred_at
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found an event envelope/payload identity mismatch"
                )
        event_positions = {
            record.event_id: position for position, record in enumerate(records)
        }
        waves = self._payloads(records, self.WAVE_EVENT)
        families = self._payloads(records, self.FAMILY_EVENT)
        experiments = self._payloads(records, self.EXPERIMENT_EVENT)
        runs = self._payloads(records, self.RUN_EVENT)
        results = self._payloads(records, self.RESULT_EVENT)
        reviews = self._payloads(records, self.REVIEW_EVENT)
        inferences = self._payloads(records, self.FAMILY_INFERENCE_EVENT)
        epochs = self._payloads(records, self.CHALLENGE_EPOCH_EVENT)
        accesses = self._payloads(records, self.HOLDOUT_EVENT)
        try:
            for item in waves:
                typed_wave = ResearchWave(
                    wave_id=item["wave_id"],
                    track=InferenceTrack(item["track"]),
                    family_ids=tuple(item["family_ids"]),
                    method=MultipleTestingMethod(item["method"]),
                    alpha_or_q=item["alpha_or_q"],
                    registered_at=parse_datetime(item["registered_at"]),
                    policy_artifact=item["policy_artifact"],
                    policy_sha256=item["policy_sha256"],
                    owner_ratified=item["owner_ratified"],
                    dependence_contract_sha256=item.get(
                        "dependence_contract_sha256"
                    ),
                    legacy_policy=item.get("legacy_policy", False),
                    schema_version=item["schema_version"],
                )
                if canonical_hash(typed_wave.to_dict()) != canonical_hash(item):
                    raise ContractValidationError(
                        "research wave payload differs from its typed contract"
                    )
            for item in families:
                typed_family = HypothesisFamily(
                    family_id=item["family_id"],
                    wave_id=item["wave_id"],
                    challenge_epoch_id=item["challenge_epoch_id"],
                    name=item["name"],
                    economic_mechanism=item["economic_mechanism"],
                    family_scope_hash=item["family_scope_hash"],
                    primary_metric=item["primary_metric"],
                    benchmark=item["benchmark"],
                    expected_direction=ExpectedDirection(item["expected_direction"]),
                    null_value=item["null_value"],
                    economic_hurdle=item["economic_hurdle"],
                    primary_variant_id=item["primary_variant_id"],
                    maximum_trial_units=item["maximum_trial_units"],
                    selection_trial_budget=item["selection_trial_budget"],
                    within_family_method=MultipleTestingMethod(
                        item["within_family_method"]
                    ),
                    family_alpha=item["family_alpha"],
                    registered_at=parse_datetime(item["registered_at"]),
                    source_artifact=item["source_artifact"],
                    source_sha256=item["source_sha256"],
                    owner_ratified=item["owner_ratified"],
                    parent_family_ids=tuple(item.get("parent_family_ids", [])),
                    schema_version=item["schema_version"],
                )
                if canonical_hash(typed_family.to_dict()) != canonical_hash(item):
                    raise ContractValidationError(
                        "hypothesis family payload differs from its typed contract"
                    )
            for item in experiments:
                typed_experiment = ResearchExperiment(
                    experiment_id=item["experiment_id"],
                    family_id=item["family_id"],
                    hypothesis_id=item["hypothesis_id"],
                    parent_experiment_ids=tuple(item["parent_experiment_ids"]),
                    generated_after_results=item["generated_after_results"],
                    generation_reason=item["generation_reason"],
                    frozen_primary_metric=item["frozen_primary_metric"],
                    registered_at=parse_datetime(item["registered_at"]),
                    source_artifact=item["source_artifact"],
                    source_sha256=item["source_sha256"],
                    owner_ratified=item["owner_ratified"],
                    schema_version=item["schema_version"],
                )
                if canonical_hash(typed_experiment.to_dict()) != canonical_hash(item):
                    raise ContractValidationError(
                        "research experiment payload differs from its typed contract"
                    )
            for item in runs:
                typed_run = ResearchRun(
                    attempt_id=item["attempt_id"],
                    family_id=item["family_id"],
                    hypothesis_id=item["hypothesis_id"],
                    experiment_id=item["experiment_id"],
                    run_id=item["run_id"],
                    run_class=ResearchRunClass(item["run_class"]),
                    phase=ResearchPhase(item["phase"]),
                    occurred_at=parse_datetime(item["occurred_at"]),
                    source_artifact=item["source_artifact"],
                    source_sha256=item["source_sha256"],
                    statistical_trial_id=item.get("statistical_trial_id"),
                    parent_trial_id=item.get("parent_trial_id"),
                    primary_metric=item.get("primary_metric"),
                    variant_id=item.get("variant_id"),
                    variant_definition_hash=item.get("variant_definition_hash"),
                    consumes_trial_budget=item["consumes_trial_budget"],
                    preregistered=item["preregistered"],
                    outcome_data_accessed=item["outcome_data_accessed"],
                    challenge_accessed=item["challenge_accessed"],
                    legacy_accounting_quality=item["legacy_accounting_quality"],
                    source_chain_head_hash=item.get("source_chain_head_hash"),
                    attempt_outcome=item.get("attempt_outcome"),
                    code_sha256=item.get("code_sha256"),
                    data_snapshot_sha256=item.get("data_snapshot_sha256"),
                    evaluator_spec_sha256=item.get("evaluator_spec_sha256"),
                    effective_sample_floor=item.get("effective_sample_floor"),
                    selection_trial_units=item.get("selection_trial_units", 0),
                    prespecified_non_selective=item.get(
                        "prespecified_non_selective", False
                    ),
                    schema_version=item["schema_version"],
                )
                if canonical_hash(typed_run.to_dict()) != canonical_hash(item):
                    raise ContractValidationError(
                        "research run payload differs from its typed contract"
                    )
            for item in results:
                typed_result = TrialResult(
                    statistical_trial_id=item["statistical_trial_id"],
                    outcome=TrialOutcome(item["outcome"]),
                    recorded_at=parse_datetime(item["recorded_at"]),
                    primary_metric=item["primary_metric"],
                    primary_metric_value=item.get("primary_metric_value"),
                    p_value=item.get("p_value"),
                    inference_eligible=item["inference_eligible"],
                    ineligibility_reasons=tuple(item["ineligibility_reasons"]),
                    stress_scenario_pass=item["stress_scenario_pass"],
                    capacity_and_concentration_pass=item[
                        "capacity_and_concentration_pass"
                    ],
                    effective_sample_size=item["effective_sample_size"],
                    minimum_effective_sample=item["minimum_effective_sample"],
                    source_artifact=item["source_artifact"],
                    source_sha256=item["source_sha256"],
                    schema_version=item["schema_version"],
                )
                if canonical_hash(typed_result.to_dict()) != canonical_hash(item):
                    raise ContractValidationError(
                        "trial result payload differs from its typed contract"
                    )
            for item in inferences:
                typed_inference = FamilyInference(
                    family_id=item["family_id"],
                    wave_id=item["wave_id"],
                    track=InferenceTrack(item["track"]),
                    method=MultipleTestingMethod(item["method"]),
                    included_trial_ids=tuple(item["included_trial_ids"]),
                    family_omnibus_p_value=item["family_omnibus_p_value"],
                    adjusted_p_values=dict(item["adjusted_p_values"]),
                    primary_variant_pass=item["primary_variant_pass"],
                    economic_hurdle_pass=item["economic_hurdle_pass"],
                    stress_scenario_pass=item["stress_scenario_pass"],
                    capacity_and_concentration_pass=item[
                        "capacity_and_concentration_pass"
                    ],
                    effective_sample_pass=item["effective_sample_pass"],
                    recorded_at=parse_datetime(item["recorded_at"]),
                    evaluated_ledger_head_hash=item["evaluated_ledger_head_hash"],
                    source_artifact=item["source_artifact"],
                    source_sha256=item["source_sha256"],
                    schema_version=item["schema_version"],
                )
                expected_inference = typed_inference.to_dict()
                expected_inference["verified_internal"] = True
                if canonical_hash(expected_inference) != canonical_hash(item):
                    raise ContractValidationError(
                        "family inference payload differs from its typed contract"
                    )
            for item in epochs:
                typed_epoch = ChallengeEpoch(
                    challenge_epoch_id=item["challenge_epoch_id"],
                    family_ids=tuple(item["family_ids"]),
                    trial_ids=tuple(item["trial_ids"]),
                    expected_input_sha256_by_trial=dict(
                        item["expected_input_sha256_by_trial"]
                    ),
                    challenge_period=item["challenge_period"],
                    panel_manifest_sha256=item["panel_manifest_sha256"],
                    alpha=item["alpha"],
                    authorized_by=item["authorized_by"],
                    authorization_artifact=item["authorization_artifact"],
                    authorization_sha256=item["authorization_sha256"],
                    authorized_at=parse_datetime(item["authorized_at"]),
                    schema_version=item["schema_version"],
                )
                if canonical_hash(typed_epoch.to_dict()) != canonical_hash(item):
                    raise ContractValidationError(
                        "challenge epoch payload differs from its typed contract"
                    )
            for item in accesses:
                typed_access = HoldoutAccess(
                    access_id=item["access_id"],
                    challenge_epoch_id=item["challenge_epoch_id"],
                    trial_ids=tuple(item["trial_ids"]),
                    input_sha256_by_trial=dict(item["input_sha256_by_trial"]),
                    accessed_at=parse_datetime(item["accessed_at"]),
                    consumer=item["consumer"],
                    purpose=item["purpose"],
                    schema_version=item["schema_version"],
                )
                if canonical_hash(typed_access.to_dict()) != canonical_hash(item):
                    raise ContractValidationError(
                        "challenge access payload differs from its typed contract"
                    )
        except (KeyError, TypeError, ValueError, ContractValidationError) as exc:
            raise EventStoreIntegrityError(
                "semantic replay found an invalid typed event payload"
            ) from exc
        for values, key in (
            (waves, "wave_id"),
            (families, "family_id"),
            (experiments, "experiment_id"),
            (runs, "attempt_id"),
            (results, "statistical_trial_id"),
            (epochs, "challenge_epoch_id"),
            (accesses, "access_id"),
            (reviews, "review_id"),
        ):
            ids = [item[key] for item in values]
            if len(ids) != len(set(ids)):
                raise EventStoreIntegrityError("semantic replay found duplicate {}".format(key))
        inference_ids = [(item["family_id"], item["track"]) for item in inferences]
        if len(inference_ids) != len(set(inference_ids)):
            raise EventStoreIntegrityError(
                "semantic replay found duplicate family inference"
            )
        family_index = {item["family_id"]: item for item in families}
        experiment_index = {item["experiment_id"]: item for item in experiments}
        wave_index = {item["wave_id"]: item for item in waves}
        wave_family_ids = [family_id for item in waves for family_id in item["family_ids"]]
        if len(wave_family_ids) != len(set(wave_family_ids)):
            raise EventStoreIntegrityError(
                "semantic replay found family membership in multiple waves"
            )
        trial_index = {
            item["statistical_trial_id"]: item
            for item in runs
            if item.get("statistical_trial_id")
        }
        trial_ids = [
            item["statistical_trial_id"]
            for item in runs
            if item.get("statistical_trial_id")
        ]
        if len(trial_ids) != len(set(trial_ids)):
            raise EventStoreIntegrityError(
                "semantic replay found duplicate statistical_trial_id"
            )
        epoch_index = {item["challenge_epoch_id"]: item for item in epochs}
        inference_index = {
            (item["family_id"], item["track"]): item for item in inferences
        }
        for index, epoch in enumerate(epochs):
            epoch_position = event_positions[
                "challenge-epoch:{}".format(epoch["challenge_epoch_id"])
            ]
            for other in epochs[index + 1 :]:
                conflict = _holdout_reuse_reason(epoch, other)
                if conflict is not None:
                    raise EventStoreIntegrityError(
                        "semantic replay found reused holdout: {}".format(conflict)
                    )
            for family_id, trial_id in zip(epoch["family_ids"], epoch["trial_ids"]):
                family = family_index.get(family_id)
                run = trial_index.get(trial_id)
                if (
                    family is None
                    or family["challenge_epoch_id"] != epoch["challenge_epoch_id"]
                    or run is None
                    or run["family_id"] != family_id
                    or run["run_class"] != ResearchRunClass.CHALLENGE_READ.value
                    or run["variant_id"] != family["primary_variant_id"]
                    or run["data_snapshot_sha256"]
                    != epoch["expected_input_sha256_by_trial"][trial_id]
                ):
                    raise EventStoreIntegrityError(
                        "semantic replay found a challenge entrant binding mismatch"
                    )
                if (
                    event_positions["family:{}".format(family_id)] >= epoch_position
                    or event_positions[
                        "attempt:{}".format(run["attempt_id"])
                    ]
                    >= epoch_position
                ):
                    raise EventStoreIntegrityError(
                        "semantic replay found a challenge prerequisite appended later"
                    )
                wave = wave_index.get(family["wave_id"])
                inference = (
                    inference_index.get((family_id, wave["track"]))
                    if wave is not None
                    else None
                )
                if (
                    wave is None
                    or inference is None
                    or inference.get("verified_internal") is not True
                    or parse_datetime(epoch["authorized_at"])
                    < parse_datetime(inference["recorded_at"])
                    or not all(
                        inference.get(name) is True
                        for name in (
                            "primary_variant_pass",
                            "economic_hurdle_pass",
                            "stress_scenario_pass",
                            "capacity_and_concentration_pass",
                            "effective_sample_pass",
                        )
                    )
                ):
                    raise EventStoreIntegrityError(
                        "semantic replay found an unauthorized challenge entrant"
                    )
                if event_positions[
                    "family-inference:{}:{}".format(
                        family_id, str(wave["track"]).lower()
                    )
                ] >= epoch_position:
                    raise EventStoreIntegrityError(
                        "semantic replay found challenge inference appended after authorization"
                    )
                wave_values = []
                for wave_family_id in wave["family_ids"]:
                    wave_inference = inference_index.get(
                        (wave_family_id, wave["track"])
                    )
                    if (
                        wave_inference is None
                        or wave_inference.get("verified_internal") is not True
                    ):
                        raise EventStoreIntegrityError(
                            "semantic replay found incomplete challenge wave inference"
                        )
                    if event_positions[
                        "family-inference:{}:{}".format(
                            wave_family_id, str(wave["track"]).lower()
                        )
                    ] >= epoch_position:
                        raise EventStoreIntegrityError(
                            "semantic replay found wave inference appended after authorization"
                        )
                    wave_values.append(
                        (
                            wave_family_id,
                            float(wave_inference["family_omnibus_p_value"]),
                        )
                    )
                wave_decisions = {
                    item["label"]: item["reject"]
                    for item in apply_wave_correction(
                        wave_values,
                        method=MultipleTestingMethod(wave["method"]),
                        alpha_or_q=float(wave["alpha_or_q"]),
                    )
                }
                if not wave_decisions.get(family_id, False):
                    raise EventStoreIntegrityError(
                        "semantic replay found a challenge entrant that failed wave correction"
                    )
        for family in families:
            wave = wave_index.get(family["wave_id"])
            family_position = event_positions[
                "family:{}".format(family["family_id"])
            ]
            if (
                wave is None
                or family["family_id"] not in wave["family_ids"]
                or event_positions["wave:{}".format(family["wave_id"])]
                >= family_position
            ):
                raise EventStoreIntegrityError("semantic replay found an orphan family")
            if set(family.get("parent_family_ids", [])) - set(family_index):
                raise EventStoreIntegrityError(
                    "semantic replay found an orphan parent family"
                )
            if any(
                event_positions["family:{}".format(parent_id)] >= family_position
                for parent_id in family.get("parent_family_ids", [])
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found a parent family appended after its child"
                )
        for experiment in experiments:
            family = family_index.get(experiment["family_id"])
            experiment_position = event_positions[
                "experiment:{}".format(experiment["experiment_id"])
            ]
            if (
                family is None
                or experiment["frozen_primary_metric"] != family["primary_metric"]
                or set(experiment["parent_experiment_ids"]) - set(experiment_index)
                or event_positions[
                    "family:{}".format(experiment["family_id"])
                ]
                >= experiment_position
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found invalid experiment lineage"
                )
            if any(
                event_positions["experiment:{}".format(parent_id)]
                >= experiment_position
                for parent_id in experiment["parent_experiment_ids"]
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found a parent experiment appended after its child"
                )
            if experiment["generated_after_results"]:
                parent_ids = set(experiment["parent_experiment_ids"])
                parent_trial_ids = {
                    item.get("statistical_trial_id")
                    for item in runs
                    if item["experiment_id"] in parent_ids
                    and item.get("statistical_trial_id")
                }
                closed_trial_ids = {
                    item["statistical_trial_id"] for item in results
                }
                result_by_trial_id = {
                    item["statistical_trial_id"]: item for item in results
                }
                qualifying_parent_results = parent_trial_ids.intersection(
                    closed_trial_ids
                )
                if not any(
                    event_positions["result:{}".format(trial_id)]
                    < experiment_position
                    and parse_datetime(experiment["registered_at"])
                    >= parse_datetime(result_by_trial_id[trial_id]["recorded_at"])
                    for trial_id in qualifying_parent_results
                ):
                    raise EventStoreIntegrityError(
                        "semantic replay found post-result lineage without a closed parent"
                    )
        for run in runs:
            if run["family_id"] not in family_index:
                raise EventStoreIntegrityError("semantic replay found an orphan attempt")
            experiment = experiment_index.get(run["experiment_id"])
            run_position = event_positions["attempt:{}".format(run["attempt_id"])]
            if (
                experiment is None
                or experiment["family_id"] != run["family_id"]
                or experiment["hypothesis_id"] != run["hypothesis_id"]
                or event_positions["family:{}".format(run["family_id"])]
                >= run_position
                or event_positions[
                    "experiment:{}".format(run["experiment_id"])
                ]
                >= run_position
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found an invalid experiment binding"
                )
            if run.get("primary_metric") and run["primary_metric"] != family_index[
                run["family_id"]
            ]["primary_metric"]:
                raise EventStoreIntegrityError(
                    "semantic replay found changed primary metric"
                )
            if run.get("parent_trial_id") and run["parent_trial_id"] not in trial_index:
                raise EventStoreIntegrityError("semantic replay found an orphan robustness record")
            if run.get("parent_trial_id") and event_positions[
                "attempt:{}".format(
                    trial_index[run["parent_trial_id"]]["attempt_id"]
                )
            ] >= run_position:
                raise EventStoreIntegrityError(
                    "semantic replay found a robustness parent appended later"
                )
        for result in results:
            if result["statistical_trial_id"] not in trial_index:
                raise EventStoreIntegrityError("semantic replay found an orphan result")
            run = trial_index[result["statistical_trial_id"]]
            family = family_index[run["family_id"]]
            result_position = event_positions[
                "result:{}".format(result["statistical_trial_id"])
            ]
            if (
                result["primary_metric"] != run.get("primary_metric")
                or result["minimum_effective_sample"]
                != run.get("effective_sample_floor")
                or parse_datetime(result["recorded_at"])
                < parse_datetime(run["occurred_at"])
                or event_positions["attempt:{}".format(run["attempt_id"])]
                >= result_position
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found a result binding mismatch"
                )
            if result["outcome"] == TrialOutcome.POSITIVE.value:
                metric_value = result.get("primary_metric_value")
                economic_pass = metric_value is not None and (
                    float(metric_value)
                    >= float(family["null_value"])
                    + float(family["economic_hurdle"])
                    if family["expected_direction"]
                    == ExpectedDirection.GREATER_THAN.value
                    else float(metric_value)
                    <= float(family["null_value"])
                    - float(family["economic_hurdle"])
                )
                if not economic_pass:
                    raise EventStoreIntegrityError(
                        "semantic replay found false positive direction"
                    )
        result_index = {item["statistical_trial_id"]: item for item in results}
        for inference in inferences:
            family = family_index.get(inference["family_id"])
            inference_position = event_positions[
                "family-inference:{}:{}".format(
                    inference["family_id"], str(inference["track"]).lower()
                )
            ]
            if family is None or not _family_inference_is_internally_valid(
                family, runs, result_index, inference
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found invalid family inference"
                )
            if (
                event_positions[
                    "family:{}".format(inference["family_id"])
                ]
                >= inference_position
                or any(
                    event_positions["result:{}".format(trial_id)]
                    >= inference_position
                    for trial_id in inference["included_trial_ids"]
                )
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found family inference before its evidence"
                )
        for family_id, family in family_index.items():
            count = sum(
                int(item.get("statistical_trial_delta", 0))
                for item in runs
                if item["family_id"] == family_id
            )
            if count > int(family["maximum_trial_units"]):
                raise EventStoreIntegrityError("semantic replay found an exceeded family budget")
            selection_count = sum(
                int(item.get("selection_trial_units", 0))
                for item in runs
                if item["family_id"] == family_id
            )
            if selection_count > int(family["selection_trial_budget"]):
                raise EventStoreIntegrityError(
                    "semantic replay found an exceeded selection budget"
                )
        accesses = self._payloads(records, self.HOLDOUT_EVENT)
        epoch_ids = [item["challenge_epoch_id"] for item in accesses]
        if len(epoch_ids) != len(set(epoch_ids)):
            raise EventStoreIntegrityError("semantic replay found reused challenge access")
        access_index = {item["challenge_epoch_id"]: item for item in accesses}
        for access in accesses:
            epoch = epoch_index.get(access["challenge_epoch_id"])
            access_position = event_positions[
                "challenge-access:{}".format(access["access_id"])
            ]
            if (
                epoch is None
                or set(access["trial_ids"]) != set(epoch["trial_ids"])
                or dict(access["input_sha256_by_trial"])
                != dict(epoch["expected_input_sha256_by_trial"])
                or parse_datetime(access["accessed_at"])
                < parse_datetime(epoch["authorized_at"])
                or parse_datetime(access["accessed_at"])
                > next(
                    record.recorded_at
                    for record in records
                    if record.event_type == self.HOLDOUT_EVENT
                    and record.payload["access_id"] == access["access_id"]
                )
                or event_positions[
                    "challenge-epoch:{}".format(access["challenge_epoch_id"])
                ]
                >= access_position
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found invalid challenge access"
                )
        for result in results:
            run = trial_index[result["statistical_trial_id"]]
            if run["run_class"] == ResearchRunClass.CHALLENGE_READ.value:
                family = family_index[run["family_id"]]
                access = access_index.get(family["challenge_epoch_id"])
                if (
                    access is None
                    or parse_datetime(result["recorded_at"])
                    < parse_datetime(access["accessed_at"])
                    or event_positions[
                        "challenge-access:{}".format(access["access_id"])
                    ]
                    >= event_positions[
                        "result:{}".format(result["statistical_trial_id"])
                    ]
                ):
                    raise EventStoreIntegrityError(
                        "semantic replay found challenge result before access"
                    )
        reviewed_family_ids = [item["family_id"] for item in reviews]
        if len(reviewed_family_ids) != len(set(reviewed_family_ids)):
            raise EventStoreIntegrityError("semantic replay found duplicate family review")
        for review in reviews:
            try:
                typed_review = IndependentResearchReview(
                    review_id=review["review_id"],
                    family_id=review["family_id"],
                    reviewer=review["reviewer"],
                    independent_of_research_authors=review[
                        "independent_of_research_authors"
                    ],
                    reviewed_ledger_head_hash=review["reviewed_ledger_head_hash"],
                    point_in_time_integrity=review["point_in_time_integrity"],
                    deterministic_replay=review["deterministic_replay"],
                    benchmark_and_factor_model_pass=review[
                        "benchmark_and_factor_model_pass"
                    ],
                    artifact_integrity_pass=review["artifact_integrity_pass"],
                    reviewed_at=parse_datetime(review["reviewed_at"]),
                    source_artifact=review["source_artifact"],
                    source_sha256=review["source_sha256"],
                    schema_version=review["schema_version"],
                )
                if canonical_hash(typed_review.to_dict()) != canonical_hash(review):
                    raise ContractValidationError(
                        "independent review payload differs from its typed contract"
                    )
                self._verify_review_artifact(review)
            except (KeyError, ContractValidationError) as exc:
                raise EventStoreIntegrityError(
                    "semantic replay found invalid independent review"
                ) from exc
            challenge_trial_ids = {
                item["statistical_trial_id"]
                for item in runs
                if item["family_id"] == review["family_id"]
                and item["run_class"] == ResearchRunClass.CHALLENGE_READ.value
            }
            review_position = event_positions[
                "review:{}".format(review["review_id"])
            ]
            if (
                review["family_id"] not in family_index
                or not challenge_trial_ids
                or challenge_trial_ids - set(result_index)
                or any(
                    event_positions["result:{}".format(trial_id)]
                    >= review_position
                    or parse_datetime(review["reviewed_at"])
                    < parse_datetime(result_index[trial_id]["recorded_at"])
                    for trial_id in challenge_trial_ids
                )
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found review before closed challenge evidence"
                )
        for record in records:
            if record.event_type == self.FAMILY_INFERENCE_EVENT and (
                record.payload["evaluated_ledger_head_hash"]
                != record.previous_event_hash
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found stale family inference binding"
                )
            if record.event_type == self.REVIEW_EVENT and (
                record.payload["reviewed_ledger_head_hash"]
                != record.previous_event_hash
            ):
                raise EventStoreIntegrityError(
                    "semantic replay found stale independent review binding"
                )

    def project(self) -> Dict[str, Any]:
        records = self.store.read_all()
        self._validate_semantics(records)
        waves = {item["wave_id"]: item for item in self._payloads(records, self.WAVE_EVENT)}
        families = {
            item["family_id"]: item for item in self._payloads(records, self.FAMILY_EVENT)
        }
        experiments = {
            item["experiment_id"]: item
            for item in self._payloads(records, self.EXPERIMENT_EVENT)
        }
        runs = self._payloads(records, self.RUN_EVENT)
        results = {
            item["statistical_trial_id"]: item
            for item in self._payloads(records, self.RESULT_EVENT)
        }
        inferences = {
            (item["family_id"], item["track"]): item
            for item in self._payloads(records, self.FAMILY_INFERENCE_EVENT)
        }
        inference_validity = {
            key: _family_inference_is_internally_valid(
                families[key[0]], runs, results, value
            )
            for key, value in inferences.items()
            if key[0] in families
        }
        epochs = {
            item["challenge_epoch_id"]: item
            for item in self._payloads(records, self.CHALLENGE_EPOCH_EVENT)
        }
        accesses = {
            item["challenge_epoch_id"]: item
            for item in self._payloads(records, self.HOLDOUT_EVENT)
        }
        reviews = {
            item["family_id"]: item
            for item in self._payloads(records, self.REVIEW_EVENT)
        }

        wave_rows: Dict[str, Dict[str, Any]] = {}
        wave_decisions: Dict[Tuple[str, str], bool] = {}
        for wave_id, wave in sorted(waves.items()):
            values: List[Tuple[str, float]] = []
            complete = True
            for family_id in wave["family_ids"]:
                inference = inferences.get((family_id, wave["track"]))
                if inference is None or not inference_validity.get(
                    (family_id, wave["track"]), False
                ):
                    complete = False
                    values.append((family_id, 1.0))
                else:
                    values.append((family_id, float(inference["family_omnibus_p_value"])))
            tests = apply_wave_correction(
                values,
                method=MultipleTestingMethod(wave["method"]),
                alpha_or_q=float(wave["alpha_or_q"]),
            )
            for test in tests:
                wave_decisions[(wave_id, test["label"])] = bool(test["reject"])
            wave_rows[wave_id] = {
                "track": wave["track"],
                "method": wave["method"],
                "alpha_or_q": float(wave["alpha_or_q"]),
                "owner_ratified": wave.get("owner_ratified") is True,
                "legacy_policy": wave.get("legacy_policy") is True,
                "family_count": len(wave["family_ids"]),
                "complete_family_inference": complete,
                "tests": tests,
            }

        family_rows = []
        for family_id, family in sorted(families.items()):
            family_runs = [item for item in runs if item["family_id"] == family_id]
            model_trials = [
                item for item in family_runs if item["run_class"] == ResearchRunClass.MODEL_TRIAL.value
            ]
            challenge_trials = [
                item
                for item in family_runs
                if item["run_class"] == ResearchRunClass.CHALLENGE_READ.value
            ]
            missing = sorted(
                item["statistical_trial_id"]
                for item in model_trials + challenge_trials
                if item["statistical_trial_id"] not in results
            )
            unresolved = sorted(
                item["statistical_trial_id"]
                for item in model_trials
                if item["legacy_accounting_quality"] != "COMPLETE"
            )
            track = waves[family["wave_id"]]["track"]
            inference = inferences.get((family_id, track))
            family_pass = bool(
                inference
                and inference_validity.get((family_id, track), False)
                and inference["primary_variant_pass"]
                and inference["economic_hurdle_pass"]
                and inference["stress_scenario_pass"]
                and inference["capacity_and_concentration_pass"]
                and inference["effective_sample_pass"]
            )
            wave_pass = wave_decisions.get((family["wave_id"], family_id), False)
            epoch = epochs.get(family["challenge_epoch_id"])
            accessed = family["challenge_epoch_id"] in accesses
            challenge_pass = False
            if epoch is not None and accessed:
                challenge_pairs = []
                for entrant_trial_id in epoch["trial_ids"]:
                    result = results.get(entrant_trial_id)
                    challenge_pairs.append(
                        (
                            entrant_trial_id,
                            float(result["p_value"])
                            if result is not None
                            and result.get("inference_eligible")
                            and result.get("p_value") is not None
                            else 1.0,
                        )
                    )
                challenge_tests = {
                    item["label"]: item
                    for item in holm_bonferroni(
                        challenge_pairs, alpha=float(epoch["alpha"])
                    )
                }
                family_challenge_trials = [
                    item
                    for item in challenge_trials
                    if item["statistical_trial_id"] in set(epoch["trial_ids"])
                ]
                if len(family_challenge_trials) == 1:
                    challenge_run = family_challenge_trials[0]
                    challenge_result = results.get(challenge_run["statistical_trial_id"])
                    metric_value = (
                        challenge_result.get("primary_metric_value")
                        if challenge_result is not None
                        else None
                    )
                    economic_pass = metric_value is not None and (
                        float(metric_value)
                        >= float(family["null_value"])
                        + float(family["economic_hurdle"])
                        if family["expected_direction"]
                        == ExpectedDirection.GREATER_THAN.value
                        else float(metric_value)
                        <= float(family["null_value"])
                        - float(family["economic_hurdle"])
                    )
                    challenge_pass = bool(
                        challenge_result
                        and challenge_result["outcome"] == TrialOutcome.POSITIVE.value
                        and challenge_result["inference_eligible"]
                        and challenge_tests[challenge_run["statistical_trial_id"]]["reject"]
                        and economic_pass
                        and challenge_result["stress_scenario_pass"]
                        and challenge_result["capacity_and_concentration_pass"]
                        and int(challenge_result["effective_sample_size"])
                        >= int(challenge_result["minimum_effective_sample"])
                    )
            family_experiments = [
                item for item in experiments.values() if item["family_id"] == family_id
            ]
            review = reviews.get(family_id)
            frozen_spec_integrity = bool(
                model_trials
                and all(
                    item["legacy_accounting_quality"] == "COMPLETE"
                    and item.get("preregistered") is True
                    and item.get("outcome_data_accessed") is False
                    and item.get("code_sha256")
                    and item.get("data_snapshot_sha256")
                    and item.get("evaluator_spec_sha256")
                    and int(item.get("effective_sample_floor") or 0) > 0
                    for item in model_trials + challenge_trials
                )
            )
            family_lineage_integrity = bool(
                family.get("owner_ratified") is True
                and waves[family["wave_id"]].get("owner_ratified") is True
                and family_experiments
                and all(item.get("owner_ratified") is True for item in family_experiments)
            )
            review_valid = bool(
                review
                and review.get("independent_of_research_authors") is True
            )
            research_gates = {
                "family_lineage_integrity": family_lineage_integrity,
                "frozen_spec_integrity": frozen_spec_integrity,
                "point_in_time_integrity": bool(
                    review_valid and review["point_in_time_integrity"]
                ),
                "deterministic_replay": bool(
                    review_valid and review["deterministic_replay"]
                ),
                "complete_trial_census": bool(
                    model_trials and not missing and not unresolved
                ),
                "trial_budget_compliant": bool(
                    len(model_trials) <= int(family["maximum_trial_units"])
                    and sum(
                        int(item.get("selection_trial_units", 0))
                        for item in family_runs
                    )
                    <= int(family["selection_trial_budget"])
                ),
                "family_inference_pass": family_pass,
                "exploratory_wave_fdr_pass": wave_pass,
                "locked_validation_economic_pass": bool(
                    inference
                    and inference_validity.get((family_id, track), False)
                    and inference["primary_variant_pass"]
                    and inference["economic_hurdle_pass"]
                ),
                "benchmark_and_factor_model": bool(
                    review_valid and review["benchmark_and_factor_model_pass"]
                ),
                "costs_capacity_and_concentration": bool(
                    inference
                    and inference_validity.get((family_id, track), False)
                    and inference["stress_scenario_pass"]
                    and inference["capacity_and_concentration_pass"]
                ),
                "challenge_epoch_integrity": bool(
                    epoch is not None
                    and accessed
                    and len(challenge_trials) == 1
                    and challenge_trials[0]["statistical_trial_id"] in epoch["trial_ids"]
                ),
                "challenge_confirmation_pass": challenge_pass,
                "independent_review": review_valid,
                "artifact_and_event_chain_integrity": bool(
                    review_valid and review["artifact_integrity_pass"]
                ),
            }
            blockers: List[str] = []
            if family.get("owner_ratified") is not True:
                blockers.append("FAMILY_MAPPING_NOT_OWNER_RATIFIED")
            if waves[family["wave_id"]].get("owner_ratified") is not True:
                blockers.append("WAVE_NOT_OWNER_RATIFIED")
            if waves[family["wave_id"]].get("legacy_policy") is True:
                blockers.append("LEGACY_POLICY_WAVE_NOT_DECISION_GRADE")
            blockers.append("AUTHENTICATED_OWNER_RATIFICATION_NOT_IMPLEMENTED")
            blockers.append("AUTHENTICATED_PREREGISTRATION_NOT_IMPLEMENTED")
            blockers.append("AUTHENTICATED_INDEPENDENT_REVIEW_NOT_IMPLEMENTED")
            if not model_trials:
                blockers.append("NO_STATISTICAL_MODEL_TRIAL")
            if missing:
                blockers.append("REGISTERED_TRIAL_RESULT_MISSING")
            if unresolved:
                blockers.append("LEGACY_TRIAL_IDENTITY_INCOMPLETE")
            if inference is None:
                blockers.append("FAMILY_INFERENCE_MISSING")
            elif not inference_validity.get((family_id, track), False):
                blockers.append("FAMILY_INFERENCE_NOT_INTERNALLY_VERIFIED")
            elif not family_pass:
                blockers.append("FAMILY_INFERENCE_FAILED")
            if family["within_family_method"] != MultipleTestingMethod.HOLM_BONFERRONI.value:
                blockers.append("VERIFIED_FAMILY_INFERENCE_ENGINE_NOT_IMPLEMENTED")
            if not wave_rows[family["wave_id"]]["complete_family_inference"]:
                blockers.append("WAVE_INFERENCE_INCOMPLETE")
            elif not wave_pass:
                blockers.append("WAVE_MULTIPLE_TESTING_GATE_FAILED")
            if epoch is None:
                blockers.append("CHALLENGE_EPOCH_NOT_REGISTERED")
            elif not accessed:
                blockers.append("CHALLENGE_EPOCH_UNTOUCHED")
            if not challenge_trials:
                blockers.append("CHALLENGE_TRIAL_NOT_REGISTERED")
            elif not challenge_pass:
                blockers.append("CHALLENGE_CONFIRMATION_MISSING_OR_FAILED")
            for gate_name, passed in sorted(research_gates.items()):
                if not passed:
                    blockers.append("RESEARCH_GATE_FAILED:{}".format(gate_name))
            family_rows.append(
                {
                    "family_id": family_id,
                    "wave_id": family["wave_id"],
                    "name": family["name"],
                    "hypothesis_ids": sorted(
                        {item["hypothesis_id"] for item in family_runs}
                    ),
                    "experiment_ids": sorted(
                        item["experiment_id"] for item in family_experiments
                    ),
                    "experiment_ids_by_hypothesis": {
                        hypothesis_id: sorted(
                            item["experiment_id"]
                            for item in family_experiments
                            if item["hypothesis_id"] == hypothesis_id
                        )
                        for hypothesis_id in sorted(
                            {item["hypothesis_id"] for item in family_experiments}
                        )
                    },
                    "attempt_count": len(family_runs),
                    "attempt_counts_by_class": {
                        item.value: sum(
                            1 for row in family_runs if row["run_class"] == item.value
                        )
                        for item in ResearchRunClass
                    },
                    "statistical_trial_count": len(model_trials),
                    "maximum_trial_units": int(family["maximum_trial_units"]),
                    "trial_units_remaining": int(family["maximum_trial_units"])
                    - len(model_trials),
                    "selection_trial_units": sum(
                        int(item.get("selection_trial_units", 0)) for item in family_runs
                    ),
                    "selection_trial_budget": int(family["selection_trial_budget"]),
                    "challenge_trial_count": len(challenge_trials),
                    "missing_result_trial_ids": missing,
                    "legacy_incomplete_trial_ids": unresolved,
                    "family_inference_pass": family_pass,
                    "wave_multiple_testing_pass": wave_pass,
                    "challenge_epoch_accessed": accessed,
                    "research_gates": research_gates,
                    "decision_grade_ready": not blockers,
                    "decision_grade_blockers": blockers,
                }
            )

        return {
            "schema_version": "caerus_alpha_lab_global_research_projection_v1",
            "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
            "canonical_event_store": str(self.store.path),
            "event_count": len(records),
            "event_chain_head": records[-1].event_hash if records else None,
            "wave_count": len(waves),
            "family_count": len(families),
            "experiment_count": len(experiments),
            "research_attempt_count": len(runs),
            "data_provenance_attempt_count": sum(
                1
                for item in runs
                if item["run_class"]
                in {
                    ResearchRunClass.DATA_GATE.value,
                    ResearchRunClass.COLLECTION.value,
                    ResearchRunClass.TRANSFORM.value,
                }
            ),
            "statistical_trial_count": sum(
                int(item.get("statistical_trial_delta", 0)) for item in runs
            ),
            "robustness_record_count": sum(
                1 for item in runs if item["run_class"] == ResearchRunClass.ROBUSTNESS.value
            ),
            "challenge_trial_count": sum(
                1
                for item in runs
                if item["run_class"] == ResearchRunClass.CHALLENGE_READ.value
            ),
            "challenge_epoch_access_count": len(accesses),
            "independent_review_count": len(reviews),
            "waves": wave_rows,
            "families": family_rows,
            "decision_grade_family_count": sum(
                1 for item in family_rows if item["decision_grade_ready"]
            ),
            "promotion_performed": False,
            "trading_behavior_changed": False,
        }


def deterministic_attempt_id(source_sha256: str) -> str:
    require_sha256(source_sha256, "source_sha256")
    return "ATTEMPT-{}".format(source_sha256[:16])


def deterministic_trial_id(family_id: str, ordinal: int) -> str:
    _require_pattern(family_id, _FAMILY_ID, "family_id")
    if not isinstance(ordinal, int) or not 1 <= ordinal <= 999:
        raise ContractValidationError("trial ordinal must be between 1 and 999")
    return "{}-T{:03d}".format(family_id, ordinal)


def deterministic_access_id(payload: Mapping[str, Any]) -> str:
    return "ACCESS-{}".format(canonical_hash(payload)[:16])
