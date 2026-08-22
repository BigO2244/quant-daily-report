"""Generic, bounded adapter contract for heterogeneous research techniques."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from projects.alpha_lab.factory.canonical import (
    canonical_hash,
    require_non_empty,
    require_sha256,
)
from projects.alpha_lab.factory.errors import ContractValidationError, ResearchBoundaryError
from projects.alpha_lab.factory.research_ledger import GlobalResearchLedger
from projects.alpha_lab.factory.store import EventRecord


_ALLOWED_MODULE_PREFIX = "projects.alpha_lab.evaluators."
_TRIAL_ID = re.compile(r"^FAM-\d{4}-\d{3}-T\d{3}$")
_FORBIDDEN_IMPORT_ROOTS = {
    "alpha_stack",
    "brokers",
    "core",
    "daily_quant_report",
    "deploy",
    "reconciliation",
    "scripts",
}
_FORBIDDEN_CALLS = {
    "cancel_order",
    "submit_market_order",
    "submit_option_limit_order",
    "submit_option_market_order",
}
_V2_SPEC_FIELDS = frozenset(
    {
        "schema_version",
        "hypothesis_id",
        "family_id",
        "experiment_id",
        "exploratory_wave_id",
        "challenge_epoch_id",
        "evaluator_id",
        "technique_family",
        "module",
        "callable_name",
        "maximum_variants",
        "frozen_variants",
        "search_census",
        "search_census_hash",
        "selection_trial_units",
        "primary_metric",
        "expected_direction",
        "null_value",
        "economic_hurdle",
        "inference_method",
        "inference_alpha_or_q",
        "resampling_unit",
        "effective_sample_floor",
        "evaluator_code_sha256",
        "data_contract_ids",
        "challenge_period",
        "spec_hash",
    }
)
_V2_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "hypothesis_id",
        "family_id",
        "experiment_id",
        "exploratory_wave_id",
        "challenge_epoch_id",
        "evaluator_id",
        "technique_family",
        "phase",
        "spec_hash",
        "input_packet_hash",
        "input_source_sha256",
        "registered_trial_ids",
        "registered_trial_contracts",
        "frozen_variant_contract_hash",
        "search_census_hash",
        "selection_trial_units",
        "challenge_access_receipt_hash",
        "boundary_attestation",
        "result",
        "promotion_performed",
        "trading_behavior_changed",
        "result_hash",
    }
)


class TechniqueFamily(str, Enum):
    CROSS_SECTIONAL = "CROSS_SECTIONAL"
    TIME_SERIES = "TIME_SERIES"
    EVENT_STUDY = "EVENT_STUDY"
    MACHINE_LEARNING = "MACHINE_LEARNING"
    PORTFOLIO_CONSTRUCTION = "PORTFOLIO_CONSTRUCTION"
    OPTIONS_INFORMATION = "OPTIONS_INFORMATION"
    EXECUTION_RESEARCH = "EXECUTION_RESEARCH"
    OTHER = "OTHER"


class EvaluationPhase(str, Enum):
    DISCOVERY = "DISCOVERY"
    CHALLENGE = "CHALLENGE"


@dataclass(frozen=True)
class FrozenVariantContract:
    """One outcome-bearing variant frozen before evaluator execution."""

    variant_id: str
    variant_definition_hash: str

    def __post_init__(self) -> None:
        require_non_empty(self.variant_id, "variant_id")
        require_sha256(self.variant_definition_hash, "variant_definition_hash")

    def to_dict(self) -> Dict[str, str]:
        return {
            "variant_id": self.variant_id,
            "variant_definition_hash": self.variant_definition_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FrozenVariantContract":
        if not isinstance(value, Mapping) or set(value) != {
            "variant_id",
            "variant_definition_hash",
        }:
            raise ContractValidationError(
                "frozen variant contract requires only variant_id and variant_definition_hash"
            )
        return cls(
            variant_id=value["variant_id"],
            variant_definition_hash=value["variant_definition_hash"],
        )


@dataclass(frozen=True)
class SearchCensusEntry:
    """One outcome-aware internal search unit, separate from registered trials."""

    search_id: str
    search_definition_hash: str

    def __post_init__(self) -> None:
        require_non_empty(self.search_id, "search_id")
        require_sha256(self.search_definition_hash, "search_definition_hash")

    def to_dict(self) -> Dict[str, str]:
        return {
            "search_id": self.search_id,
            "search_definition_hash": self.search_definition_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SearchCensusEntry":
        if not isinstance(value, Mapping) or set(value) != {
            "search_id",
            "search_definition_hash",
        }:
            raise ContractValidationError(
                "search census entry requires only search_id and search_definition_hash"
            )
        return cls(
            search_id=value["search_id"],
            search_definition_hash=value["search_definition_hash"],
        )


def _contract_sequence(
    value: Any, *, field_name: str, contract_type: Any
) -> Tuple[Any, ...]:
    if not isinstance(value, list):
        raise ContractValidationError("{} must be an ordered list".format(field_name))
    try:
        return tuple(contract_type.from_dict(item) for item in value)
    except (KeyError, TypeError) as exc:
        raise ContractValidationError("{} is invalid".format(field_name)) from exc


@dataclass(frozen=True)
class EvaluatorSpec:
    hypothesis_id: str
    evaluator_id: str
    technique_family: TechniqueFamily
    module: str
    callable_name: str
    maximum_variants: int
    primary_metric: str
    data_contract_ids: Tuple[str, ...]
    challenge_period: str
    spec_hash: str
    schema_version: str = "caerus_alpha_lab_evaluator_spec_v1"
    family_id: Optional[str] = None
    experiment_id: Optional[str] = None
    exploratory_wave_id: Optional[str] = None
    challenge_epoch_id: Optional[str] = None
    expected_direction: Optional[str] = None
    null_value: Optional[float] = None
    economic_hurdle: Optional[float] = None
    inference_method: Optional[str] = None
    inference_alpha_or_q: Optional[float] = None
    resampling_unit: Optional[str] = None
    effective_sample_floor: Optional[int] = None
    evaluator_code_sha256: Optional[str] = None
    frozen_variants: Tuple[FrozenVariantContract, ...] = ()
    search_census: Tuple[SearchCensusEntry, ...] = ()
    search_census_hash: Optional[str] = None
    selection_trial_units: Optional[int] = None

    def __post_init__(self) -> None:
        for name in (
            "hypothesis_id",
            "evaluator_id",
            "module",
            "callable_name",
            "primary_metric",
            "challenge_period",
        ):
            require_non_empty(getattr(self, name), name)
        if not self.module.startswith(_ALLOWED_MODULE_PREFIX):
            raise ResearchBoundaryError(
                "evaluator module must live below projects.alpha_lab.evaluators"
            )
        if (
            isinstance(self.maximum_variants, bool)
            or not isinstance(self.maximum_variants, int)
            or self.maximum_variants < 1
        ):
            raise ContractValidationError("maximum_variants must be positive")
        if not self.data_contract_ids:
            raise ContractValidationError("data_contract_ids cannot be empty")
        if self.schema_version == "caerus_alpha_lab_evaluator_spec_v2":
            for name in (
                "family_id",
                "experiment_id",
                "exploratory_wave_id",
                "challenge_epoch_id",
                "expected_direction",
                "inference_method",
                "resampling_unit",
                "evaluator_code_sha256",
            ):
                require_non_empty(getattr(self, name), name)
            if (
                isinstance(self.null_value, bool)
                or not isinstance(self.null_value, (float, int))
            ):
                raise ContractValidationError("v2 evaluator requires numeric null_value")
            if (
                isinstance(self.economic_hurdle, bool)
                or not isinstance(self.economic_hurdle, (float, int))
                or float(self.economic_hurdle) < 0.0
            ):
                raise ContractValidationError("v2 evaluator requires economic_hurdle")
            if self.expected_direction not in {"GREATER_THAN", "LESS_THAN"}:
                raise ContractValidationError("v2 evaluator expected_direction is invalid")
            if (
                isinstance(self.inference_alpha_or_q, bool)
                or not isinstance(self.inference_alpha_or_q, (float, int))
                or not (0.0 < float(self.inference_alpha_or_q) < 1.0)
            ):
                raise ContractValidationError(
                    "v2 evaluator requires inference_alpha_or_q between zero and one"
                )
            if (
                isinstance(self.effective_sample_floor, bool)
                or not isinstance(self.effective_sample_floor, int)
                or self.effective_sample_floor < 1
            ):
                raise ContractValidationError(
                    "v2 evaluator requires a positive effective_sample_floor"
                )
            if not isinstance(self.evaluator_code_sha256, str) or len(
                self.evaluator_code_sha256
            ) != 64:
                raise ContractValidationError(
                    "v2 evaluator requires evaluator_code_sha256"
                )
            require_sha256(self.evaluator_code_sha256, "evaluator_code_sha256")
            if not self.frozen_variants:
                raise ContractValidationError(
                    "v2 evaluator requires an ordered frozen_variants contract"
                )
            if not all(
                isinstance(item, FrozenVariantContract)
                for item in self.frozen_variants
            ):
                raise ContractValidationError("frozen_variants contract is invalid")
            variant_ids = [item.variant_id for item in self.frozen_variants]
            if len(variant_ids) != len(set(variant_ids)):
                raise ContractValidationError("frozen variant IDs must be unique")
            variant_hashes = [
                item.variant_definition_hash for item in self.frozen_variants
            ]
            if len(variant_hashes) != len(set(variant_hashes)):
                raise ContractValidationError(
                    "frozen variant definition hashes must be unique"
                )
            if len(self.frozen_variants) != self.maximum_variants:
                raise ContractValidationError(
                    "maximum_variants must equal the frozen variant census"
                )
            if not all(
                isinstance(item, SearchCensusEntry) for item in self.search_census
            ):
                raise ContractValidationError("search_census contract is invalid")
            search_ids = [item.search_id for item in self.search_census]
            if len(search_ids) != len(set(search_ids)):
                raise ContractValidationError("search census IDs must be unique")
            search_hashes = [
                item.search_definition_hash for item in self.search_census
            ]
            if len(search_hashes) != len(set(search_hashes)):
                raise ContractValidationError(
                    "search census definition hashes must be unique"
                )
            require_sha256(self.search_census_hash, "search_census_hash")
            if canonical_hash(self.search_census_dicts) != self.search_census_hash:
                raise ContractValidationError("search_census_hash mismatch")
            if (
                isinstance(self.selection_trial_units, bool)
                or not isinstance(self.selection_trial_units, int)
                or self.selection_trial_units != len(self.search_census)
            ):
                raise ContractValidationError(
                    "selection_trial_units must equal the frozen search census size"
                )
        elif self.schema_version != "caerus_alpha_lab_evaluator_spec_v1":
            raise ContractValidationError("unsupported evaluator spec schema")
        unsigned = self.unsigned_dict()
        if canonical_hash(unsigned) != self.spec_hash:
            raise ContractValidationError("evaluator spec_hash mismatch")

    def unsigned_dict(self) -> Dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "hypothesis_id": self.hypothesis_id,
            "evaluator_id": self.evaluator_id,
            "technique_family": self.technique_family.value,
            "module": self.module,
            "callable_name": self.callable_name,
            "maximum_variants": self.maximum_variants,
            "primary_metric": self.primary_metric,
            "data_contract_ids": self.data_contract_ids,
            "challenge_period": self.challenge_period,
        }
        if self.schema_version == "caerus_alpha_lab_evaluator_spec_v2":
            result.update(
                {
                    "family_id": self.family_id,
                    "experiment_id": self.experiment_id,
                    "exploratory_wave_id": self.exploratory_wave_id,
                    "challenge_epoch_id": self.challenge_epoch_id,
                    "expected_direction": self.expected_direction,
                    "null_value": self.null_value,
                    "economic_hurdle": self.economic_hurdle,
                    "inference_method": self.inference_method,
                    "inference_alpha_or_q": self.inference_alpha_or_q,
                    "resampling_unit": self.resampling_unit,
                    "effective_sample_floor": self.effective_sample_floor,
                    "evaluator_code_sha256": self.evaluator_code_sha256,
                    "frozen_variants": self.frozen_variant_dicts,
                    "search_census": self.search_census_dicts,
                    "search_census_hash": self.search_census_hash,
                    "selection_trial_units": self.selection_trial_units,
                }
            )
        return result

    @property
    def frozen_variant_dicts(self) -> list[Dict[str, str]]:
        return [item.to_dict() for item in self.frozen_variants]

    @property
    def search_census_dicts(self) -> list[Dict[str, str]]:
        return [item.to_dict() for item in self.search_census]

    def to_dict(self) -> Dict[str, Any]:
        result = self.unsigned_dict()
        result["spec_hash"] = self.spec_hash
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluatorSpec":
        schema_version = value.get(
            "schema_version", "caerus_alpha_lab_evaluator_spec_v1"
        )
        if schema_version == "caerus_alpha_lab_evaluator_spec_v2":
            missing = sorted(_V2_SPEC_FIELDS - set(value))
            unexpected = sorted(set(value) - _V2_SPEC_FIELDS)
            if missing or unexpected:
                details = []
                if missing:
                    details.append("missing={}".format(",".join(missing)))
                if unexpected:
                    details.append("unexpected={}".format(",".join(unexpected)))
                raise ContractValidationError(
                    "v2 evaluator spec fields are not exact: {}".format(
                        ";".join(details)
                    )
                )
        return cls(
            hypothesis_id=value["hypothesis_id"],
            evaluator_id=value["evaluator_id"],
            technique_family=TechniqueFamily(value["technique_family"]),
            module=value["module"],
            callable_name=value["callable_name"],
            maximum_variants=(
                value["maximum_variants"]
                if schema_version == "caerus_alpha_lab_evaluator_spec_v2"
                else int(value["maximum_variants"])
            ),
            primary_metric=value["primary_metric"],
            data_contract_ids=tuple(value["data_contract_ids"]),
            challenge_period=value["challenge_period"],
            spec_hash=value["spec_hash"],
            schema_version=schema_version,
            family_id=value.get("family_id"),
            experiment_id=value.get("experiment_id"),
            exploratory_wave_id=value.get("exploratory_wave_id"),
            challenge_epoch_id=value.get("challenge_epoch_id"),
            expected_direction=value.get("expected_direction"),
            null_value=value.get("null_value"),
            economic_hurdle=value.get("economic_hurdle"),
            inference_method=value.get("inference_method"),
            inference_alpha_or_q=value.get("inference_alpha_or_q"),
            resampling_unit=value.get("resampling_unit"),
            effective_sample_floor=value.get("effective_sample_floor"),
            evaluator_code_sha256=value.get("evaluator_code_sha256"),
            frozen_variants=_contract_sequence(
                value.get("frozen_variants", []),
                field_name="frozen_variants",
                contract_type=FrozenVariantContract,
            ),
            search_census=_contract_sequence(
                value.get("search_census", []),
                field_name="search_census",
                contract_type=SearchCensusEntry,
            ),
            search_census_hash=value.get("search_census_hash"),
            selection_trial_units=value.get("selection_trial_units"),
        )


def inspect_evaluator_boundary(source_path: Path) -> Dict[str, Any]:
    """Statically reject direct production imports and order-submission calls."""

    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                    findings.append("forbidden_import:{}".format(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                findings.append("forbidden_import:{}".format(node.module))
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in _FORBIDDEN_CALLS:
                findings.append("forbidden_call:{}".format(name))
    return {
        "schema_version": "caerus_alpha_lab_evaluator_boundary_v1",
        "source_path": str(source_path),
        "status": "PASS" if not findings else "FAIL",
        "findings": sorted(set(findings)),
        "source_sha256": __import__("hashlib").sha256(source_path.read_bytes()).hexdigest(),
    }


def validate_evaluator_output(
    *,
    spec: EvaluatorSpec,
    raw: Mapping[str, Any],
    phase: EvaluationPhase,
    registered_trial_ids: Tuple[str, ...],
) -> list[Dict[str, str]]:
    """Validate the shared v2 semantic contract for execution and recovery."""

    variant_count = raw.get("variant_count")
    if (
        isinstance(variant_count, bool)
        or not isinstance(variant_count, int)
        or variant_count < 1
    ):
        raise ContractValidationError("evaluator result requires positive variant_count")
    if variant_count != len(spec.frozen_variants):
        raise ContractValidationError(
            "evaluator variant_count differs from the frozen variant census"
        )
    if len(registered_trial_ids) != variant_count:
        raise ContractValidationError(
            "every evaluator variant must map to one registered family trial"
        )
    if phase is EvaluationPhase.CHALLENGE and variant_count != 1:
        raise ContractValidationError("challenge may evaluate one frozen champion only")
    variants = raw.get("variants")
    if not isinstance(variants, list) or len(variants) != variant_count:
        raise ContractValidationError("evaluator must return one result row per variant")
    actual_variant_contracts = []
    for position, (variant, frozen_variant) in enumerate(
        zip(variants, spec.frozen_variants), start=1
    ):
        if not isinstance(variant, Mapping):
            raise ContractValidationError("each evaluator variant must be an object")
        actual_contract = {
            "variant_id": variant.get("variant_id"),
            "variant_definition_hash": variant.get("variant_definition_hash"),
        }
        if actual_contract != frozen_variant.to_dict():
            raise ContractValidationError(
                "evaluator variant {} differs from the frozen ordered contract".format(
                    position
                )
            )
        actual_variant_contracts.append(actual_contract)
    raw_search_census = _contract_sequence(
        raw.get("search_census"),
        field_name="evaluator search_census",
        contract_type=SearchCensusEntry,
    )
    raw_search_census_dicts = [item.to_dict() for item in raw_search_census]
    if (
        tuple(raw_search_census) != spec.search_census
        or raw.get("search_census_hash") != spec.search_census_hash
        or canonical_hash(raw_search_census_dicts) != spec.search_census_hash
    ):
        raise ContractValidationError(
            "evaluator search census differs from the frozen v2 contract"
        )
    raw_selection_units = raw.get("selection_trial_units")
    if (
        isinstance(raw_selection_units, bool)
        or not isinstance(raw_selection_units, int)
        or raw_selection_units != len(raw_search_census)
        or raw_selection_units != spec.selection_trial_units
    ):
        raise ContractValidationError(
            "evaluator selection_trial_units differ from the mechanical search census"
        )
    allowed_verdicts = {
        "POSITIVE",
        "NEGATIVE",
        "INCONCLUSIVE",
        "NOT_EVALUABLE",
        "FAILED",
        "ABORTED",
    }
    for variant in variants:
        if variant.get("evidence_verdict") not in allowed_verdicts:
            raise ContractValidationError("each evaluator variant requires evidence_verdict")
        if not isinstance(variant.get("inference_eligible"), bool):
            raise ContractValidationError("each evaluator variant requires inference_eligible")
        reasons = variant.get("ineligibility_reasons")
        if not isinstance(reasons, list):
            raise ContractValidationError("each evaluator variant requires ineligibility_reasons")
        p_value = variant.get("p_value")
        if p_value is not None and not (
            isinstance(p_value, (float, int)) and 0.0 <= float(p_value) <= 1.0
        ):
            raise ContractValidationError("variant p_value must be a probability")
        if variant["inference_eligible"] and p_value is None:
            raise ContractValidationError("inference-eligible variant requires p_value")
        metric_value = variant.get("primary_metric_value")
        if variant.get("evidence_verdict") == "POSITIVE":
            if not isinstance(metric_value, (float, int)):
                raise ContractValidationError(
                    "positive evaluator variant requires primary_metric_value"
                )
            economic_pass = (
                float(metric_value)
                >= float(spec.null_value) + float(spec.economic_hurdle)
                if spec.expected_direction == "GREATER_THAN"
                else float(metric_value)
                <= float(spec.null_value) - float(spec.economic_hurdle)
            )
            if not economic_pass:
                raise ContractValidationError(
                    "positive evaluator variant does not clear the frozen economic hurdle"
                )
        for gate_name in (
            "stress_scenario_pass",
            "capacity_and_concentration_pass",
        ):
            if not isinstance(variant.get(gate_name), bool):
                raise ContractValidationError(
                    "each evaluator variant requires {}".format(gate_name)
                )
        effective_sample_size = variant.get("effective_sample_size")
        if (
            isinstance(effective_sample_size, bool)
            or not isinstance(effective_sample_size, int)
            or effective_sample_size < 0
        ):
            raise ContractValidationError(
                "each evaluator variant requires non-negative effective_sample_size"
            )
    if raw.get("primary_metric_name") != spec.primary_metric:
        raise ContractValidationError("evaluator primary metric changed from frozen spec")
    if raw.get("orders_submitted") is not False:
        raise ResearchBoundaryError("evaluator must attest that no orders were submitted")
    return actual_variant_contracts


def validate_evaluator_result_envelope(
    *, spec: EvaluatorSpec, envelope: Mapping[str, Any]
) -> EvaluationPhase:
    """Revalidate a finalized v2 result without rerunning outcome code."""

    if set(envelope) != _V2_RESULT_FIELDS:
        raise ContractValidationError("evaluator result envelope fields are not exact")
    unsigned = dict(envelope)
    supplied_hash = unsigned.pop("result_hash")
    if supplied_hash != canonical_hash(unsigned):
        raise ContractValidationError("evaluator result_hash is invalid")
    expected = {
        "schema_version": "caerus_alpha_lab_evaluator_result_v2",
        "hypothesis_id": spec.hypothesis_id,
        "family_id": spec.family_id,
        "experiment_id": spec.experiment_id,
        "exploratory_wave_id": spec.exploratory_wave_id,
        "challenge_epoch_id": spec.challenge_epoch_id,
        "evaluator_id": spec.evaluator_id,
        "technique_family": spec.technique_family.value,
        "spec_hash": spec.spec_hash,
        "frozen_variant_contract_hash": canonical_hash(
            spec.frozen_variant_dicts
        ),
        "search_census_hash": spec.search_census_hash,
        "selection_trial_units": spec.selection_trial_units,
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }
    if any(envelope.get(key) != value for key, value in expected.items()):
        raise ContractValidationError("evaluator result differs from the frozen spec")
    require_sha256(envelope.get("input_packet_hash"), "input_packet_hash")
    require_sha256(envelope.get("input_source_sha256"), "input_source_sha256")
    try:
        phase = EvaluationPhase(str(envelope["phase"]))
        trial_ids = tuple(str(item) for item in envelope["registered_trial_ids"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("evaluator result phase or trials are invalid") from exc
    if (
        not trial_ids
        or len(trial_ids) != len(set(trial_ids))
        or any(
            _TRIAL_ID.fullmatch(item) is None
            or not item.startswith(str(spec.family_id) + "-T")
            for item in trial_ids
        )
    ):
        raise ContractValidationError("evaluator result trial IDs are invalid")
    raw = envelope.get("result")
    if not isinstance(raw, Mapping):
        raise ContractValidationError("evaluator result payload is invalid")
    contracts = validate_evaluator_output(
        spec=spec,
        raw=raw,
        phase=phase,
        registered_trial_ids=trial_ids,
    )
    expected_trial_contracts = [
        {"statistical_trial_id": trial_id, **contract}
        for trial_id, contract in zip(trial_ids, contracts)
    ]
    if envelope.get("registered_trial_contracts") != expected_trial_contracts:
        raise ContractValidationError(
            "evaluator result trial contracts are not frozen and ordered"
        )
    boundary = envelope.get("boundary_attestation")
    if (
        not isinstance(boundary, Mapping)
        or boundary.get("schema_version")
        != "caerus_alpha_lab_evaluator_boundary_v1"
        or boundary.get("status") != "PASS"
        or boundary.get("findings") != []
        or boundary.get("source_sha256") != spec.evaluator_code_sha256
        or not isinstance(boundary.get("source_path"), str)
        or not boundary.get("source_path")
    ):
        raise ContractValidationError(
            "evaluator boundary attestation is invalid or changed"
        )
    receipt_hash = envelope.get("challenge_access_receipt_hash")
    if phase is EvaluationPhase.CHALLENGE:
        require_sha256(receipt_hash, "challenge_access_receipt_hash")
    else:
        if receipt_hash is not None:
            raise ContractValidationError(
                "discovery result cannot reference challenge access"
            )
        if envelope["input_packet_hash"] != envelope["input_source_sha256"]:
            raise ContractValidationError(
                "discovery input packet hash differs from its registered input source"
            )
    return phase


def run_evaluator(
    *,
    spec: EvaluatorSpec,
    input_packet: Mapping[str, Any],
    phase: EvaluationPhase,
    registered_trial_ids: Tuple[str, ...],
    challenge_access_receipt: Optional[EventRecord] = None,
    challenge_ledger: Optional[GlobalResearchLedger] = None,
    challenge_input_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a frozen evaluator and normalize its result without changing lifecycle state."""

    if spec.schema_version != "caerus_alpha_lab_evaluator_spec_v2":
        raise ContractValidationError(
            "v1 evaluator specs are historical evidence; outcome runs require v2"
        )
    if not registered_trial_ids or len(set(registered_trial_ids)) != len(
        registered_trial_ids
    ):
        raise ContractValidationError("evaluator requires unique registered trial IDs")
    if any(
        not isinstance(item, str)
        or _TRIAL_ID.fullmatch(item) is None
        or not item.startswith(str(spec.family_id) + "-T")
        for item in registered_trial_ids
    ):
        raise ContractValidationError("registered trials do not belong to the frozen family")
    if phase is EvaluationPhase.CHALLENGE:
        if (
            not isinstance(challenge_access_receipt, EventRecord)
            or not isinstance(challenge_ledger, GlobalResearchLedger)
        ):
            raise ContractValidationError(
                "challenge phase requires a canonical ledger access event"
            )
        receipt_event = challenge_access_receipt
        # Replays typed payloads and every global relationship before trusting
        # event membership. The CLI separately enforces the canonical GCP path.
        challenge_ledger.project()
        canonical_matches = [
            item
            for item in challenge_ledger.store.read_all()
            if item.event_hash == receipt_event.event_hash
            and item.event_id == receipt_event.event_id
        ]
        if len(canonical_matches) != 1 or canonical_matches[0] != receipt_event:
            raise ContractValidationError(
                "challenge access event is not present on the verified ledger chain"
            )
        if receipt_event.event_type != "challenge_access_started":
            raise ContractValidationError("challenge receipt has the wrong event type")
        receipt_trials = receipt_event.payload.get("trial_ids")
        if (
            not isinstance(receipt_trials, list)
            or list(registered_trial_ids) != [str(item) for item in receipt_trials]
        ):
            raise ContractValidationError("challenge receipt does not cover the trial")
        if (
            receipt_event.payload.get("challenge_epoch_id")
            != spec.challenge_epoch_id
        ):
            raise ContractValidationError("challenge receipt is for a different epoch")
        require_sha256(challenge_input_sha256, "challenge_input_sha256")
        expected_input_hashes = receipt_event.payload.get("input_sha256_by_trial")
        if not isinstance(expected_input_hashes, Mapping) or any(
            expected_input_hashes.get(trial_id) != challenge_input_sha256
            for trial_id in registered_trial_ids
        ):
            raise ContractValidationError(
                "challenge input hash differs from the consumed ledger event"
            )
    elif any(
        value is not None
        for value in (
            challenge_access_receipt,
            challenge_ledger,
            challenge_input_sha256,
        )
    ):
        raise ContractValidationError("discovery phase cannot carry challenge access")
    if input_packet.get("data_gate_status") != "READY_FOR_FROZEN_EVALUATOR":
        raise ContractValidationError("frozen evaluator requires a ready data gate")
    if input_packet.get("hypothesis_id") != spec.hypothesis_id:
        raise ContractValidationError("input hypothesis does not match evaluator spec")
    packet_assets = input_packet.get("assets")
    if not isinstance(packet_assets, Mapping):
        raise ContractValidationError("evaluator input requires certified assets")
    missing_contracts = sorted(
        set(spec.data_contract_ids) - {str(item) for item in packet_assets}
    )
    if missing_contracts:
        raise ContractValidationError(
            "evaluator input is missing frozen data contracts: {}".format(
                ",".join(missing_contracts)
            )
        )

    module = importlib.import_module(spec.module)
    source_path = Path(inspect.getsourcefile(module) or "").resolve()
    boundary = inspect_evaluator_boundary(source_path)
    if boundary["status"] != "PASS":
        raise ResearchBoundaryError("evaluator production boundary failed")
    if boundary["source_sha256"] != spec.evaluator_code_sha256:
        raise ContractValidationError("evaluator code differs from the frozen v2 spec")
    function = getattr(module, spec.callable_name, None)
    if not callable(function):
        raise ContractValidationError("evaluator callable is missing")
    raw = function(dict(input_packet), phase=phase.value)
    if not isinstance(raw, Mapping):
        raise ContractValidationError("evaluator must return a mapping")
    actual_variant_contracts = validate_evaluator_output(
        spec=spec,
        raw=raw,
        phase=phase,
        registered_trial_ids=registered_trial_ids,
    )
    result = {
        "schema_version": "caerus_alpha_lab_evaluator_result_v2",
        "hypothesis_id": spec.hypothesis_id,
        "family_id": spec.family_id,
        "experiment_id": spec.experiment_id,
        "exploratory_wave_id": spec.exploratory_wave_id,
        "challenge_epoch_id": spec.challenge_epoch_id,
        "evaluator_id": spec.evaluator_id,
        "technique_family": spec.technique_family.value,
        "phase": phase.value,
        "spec_hash": spec.spec_hash,
        "input_packet_hash": canonical_hash(input_packet),
        "input_source_sha256": (
            challenge_input_sha256
            if phase is EvaluationPhase.CHALLENGE
            else canonical_hash(input_packet)
        ),
        "registered_trial_ids": list(registered_trial_ids),
        "registered_trial_contracts": [
            {
                "statistical_trial_id": trial_id,
                **variant_contract,
            }
            for trial_id, variant_contract in zip(
                registered_trial_ids, actual_variant_contracts
            )
        ],
        "frozen_variant_contract_hash": canonical_hash(
            spec.frozen_variant_dicts
        ),
        "search_census_hash": spec.search_census_hash,
        "selection_trial_units": spec.selection_trial_units,
        "challenge_access_receipt_hash": (
            challenge_access_receipt.event_hash
            if challenge_access_receipt is not None
            else None
        ),
        "boundary_attestation": boundary,
        "result": dict(raw),
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }
    result["result_hash"] = canonical_hash(result)
    validate_evaluator_result_envelope(spec=spec, envelope=result)
    return result


def load_spec(path: Path) -> EvaluatorSpec:
    return EvaluatorSpec.from_dict(json.loads(path.read_text(encoding="utf-8")))
